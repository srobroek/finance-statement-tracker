from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .actual_pipeline import export_statement_for_actual
from .browser_exports import build_capture_from_export
from .browser_ingestion import export_browser_capture_for_actual
from .browser_sources import account_source, capture_account, load_browser_sources
from .statement_sources import load_statement_sources, require_active_statement_adapter


JOB_TYPES = frozenset({"STATEMENT_PDF", "BROWSER_CAPTURE", "BROWSER_EXPORT"})
ACTUAL_MODES = frozenset({"STAGE", "PREFLIGHT", "COMMIT"})


@dataclass(frozen=True, slots=True)
class IngestionJobResult:
    job_id: str
    job_type: str
    status: str
    actual_mode: str
    source_sha256: str
    manifest_path: str
    actual_result_path: str | None
    idempotent_replay: bool
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status,
            "actual_mode": self.actual_mode,
            "source_sha256": self.source_sha256,
            "manifest_path": self.manifest_path,
            "actual_result_path": self.actual_result_path,
            "idempotent_replay": self.idempotent_replay,
            "created_at": self.created_at,
        }


class IngestionJobRunner:
    def __init__(self, data_root: Path, repository_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.repository_root = repository_root.resolve()
        self.inbox = self.data_root / "inbox"
        self.jobs = self.data_root / "jobs"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.jobs.mkdir(parents=True, exist_ok=True)

    def _source(self, value: object) -> Path:
        relative = Path(str(value or "").strip())
        if not str(relative) or relative.is_absolute():
            raise ValueError("source_path must be relative to the ingestion inbox")
        candidate = (self.inbox / relative).resolve()
        if not candidate.is_relative_to(self.inbox.resolve()):
            raise ValueError("source_path escapes the ingestion inbox")
        if not candidate.is_file():
            raise ValueError(f"Ingestion source does not exist: {relative}")
        return candidate

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _identity(self, request: dict[str, Any], source_sha256: str) -> str:
        material = json.dumps(
            {"request": request, "source_sha256": source_sha256},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def _paths(self, job_id: str) -> tuple[Path, Path, Path, Path]:
        root = self.jobs / job_id
        return root, root / "request.json", root / "manifest.json", root / "actual-result.json"

    def _stage_statement(
        self, request: dict[str, Any], source: Path, manifest: Path
    ) -> dict[str, Any]:
        sources = load_statement_sources(self.repository_root / "config" / "statement-sources.json")
        adapter = require_active_statement_adapter(
            sources,
            str(request.get("card_code") or ""),
            str(request.get("adapter") or "").strip() or None,
        )
        run = export_statement_for_actual(
            source,
            self.repository_root / "config" / "actual-bootstrap.json",
            manifest,
            password_env=str(request.get("password_env") or "STATEMENT_PASSWORD"),
            adapter_code=adapter,
            rules_path=self.repository_root / "config" / "static-rules.seed.json",
        )
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["source_evidence"] = {
            "source_kind": str(request.get("source_kind") or "statement_pdf"),
            "source_message_id": str(request.get("source_message_id") or "") or None,
            "source_attachment_id": str(request.get("source_attachment_id") or "") or None,
            "source_filename": source.name,
            "document_sha256": self._sha256(source),
        }
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return run.to_dict()

    def _stage_browser_capture(self, source: Path, manifest: Path) -> dict[str, Any]:
        run = export_browser_capture_for_actual(
            source,
            self.repository_root / "config" / "actual-bootstrap.json",
            manifest,
            rules_path=self.repository_root / "config" / "static-rules.seed.json",
        )
        return run.to_dict()

    def _stage_browser_export(
        self, request: dict[str, Any], source: Path, job_root: Path, manifest: Path
    ) -> dict[str, Any]:
        sources = load_browser_sources(self.repository_root / "config" / "browser-sources.json")
        configured = account_source(sources, str(request.get("actual_account") or ""))
        provider = str(request.get("provider") or "").strip()
        data_id = str(request.get("data_id") or "").strip()
        if provider != str(configured.get("provider_id") or "") or data_id not in configured.get("data_ids", []):
            raise ValueError("Browser provider/data_id is not configured for the requested Actual account")
        capture = build_capture_from_export(
            provider,
            data_id,
            source,
            capture_account(configured),
            adapters_root=self.repository_root / "browser_adapters",
        )
        capture_path = job_root / "browser-capture.json"
        capture_path.write_text(json.dumps(capture, indent=2), encoding="utf-8")
        return self._stage_browser_capture(capture_path, manifest)

    def _actual(self, manifest: Path, result: Path, mode: str) -> dict[str, Any] | None:
        if mode == "STAGE":
            return None
        if mode == "COMMIT" and os.environ.get("ALLOW_ACTUAL_WRITES", "").casefold() != "true":
            raise ValueError("Actual writes are disabled; set ALLOW_ACTUAL_WRITES=true explicitly")
        command = [
            "node",
            str(self.repository_root / "integrations" / "actual" / "actualctl.mjs"),
            "import",
            "--input",
            str(manifest),
            "--result",
            str(result),
        ]
        if mode == "COMMIT":
            command.append("--commit")
        completed = subprocess.run(
            command,
            cwd=self.repository_root / "integrations" / "actual",
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or "Actual bridge failed")
        payload = json.loads(result.read_text(encoding="utf-8"))
        if mode == "COMMIT" and payload.get("status") != "committed":
            raise RuntimeError("Actual bridge did not report a committed import")
        if mode == "PREFLIGHT" and payload.get("status") not in {"dry-run", "rejected"}:
            raise RuntimeError("Actual bridge returned an unexpected preflight status")
        return payload

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        job_type = str(request.get("type") or "").strip().upper()
        actual_mode = str(request.get("actual_mode") or "STAGE").strip().upper()
        if job_type not in JOB_TYPES:
            raise ValueError(f"Unsupported ingestion job type: {job_type}")
        if actual_mode not in ACTUAL_MODES:
            raise ValueError(f"Unsupported Actual mode: {actual_mode}")
        normalized_request = dict(request)
        normalized_request["type"] = job_type
        normalized_request["actual_mode"] = actual_mode
        source = self._source(normalized_request.get("source_path"))
        source_sha256 = self._sha256(source)
        job_id = self._identity(normalized_request, source_sha256)
        job_root, request_path, manifest, actual_result = self._paths(job_id)
        result_path = job_root / "result.json"
        if result_path.exists():
            replay = json.loads(result_path.read_text(encoding="utf-8"))
            replay["idempotent_replay"] = True
            return replay
        job_root.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(normalized_request, indent=2), encoding="utf-8")

        if job_type == "STATEMENT_PDF":
            staged = self._stage_statement(normalized_request, source, manifest)
        elif job_type == "BROWSER_CAPTURE":
            staged = self._stage_browser_capture(source, manifest)
        else:
            staged = self._stage_browser_export(normalized_request, source, job_root, manifest)

        actual_payload = self._actual(manifest, actual_result, actual_mode)
        status = "STAGED" if actual_mode == "STAGE" else str(actual_payload.get("status")).upper()
        result = IngestionJobResult(
            job_id=job_id,
            job_type=job_type,
            status=status,
            actual_mode=actual_mode,
            source_sha256=source_sha256,
            manifest_path=str(manifest),
            actual_result_path=str(actual_result) if actual_payload is not None else None,
            idempotent_replay=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        ).to_dict()
        result["staging_status"] = staged.get("staging_status")
        result["review_count"] = staged.get("review_count", 0)
        result["envelope_count"] = len(staged.get("envelopes") or [])
        result["cashback_reconciliation_count"] = len(staged.get("cashback_reconciliation") or [])
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    def result(self, job_id: str) -> dict[str, Any]:
        if not job_id or any(character not in "0123456789abcdef" for character in job_id):
            raise ValueError("Invalid job id")
        path = self.jobs / job_id / "result.json"
        if not path.is_file():
            raise ValueError("Ingestion job was not found")
        return json.loads(path.read_text(encoding="utf-8"))
