from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .actual_notes import add_actual_document
from .actual_pipeline import export_statement_for_actual
from .browser_exports import build_capture_from_export
from .browser_ingestion import export_browser_capture_for_actual
from .browser_sources import account_source, capture_account, load_browser_sources
from .statement_sources import load_statement_sources, require_active_statement_source


JOB_TYPES = frozenset({"STATEMENT_PDF", "BROWSER_CAPTURE", "BROWSER_EXPORT"})
ACTUAL_MODES = frozenset({"STAGE", "PREFLIGHT", "COMMIT"})
AI_HANDOFF_SCHEMA_VERSION = 2
RESULT_SCHEMA_VERSION = 5
EVIDENCE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def compact_ai_handoff(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Deduplicate policy and transaction context without losing request identity."""
    policies: dict[str, dict[str, Any]] = {}
    variants: dict[str, dict[str, dict[str, Any]]] = {}
    pending_requests: list[tuple[str, str, str, list[str]]] = []
    request_keys: set[tuple[str, str]] = set()
    for request in requests:
        policy_id = str(request.get("policy_id") or "").strip()
        transaction = request.get("transaction")
        transaction_id = str(
            transaction.get("transaction_id") if isinstance(transaction, dict) else ""
        ).strip()
        if not policy_id or not transaction_id:
            raise ValueError("AI request requires policy_id and transaction.transaction_id")
        request_key = (transaction_id, policy_id)
        if request_key in request_keys:
            raise ValueError(
                "Duplicate AI request for transaction "
                f"{transaction_id} policy {policy_id}"
            )
        request_keys.add(request_key)
        policy = {
            "policy_version": request.get("policy_version"),
            "instruction": request.get("instruction"),
            "allowed_values": request.get("allowed_values") or {},
            "allowed_tags": request.get("allowed_tags") or [],
            "response_contract": request.get("response_contract") or {},
        }
        if policy_id in policies and policies[policy_id] != policy:
            raise ValueError(f"AI policy context changed within one handoff: {policy_id}")
        policies[policy_id] = policy
        canonical_transaction = json.dumps(
            transaction,
            sort_keys=True,
            separators=(",", ":"),
        )
        context_hash = hashlib.sha256(canonical_transaction.encode("utf-8")).hexdigest()
        variants.setdefault(transaction_id, {})[context_hash] = dict(transaction)
        pending_requests.append((
            transaction_id,
            policy_id,
            context_hash,
            list(request.get("allowed_fields") or []),
        ))

    transactions: dict[str, dict[str, Any]] = {}
    transaction_refs: dict[tuple[str, str], str] = {}
    for transaction_id, contexts in variants.items():
        multiple_contexts = len(contexts) > 1
        for context_hash, transaction in sorted(contexts.items()):
            reference = (
                f"{transaction_id}@{context_hash[:12]}"
                if multiple_contexts
                else transaction_id
            )
            if reference in transactions and transactions[reference] != transaction:
                raise ValueError(f"AI transaction context reference collision: {reference}")
            transactions[reference] = transaction
            transaction_refs[(transaction_id, context_hash)] = reference

    compact_requests = [
        {
            "transaction_id": transaction_id,
            "transaction_ref": transaction_refs[(transaction_id, context_hash)],
            "policy_id": policy_id,
            "allowed_fields": allowed_fields,
        }
        for transaction_id, policy_id, context_hash, allowed_fields in pending_requests
    ]
    return {
        "schema_version": AI_HANDOFF_SCHEMA_VERSION,
        "policies": policies,
        "transactions": transactions,
        "requests": compact_requests,
    }


def normalize_evidence_links(raw_links: Any) -> list[dict[str, Any]]:
    """Validate and canonicalize portable catalogue links for Actual notes."""
    if raw_links in (None, []):
        return []
    if not isinstance(raw_links, list) or any(
        not isinstance(link, dict) for link in raw_links
    ):
        raise ValueError("evidence_links must be a list of objects")
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for link in raw_links:
        transaction_id = str(link.get("transaction_id") or "").strip()
        evidence_id = str(link.get("evidence_id") or "").strip().casefold()
        relative_path = str(link.get("relative_path") or "").strip()
        if not transaction_id or not EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
            raise ValueError(
                "Every evidence link requires transaction_id and sha256 evidence_id"
            )
        path = PurePosixPath(relative_path)
        if (
            path.is_absolute()
            or not path.parts
            or path.parts[0] != "Finance Evidence"
            or any(part in {"", ".", ".."} for part in path.parts)
            or "|" in relative_path
            or any(character in relative_path for character in "\r\n\t")
        ):
            raise ValueError(
                "Evidence relative_path must be a safe Finance Evidence path"
            )
        identity = (transaction_id, evidence_id)
        if identity in identities:
            raise ValueError(
                f"Duplicate evidence link for transaction {transaction_id}: {evidence_id}"
            )
        identities.add(identity)
        item: dict[str, Any] = {
            "transaction_id": transaction_id,
            "evidence_id": evidence_id,
            "relative_path": path.as_posix(),
        }
        for field in ("document_type", "reference", "message_id"):
            value = str(link.get(field) or "").strip()
            if value:
                if len(value) > 500 or any(character in value for character in "\r\n\t"):
                    raise ValueError(f"Evidence {field} is unsafe or too long")
                item[field] = value
        normalized.append(item)
    return sorted(
        normalized,
        key=lambda item: (item["transaction_id"], item["evidence_id"]),
    )


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
    def __init__(
        self,
        data_root: Path,
        repository_root: Path,
        pipeline_revision: str | None = None,
    ) -> None:
        self.data_root = data_root.resolve()
        self.repository_root = repository_root.resolve()
        configured_revision = str(
            pipeline_revision or os.environ.get("FINANCE_PIPELINE_REVISION") or ""
        ).strip()
        self.pipeline_revision = configured_revision or self._repository_fingerprint()
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

    def _repository_fingerprint(self) -> str:
        """Hash deterministic worker inputs when no build revision is injected."""
        paths: set[Path] = set()
        for pattern in (
            "finance_tracker/*.py",
            "apps/actual-ingestion/*.py",
            "integrations/actual/*.mjs",
            "integrations/actual/package.json",
            "integrations/actual/package-lock.json",
            "config/*.json",
            "browser_adapters/**/*",
        ):
            paths.update(
                path
                for path in self.repository_root.glob(pattern)
                if path.is_file()
            )
        if not paths:
            raise ValueError("Cannot fingerprint an empty ingestion pipeline")
        digest = hashlib.sha256()
        for path in sorted(paths, key=lambda item: item.as_posix()):
            relative = path.relative_to(self.repository_root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        return f"content-sha256:{digest.hexdigest()}"

    def _identity(self, request: dict[str, Any], source_sha256: str) -> str:
        material = json.dumps(
            {
                "pipeline_revision": self.pipeline_revision,
                "request": request,
                "source_sha256": source_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def _paths(self, job_id: str) -> tuple[Path, Path, Path, Path]:
        root = self.jobs / job_id
        return root, root / "request.json", root / "manifest.json", root / "actual-result.json"

    @staticmethod
    def _upgrade_result(result: dict[str, Any], manifest: Path) -> dict[str, Any]:
        """Upgrade durable results without changing their idempotency identity."""
        upgraded = dict(result)
        manifest_payload: dict[str, Any] | None = None

        def read_manifest() -> dict[str, Any]:
            nonlocal manifest_payload
            if manifest_payload is None:
                manifest_payload = (
                    json.loads(manifest.read_text(encoding="utf-8"))
                    if manifest.is_file()
                    else {}
                )
            return manifest_payload

        handoff = upgraded.get("ai_handoff")
        if (
            not isinstance(handoff, dict)
            or handoff.get("schema_version") != AI_HANDOFF_SCHEMA_VERSION
        ):
            ai_request_count = int(upgraded.get("ai_request_count") or 0)
            ai_requests: list[dict[str, Any]] = []
            if manifest.is_file():
                raw_requests = read_manifest().get("ai_requests") or []
                if not isinstance(raw_requests, list) or any(
                    not isinstance(request, dict) for request in raw_requests
                ):
                    raise ValueError("Stored ingestion manifest has invalid ai_requests")
                ai_requests = raw_requests
            if ai_request_count and len(ai_requests) != ai_request_count:
                raise ValueError(
                    "Stored ingestion result cannot be upgraded safely: "
                    f"expected {ai_request_count} AI requests, found {len(ai_requests)}"
                )
            upgraded["ai_handoff"] = compact_ai_handoff(ai_requests)
        upgraded.setdefault("ai_handoff_complete", False)
        upgraded["evidence_link_count"] = len(read_manifest().get("evidence_links") or [])
        upgraded["result_schema_version"] = RESULT_SCHEMA_VERSION
        return upgraded

    @staticmethod
    def _attach_evidence_links(manifest: Path, links: list[dict[str, Any]]) -> int:
        if not links:
            return 0
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        records: dict[str, dict[str, Any]] = {}
        for envelope in payload.get("envelopes") or []:
            for record in envelope.get("records") or []:
                transaction_id = str(record.get("imported_id") or "").strip()
                if not transaction_id:
                    continue
                if transaction_id in records:
                    raise ValueError(
                        f"Manifest contains duplicate imported_id: {transaction_id}"
                    )
                records[transaction_id] = record
        transaction_rows = {
            str(row.get("transaction_id") or ""): row
            for row in payload.get("transactions") or []
            if isinstance(row, dict)
        }
        for link in links:
            transaction_id = link["transaction_id"]
            record = records.get(transaction_id)
            if record is None:
                raise ValueError(
                    f"Evidence link does not match a staged transaction: {transaction_id}"
                )
            notes = str(record.get("notes") or "")
            record["notes"] = add_actual_document(notes, link["relative_path"])
            transaction_row = transaction_rows.get(transaction_id)
            if transaction_row is not None:
                transaction_row["evidence_status"] = "LINKED"
                metadata = transaction_row.setdefault("metadata", {})
                metadata.setdefault("evidence_links", []).append(dict(link))
        payload["evidence_links"] = links
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return len(links)

    @staticmethod
    def _ai_handoff(
        request: dict[str, Any],
    ) -> tuple[
        list[dict[str, Any]],
        dict[tuple[str, str], dict[str, Any]],
        Any,
    ]:
        raw_responses = request.get("ai_responses") or []
        if not isinstance(raw_responses, list) or any(
            not isinstance(response, dict) for response in raw_responses
        ):
            raise ValueError("ai_responses must be a list of response objects")
        responses: dict[tuple[str, str], dict[str, Any]] = {}
        for response in raw_responses:
            transaction_id = str(response.get("transaction_id") or "").strip()
            policy_id = str(response.get("policy_id") or "").strip()
            proposals = response.get("proposals")
            if not transaction_id or not policy_id or not isinstance(proposals, list):
                raise ValueError(
                    "Every AI response requires transaction_id, policy_id, and a proposals list"
                )
            key = (transaction_id, policy_id)
            if key in responses:
                raise ValueError(
                    f"Duplicate AI response for transaction {transaction_id} policy {policy_id}"
                )
            responses[key] = {
                "proposals": proposals,
                "provider": str(response.get("provider") or "codex-scheduled-task"),
                "model": str(response.get("model") or "gpt-5.6-sol"),
            }

        collected: list[dict[str, Any]] = []

        def resolver(prompt: dict[str, Any]) -> dict[str, Any]:
            collected.append(prompt)
            transaction = prompt.get("transaction")
            transaction_id = str(
                transaction.get("transaction_id") if isinstance(transaction, dict) else ""
            ).strip()
            policy_id = str(prompt.get("policy_id") or "").strip()
            response = responses.pop((transaction_id, policy_id), None)
            if response is None:
                return {
                    "proposals": [],
                    "provider": "codex-scheduled-task",
                    "model": "pending",
                }
            return response

        return collected, responses, resolver

    def _stage_statement(
        self, request: dict[str, Any], source: Path, manifest: Path
    ) -> dict[str, Any]:
        sources = load_statement_sources(self.repository_root / "config" / "statement-sources.json")
        statement_source = require_active_statement_source(
            sources,
            str(request.get("card_code") or ""),
            str(request.get("adapter") or "").strip() or None,
        )
        requested_password_env = (
            str(request.get("password_env") or "").strip() or None
            if "password_env" in request
            else statement_source.password_env
        )
        if requested_password_env != statement_source.password_env:
            raise ValueError(
                "Statement password_env must match the configured source registry"
            )
        ai_requests, unused_ai_responses, ai_resolver = self._ai_handoff(request)
        run = export_statement_for_actual(
            source,
            self.repository_root / "config" / "actual-bootstrap.json",
            manifest,
            password_env=statement_source.password_env,
            adapter_code=statement_source.adapter,
            source_message_id=str(request.get("source_message_id") or "") or None,
            rules_path=self.repository_root / "config" / "static-rules.seed.json",
            ai_policies_path=self.repository_root / "config" / "ai-policies.json",
            ai_resolver=ai_resolver,
        )
        if unused_ai_responses:
            unused = ", ".join(
                f"{transaction_id}/{policy_id}"
                for transaction_id, policy_id in sorted(unused_ai_responses)
            )
            raise ValueError(f"AI responses did not match generated requests: {unused}")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["ai_requests"] = ai_requests
        payload["ai_response_count"] = len(request.get("ai_responses") or [])
        payload["source_evidence"] = {
            "source_kind": str(request.get("source_kind") or "statement_pdf"),
            "source_message_id": str(request.get("source_message_id") or "") or None,
            "source_attachment_id": str(request.get("source_attachment_id") or "") or None,
            "source_filename": str(request.get("source_filename") or source.name),
            "document_sha256": self._sha256(source),
        }
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        staged = run.to_dict()
        staged["ai_requests"] = ai_requests
        staged["ai_request_count"] = len(ai_requests)
        staged["ai_response_count"] = len(request.get("ai_responses") or [])
        return staged

    def _stage_browser_capture(
        self, request: dict[str, Any], source: Path, manifest: Path
    ) -> dict[str, Any]:
        ai_requests, unused_ai_responses, ai_resolver = self._ai_handoff(request)
        run = export_browser_capture_for_actual(
            source,
            self.repository_root / "config" / "actual-bootstrap.json",
            manifest,
            rules_path=self.repository_root / "config" / "static-rules.seed.json",
            ai_policies_path=self.repository_root / "config" / "ai-policies.json",
            ai_resolver=ai_resolver,
        )
        if unused_ai_responses:
            unused = ", ".join(
                f"{transaction_id}/{policy_id}"
                for transaction_id, policy_id in sorted(unused_ai_responses)
            )
            raise ValueError(f"AI responses did not match generated requests: {unused}")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["ai_requests"] = ai_requests
        payload["ai_response_count"] = len(request.get("ai_responses") or [])
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        staged = run.to_dict()
        staged["ai_requests"] = ai_requests
        staged["ai_request_count"] = len(ai_requests)
        staged["ai_response_count"] = len(request.get("ai_responses") or [])
        return staged

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
        return self._stage_browser_capture(request, capture_path, manifest)

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
        if "evidence_links" in normalized_request:
            normalized_request["evidence_links"] = normalize_evidence_links(
                normalized_request.get("evidence_links")
            )
        source = self._source(normalized_request.get("source_path"))
        source_sha256 = self._sha256(source)
        job_id = self._identity(normalized_request, source_sha256)
        job_root, request_path, manifest, actual_result = self._paths(job_id)
        result_path = job_root / "result.json"
        if result_path.exists():
            stored = json.loads(result_path.read_text(encoding="utf-8"))
            replay = self._upgrade_result(stored, manifest)
            if replay != stored:
                result_path.write_text(json.dumps(replay, indent=2), encoding="utf-8")
            replay["idempotent_replay"] = True
            return replay
        job_root.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(normalized_request, indent=2), encoding="utf-8")

        if job_type == "STATEMENT_PDF":
            staged = self._stage_statement(normalized_request, source, manifest)
        elif job_type == "BROWSER_CAPTURE":
            staged = self._stage_browser_capture(normalized_request, source, manifest)
        else:
            staged = self._stage_browser_export(normalized_request, source, job_root, manifest)

        evidence_link_count = self._attach_evidence_links(
            manifest,
            list(normalized_request.get("evidence_links") or []),
        )

        ai_request_count = int(staged.get("ai_request_count") or 0)
        ai_handoff_complete = bool(normalized_request.get("ai_handoff_complete"))
        ai_response_count = len(normalized_request.get("ai_responses") or [])
        if actual_mode != "STAGE":
            if ai_request_count and not ai_handoff_complete:
                raise ValueError(
                    f"Actual {actual_mode} requires ai_handoff_complete=true"
                )
            if staged.get("staging_status") not in {
                "READY_FOR_LEDGER_MATCH",
                "READY_FOR_APPROVAL",
            }:
                raise ValueError(
                    f"Actual {actual_mode} requires a review-free staging manifest"
                )
        if ai_handoff_complete and ai_response_count != ai_request_count:
            raise ValueError(
                "AI handoff marked complete but did not answer every request: "
                f"expected {ai_request_count}, received {ai_response_count}"
            )
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
        result["ai_request_count"] = ai_request_count
        result["ai_response_count"] = int(staged.get("ai_response_count") or 0)
        ai_trace = list(staged.get("ai_trace") or [])
        result["ai_trace_count"] = len(ai_trace)
        result["ai_accepted_count"] = sum(
            1 for trace in ai_trace if isinstance(trace, dict) and trace.get("accepted") is True
        )
        result["ai_rejected_count"] = sum(
            1 for trace in ai_trace if isinstance(trace, dict) and trace.get("accepted") is False
        )
        result["ai_handoff"] = compact_ai_handoff(list(staged.get("ai_requests") or []))
        result["ai_handoff_complete"] = ai_handoff_complete
        result["evidence_link_count"] = evidence_link_count
        result["pipeline_revision"] = self.pipeline_revision
        result["result_schema_version"] = RESULT_SCHEMA_VERSION
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    def result(self, job_id: str) -> dict[str, Any]:
        if not job_id or any(character not in "0123456789abcdef" for character in job_id):
            raise ValueError("Invalid job id")
        path = self.jobs / job_id / "result.json"
        if not path.is_file():
            raise ValueError("Ingestion job was not found")
        return json.loads(path.read_text(encoding="utf-8"))
