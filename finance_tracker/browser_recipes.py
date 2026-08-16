from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Mapping


_STEP = re.compile(r"^\s*(?:\d+\.\s*)?([A-Z_]+)(?:\s+(.*))?$")
_PARAM = re.compile(r"<([A-Za-z_][A-Za-z0-9_]*)>")
_SENSITIVE_PARAMS = {
    "cookie",
    "cvv",
    "otp",
    "password",
    "pin",
    "secret",
    "session",
    "token",
}


def default_browser_adapter_root() -> Path:
    return Path(__file__).resolve().parent.parent / "browser_adapters"


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Browser adapter metadata does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Browser adapter metadata must be an object: {path}")
    return payload


def load_grammar(root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else default_browser_adapter_root()
    grammar = _json(base / "recipe-grammar.json")
    actions = grammar.get("actions")
    if not isinstance(actions, dict) or not actions:
        raise ValueError("Browser recipe grammar requires actions")
    for acquire, action in grammar.get("acquire_terminal_map", {}).items():
        if action not in actions:
            raise ValueError(f"Acquire mode {acquire} refers to unknown action {action}")
        if not actions[action].get("terminal"):
            raise ValueError(f"Acquire mode {acquire} refers to non-terminal action {action}")
    return grammar


def list_providers(root: str | Path | None = None) -> list[str]:
    base = Path(root) if root else default_browser_adapter_root()
    return sorted(
        path.name
        for path in base.iterdir()
        if path.is_dir() and (path / "provider.json").is_file()
    )


def load_provider(provider: str, root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else default_browser_adapter_root()
    provider_root = base / provider
    metadata = _json(provider_root / "provider.json")
    if str(metadata.get("provider_id") or "") != provider:
        raise ValueError(f"Provider id does not match directory: {provider}")
    data_root = provider_root / "data"
    data_ids = sorted(
        path.name for path in data_root.iterdir() if path.is_dir() and (path / "data.json").is_file()
    ) if data_root.is_dir() else []
    return {**metadata, "data_ids": data_ids, "root": str(provider_root)}


def load_data(provider: str, data_id: str, root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else default_browser_adapter_root()
    data_root = base / provider / "data" / data_id
    metadata = _json(data_root / "data.json")
    if str(metadata.get("data_id") or "") != data_id:
        raise ValueError(f"Data id does not match directory: {provider}/{data_id}")
    return {**metadata, "root": str(data_root)}


def _fields(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    parsed: dict[str, str] = {}
    for token in next(csv.reader([raw], skipinitialspace=True)):
        if ":" not in token:
            raise ValueError(f"Malformed recipe field: {token.strip()}")
        key, value = token.split(":", 1)
        key = key.strip()
        if not key or not value.strip():
            raise ValueError(f"Malformed recipe field: {token.strip()}")
        if key in parsed:
            raise ValueError(f"Duplicate recipe field: {key}")
        parsed[key] = value.strip().strip('"')
    return parsed


def lint_recipe(
    recipe_path: str | Path,
    *,
    data_metadata: Mapping[str, Any] | None = None,
    grammar: Mapping[str, Any] | None = None,
) -> list[str]:
    path = Path(recipe_path)
    grammar = dict(grammar or load_grammar(path.parents[0] if path.name == "recipe-grammar.json" else None))
    actions = grammar["actions"]
    is_provider = path.name == "provider.recipe"
    location = "provider" if is_provider else "data"
    violations: list[str] = []
    seen_actions: list[str] = []
    note_run = 0
    declared = set(str(value) for value in (data_metadata or {}).get("inputs", []))
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _STEP.match(line)
        if not match:
            violations.append(f"line {line_number}: missing ACTION keyword")
            continue
        action, remainder = match.groups()
        spec = actions.get(action)
        if not spec:
            violations.append(f"line {line_number}: unknown action '{action}'")
            continue
        seen_actions.append(action)
        allowed_where = spec.get("where")
        if allowed_where not in {"both", location}:
            violations.append(f"line {line_number}: {action} only allowed in {allowed_where} recipes")
        if spec.get("free_text"):
            if not (remainder or "").strip():
                violations.append(f"line {line_number}: NOTE requires text")
            note_run += 1
            if note_run > int(grammar.get("max_consecutive_notes", 2)):
                violations.append(f"line {line_number}: too many consecutive NOTE steps")
            continue
        note_run = 0
        try:
            fields = _fields(remainder or "")
        except ValueError as error:
            violations.append(f"line {line_number}: {error}")
            continue
        required = set(spec.get("required", []))
        optional = set(spec.get("optional", []))
        for name in sorted(required - fields.keys()):
            violations.append(f"line {line_number}: missing required field '{name}'")
        for name in sorted(fields.keys() - required - optional):
            violations.append(f"line {line_number}: unknown field '{name}'")
        if not is_provider:
            for param in _PARAM.findall(" ".join(fields.values())):
                if param not in declared:
                    violations.append(f"line {line_number}: <{param}> is not declared in data inputs")
                if param.casefold() in _SENSITIVE_PARAMS:
                    violations.append(f"line {line_number}: sensitive parameter <{param}> is forbidden")
    if data_metadata:
        acquire = str(data_metadata.get("acquire") or "")
        terminal = grammar.get("acquire_terminal_map", {}).get(acquire)
        if not terminal:
            violations.append(f"unsupported acquire mode: {acquire}")
        elif terminal not in seen_actions:
            violations.append(f"acquire {acquire} requires a {terminal} step")
        parser = data_metadata.get("parser")
        if parser and data_metadata.get("kind") != "holdings" and not data_metadata.get("fingerprint"):
            violations.append("parser is configured but no fingerprint is declared")
    return violations


def validate_registry(root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else default_browser_adapter_root()
    grammar = load_grammar(base)
    providers = []
    violations: list[str] = []
    for provider_id in list_providers(base):
        provider = load_provider(provider_id, base)
        provider_recipe = Path(provider["root"]) / str(provider.get("recipe") or "provider.recipe")
        for error in lint_recipe(provider_recipe, grammar=grammar):
            violations.append(f"{provider_id}/provider.recipe: {error}")
        data_entries = []
        for data_id in provider["data_ids"]:
            data = load_data(provider_id, data_id, base)
            recipe = Path(data["root"]) / str(data.get("recipe") or "recipe")
            for error in lint_recipe(recipe, data_metadata=data, grammar=grammar):
                violations.append(f"{provider_id}/{data_id}: {error}")
            if provider.get("mobile_only") and data.get("acquire") != "upload":
                violations.append(f"{provider_id}/{data_id}: mobile-only provider must use upload")
            data_entries.append({
                "data_id": data_id,
                "kind": data.get("kind"),
                "acquire": data.get("acquire"),
                "parser": data.get("parser"),
                "verified": bool(data.get("verified", False)),
            })
        providers.append({
            "provider_id": provider_id,
            "display_name": provider.get("display_name"),
            "portal_url": provider.get("portal_url"),
            "mobile_only": bool(provider.get("mobile_only", False)),
            "data": data_entries,
        })
    return {"status": "ok" if not violations else "invalid", "providers": providers, "violations": violations}


def render_recipe(
    provider: str,
    data_id: str,
    parameters: Mapping[str, str],
    root: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root else default_browser_adapter_root()
    provider_metadata = load_provider(provider, base)
    data = load_data(provider, data_id, base)
    required = set(str(value) for value in data.get("inputs", []))
    supplied = set(parameters)
    missing = sorted(required - supplied)
    unknown = sorted(supplied - required)
    if missing or unknown:
        raise ValueError(f"Recipe parameters mismatch: missing={missing} unknown={unknown}")
    if any(name.casefold() in _SENSITIVE_PARAMS for name in supplied):
        raise ValueError("Credentials and session values are forbidden recipe parameters")
    if any("\n" in str(value) or "\r" in str(value) for value in parameters.values()):
        raise ValueError("Recipe parameter values must be single-line text")
    provider_recipe = (Path(provider_metadata["root"]) / str(provider_metadata.get("recipe") or "provider.recipe")).read_text(encoding="utf-8")
    data_recipe = (Path(data["root"]) / str(data.get("recipe") or "recipe")).read_text(encoding="utf-8")
    for name, value in parameters.items():
        data_recipe = data_recipe.replace(f"<{name}>", str(value))
    return {
        "provider": provider_metadata,
        "data": data,
        "provider_recipe": provider_recipe,
        "data_recipe": data_recipe,
    }
