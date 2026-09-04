from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Transaction
from .properties import load_property_registry
from .rules import RuleCondition, condition_matches


AI_WRITABLE_FIELDS = frozenset(
    {
        "vendor",
        "category",
        "subcategory",
        "owner",
        "property_code",
        "rental_unit",
        "is_subscription",
        "evidence_policy",
        "channel",
        "reward_bucket",
        "category_recommendation",
        "rule_recommendation",
    }
)
PROTECTED_FIELDS = frozenset(
    {
        "transaction_id",
        "transaction_at",
        "card",
        "account",
        "institution",
        "account_last4",
        "merchant_raw",
        "amount_aed",
        "amount_original",
        "source_direction",
        "currency",
        "source_type",
        "source_message_id",
        "transaction_type",
        "is_refund",
        "reconciliation_status",
    }
)


@dataclass(frozen=True, slots=True)
class AIPolicy:
    policy_id: str
    name: str
    priority: int
    instruction: str
    target_fields: tuple[str, ...]
    agent_profile: str = "LUNA_MAX"
    trigger_fields: tuple[str, ...] = ()
    conditions: tuple[RuleCondition, ...] = ()
    minimum_confidence: float = 0.82
    allowed_values: dict[str, tuple[Any, ...]] | None = None
    allowed_tags: tuple[str, ...] = ()
    enabled: bool = True
    version: int = 1


@dataclass(frozen=True, slots=True)
class AITrace:
    transaction_id: str
    policy_id: str
    field: str
    proposed_value: Any
    confidence: float
    accepted: bool
    reason: str
    rationale: str = ""
    source_refs: tuple[str, ...] = ()
    policy_version: int = 1
    input_fields: tuple[str, ...] = ()
    provider: str = "unspecified"
    model: str = "unspecified"

    @property
    def decision_status(self) -> str:
        return "ACCEPTED" if self.accepted else "REJECTED"


def _scope_matches(transaction: Transaction, policy: AIPolicy) -> bool:
    if not policy.conditions:
        return True
    groups: dict[int, list[RuleCondition]] = {}
    for condition in policy.conditions:
        groups.setdefault(condition.group, []).append(condition)
    return any(all(condition_matches(transaction, condition) for condition in group) for group in groups.values())


def _unresolved(transaction: Transaction, field: str) -> bool:
    value = transaction.value(field)
    if field == "is_subscription":
        return not bool(value)
    if field == "channel":
        return value in (None, "", "UNKNOWN")
    return value in (None, "", [], set())


def validate_policy(policy: AIPolicy) -> None:
    errors = []
    if not policy.policy_id or not policy.name or not policy.instruction:
        errors.append("policy_id, name, and instruction are required")
    if not 0 <= policy.minimum_confidence <= 1:
        errors.append("minimum_confidence must be between 0 and 1")
    if not policy.target_fields:
        errors.append("at least one target field is required")
    if policy.agent_profile not in {"LUNA_MAX", "SOL_MEDIUM"}:
        errors.append("agent_profile must be LUNA_MAX or SOL_MEDIUM")
    invalid_targets = set(policy.target_fields) - (AI_WRITABLE_FIELDS | {"tags"})
    if invalid_targets:
        errors.append("unsupported target fields: " + ", ".join(sorted(invalid_targets)))
    if set(policy.target_fields) & PROTECTED_FIELDS:
        errors.append("protected fields cannot be AI targets")
    invalid_triggers = set(policy.trigger_fields) - set(policy.target_fields)
    if invalid_triggers:
        errors.append(
            "trigger fields must also be target fields: "
            + ", ".join(sorted(invalid_triggers))
        )
    if errors:
        raise ValueError(f"Invalid AI policy {policy.policy_id}: " + "; ".join(errors))


class AIEnrichmentEngine:
    """Validate model proposals and apply only scoped, unresolved derived fields."""

    def __init__(self, policies: Iterable[AIPolicy]):
        self.policies = tuple(sorted((policy for policy in policies if policy.enabled), key=lambda item: (item.priority, item.policy_id)))
        for policy in self.policies:
            validate_policy(policy)

    def enrich(
        self,
        transaction: Transaction,
        resolver: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> list[AITrace]:
        traces: list[AITrace] = []
        locked = set(transaction.metadata.get("locked_fields", []))
        for policy in self.policies:
            if not _scope_matches(transaction, policy):
                continue
            unresolved = [
                field
                for field in policy.target_fields
                if field not in locked and (field == "tags" or _unresolved(transaction, field))
            ]
            if not unresolved:
                continue
            trigger_fields = policy.trigger_fields or policy.target_fields
            if not any(field in unresolved for field in trigger_fields):
                continue
            response = resolver(self._request(transaction, policy, unresolved))
            # The durable job collector marks a request as pending when no
            # response was supplied yet. Later policies must see the accepted
            # output of earlier policies, so stop this transaction's ordered
            # AI pass and expose only the first unresolved decision. A replay
            # with that response continues from the new fixed point.
            if isinstance(response, dict) and str(response.get("model") or "") == "pending":
                break
            proposals = response.get("proposals", []) if isinstance(response, dict) else []
            if not isinstance(proposals, list):
                raise ValueError(f"AI response for {policy.policy_id} must contain a proposals list")
            for raw in proposals:
                trace = self._apply_proposal(
                    transaction,
                    policy,
                    unresolved,
                    raw,
                    provider=str(response.get("provider") or "unspecified"),
                    model=str(response.get("model") or "unspecified"),
                )
                traces.append(trace)
                if not trace.accepted:
                    transaction.set_value("review_required", True)
                elif trace.field in unresolved and trace.field != "tags":
                    # A response may contain a field more than once. Once an
                    # accepted proposal resolves it, later proposals must not
                    # overwrite the first accepted value.
                    unresolved.remove(trace.field)
        transaction.metadata.setdefault("ai_trace", []).extend(
            {
                "policy_id": trace.policy_id,
                "field": trace.field,
                "confidence": trace.confidence,
                "accepted": trace.accepted,
                "decision_status": trace.decision_status,
                "proposed_value": trace.proposed_value,
                "reason": trace.reason,
                "rationale": trace.rationale,
                "source_refs": list(trace.source_refs),
                "policy_version": trace.policy_version,
                "input_fields": list(trace.input_fields),
                "provider": trace.provider,
                "model": trace.model,
            }
            for trace in traces
        )
        return traces

    @staticmethod
    def _request(transaction: Transaction, policy: AIPolicy, unresolved: list[str]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "agent_profile": policy.agent_profile,
            "instruction": policy.instruction,
            "allowed_fields": unresolved,
            "allowed_values": policy.allowed_values or {},
            "allowed_tags": list(policy.allowed_tags),
            "transaction": {
                "transaction_id": transaction.transaction_id,
                "transaction_at": transaction.transaction_at.isoformat(),
                "card": transaction.card,
                "account": transaction.account,
                "institution": transaction.institution,
                "merchant_raw": transaction.merchant_raw,
                "amount_aed": str(transaction.amount_aed),
                "currency": transaction.currency,
                "source_type": transaction.source_type,
                "vendor": transaction.vendor,
                "category": transaction.category,
                "subcategory": transaction.subcategory,
                "tags": sorted(transaction.tags),
            },
            "response_contract": {
                "proposals": [
                    {
                        "field": "one allowed field",
                        "value": "proposed value",
                        "confidence": "number from 0 to 1",
                        "rationale": "short evidence-based reason",
                        "source_refs": ["message or document references when used"],
                    }
                ]
            },
        }

    @staticmethod
    def _apply_proposal(
        transaction: Transaction,
        policy: AIPolicy,
        unresolved: list[str],
        raw: Any,
        *,
        provider: str,
        model: str,
    ) -> AITrace:
        if not isinstance(raw, dict):
            return AITrace(
                transaction.transaction_id,
                policy.policy_id,
                "",
                None,
                0,
                False,
                "proposal_not_object",
                policy_version=policy.version,
                provider=provider,
                model=model,
            )
        field = str(raw.get("field") or "")
        value = raw.get("value")
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0
        rationale = str(raw.get("rationale") or "")
        refs = raw.get("source_refs") or []
        source_refs = tuple(str(ref) for ref in refs) if isinstance(refs, list) else ()
        reason = "accepted"
        accepted = True
        if field not in unresolved:
            accepted, reason = False, "field_not_requested_or_already_resolved"
        elif field in PROTECTED_FIELDS or field not in AI_WRITABLE_FIELDS | {"tags"}:
            accepted, reason = False, "protected_or_unsupported_field"
        elif not 0 <= confidence <= 1 or confidence < policy.minimum_confidence:
            accepted, reason = False, "below_confidence_threshold"
        elif policy.allowed_values and field in policy.allowed_values and value not in policy.allowed_values[field]:
            accepted, reason = False, "value_not_allowed"
        elif field == "is_subscription" and not isinstance(value, bool):
            accepted, reason = False, "boolean_value_required"
        elif field not in {"is_subscription", "tags", "category_recommendation", "rule_recommendation"} and value in (None, ""):
            accepted, reason = False, "empty_value"
        elif field == "tags":
            values = value if isinstance(value, list) else [value]
            allowed = set(policy.allowed_tags)
            if not values or any(str(item) not in allowed for item in values):
                accepted, reason = False, "tag_not_allowed"
            else:
                transaction.tags.update(str(item) for item in values)
        elif field == "evidence_policy":
            transaction.evidence_policy = str(value)
            transaction.set_value("evidence_status", "REQUESTED")
        elif field == "rule_recommendation":
            if not isinstance(value, dict):
                accepted, reason = False, "rule_recommendation_must_be_object"
            else:
                transaction.metadata.setdefault("static_rule_recommendations", []).append(value)
        elif field == "category_recommendation":
            if not isinstance(value, dict) or not str(value.get("name") or "").strip():
                accepted, reason = False, "category_recommendation_must_name_category"
            else:
                recommendation = {
                    "name": str(value["name"]).strip(),
                    "group": str(value.get("group") or "Needs Review").strip(),
                    "reason": str(value.get("reason") or rationale).strip(),
                }
                transaction.metadata.setdefault("category_recommendations", []).append(recommendation)
                if "tags" not in set(transaction.metadata.get("locked_fields", [])):
                    transaction.tags.add("category-review")
                transaction.set_value("review_required", True)
        else:
            transaction.set_value(field, value)
        return AITrace(
            transaction.transaction_id,
            policy.policy_id,
            field,
            value,
            confidence,
            accepted,
            reason,
            rationale,
            source_refs,
            policy.version,
            tuple(
                field
                for field in (
                    "transaction_at",
                    "card",
                    "account",
                    "institution",
                    "merchant_raw",
                    "amount_aed",
                    "currency",
                    "source_type",
                    "vendor",
                    "category",
                    "subcategory",
                    "tags",
                )
                if transaction.value(field) not in (None, "", [], set())
            ),
            provider,
            model,
        )


def _configured_allowed_values(
    source_name: str,
    *,
    policy_path: Path,
) -> tuple[Any, ...]:
    """Resolve reusable allowlists from the deployment's authoritative config."""
    if source_name == "cashback.buckets":
        configured = os.environ.get("CASHBACK_PROGRAM_CONFIG_PATH")
        config_path = Path(configured) if configured else policy_path.parent / "cashback-programs.json"
        source = json.loads(config_path.read_text(encoding="utf-8"))
        return tuple(
            str(bucket["code"])
            for program in source.get("programs", [])
            for bucket in program.get("buckets", [])
        )
    if source_name == "actual.categories":
        configured = os.environ.get("ACTUAL_BOOTSTRAP_CONFIG_PATH")
        config_path = Path(configured) if configured else policy_path.parent / "actual-bootstrap.json"
        source = json.loads(config_path.read_text(encoding="utf-8"))
        return tuple(
            str(category)
            for group in source.get("category_groups", [])
            for category in group.get("categories", [])
        )
    if source_name in {"properties.codes", "properties.rental_units"}:
        configured = os.environ.get("PROPERTY_CONFIG_PATH")
        config_path = Path(configured) if configured else policy_path.parent / "properties.json"
        registry = load_property_registry(config_path)
        if source_name == "properties.codes":
            return tuple(item.property_code for item in registry.properties)
        return tuple(
            item.rental_unit for item in registry.properties if item.rental_unit
        )
    raise ValueError(f"Unsupported AI allowed-value source: {source_name}")


def load_ai_policies(path: str | Path) -> list[AIPolicy]:
    policy_path = Path(path)
    source = json.loads(policy_path.read_text(encoding="utf-8"))
    if source.get("schema_version") != 1:
        raise ValueError("AI policy config schema_version must be 1")
    policies = []
    for row in source.get("policies", []):
        conditions = tuple(
            RuleCondition(
                field=str(condition["field"]),
                operator=str(condition["operator"]),
                value=condition.get("value"),
                second_value=condition.get("second_value"),
                group=int(condition.get("group", 1)),
                negate=bool(condition.get("negate", False)),
                case_sensitive=bool(condition.get("case_sensitive", False)),
            )
            for condition in row.get("conditions", [])
        )
        allowed_values: dict[str, tuple[Any, ...]] = {
            field: tuple(values) for field, values in row.get("allowed_values", {}).items()
        }
        for field, source_name in row.get("allowed_value_sources", {}).items():
            if field in allowed_values:
                raise ValueError(
                    f"AI policy {row.get('policy_id')} configures {field} as both values and a source"
                )
            allowed_values[str(field)] = _configured_allowed_values(
                str(source_name),
                policy_path=policy_path,
            )
        policies.append(
            AIPolicy(
                policy_id=str(row["policy_id"]),
                name=str(row["name"]),
                priority=int(row.get("priority", 100)),
                instruction=str(row["instruction"]),
                target_fields=tuple(str(field) for field in row.get("target_fields", [])),
                agent_profile=str(row.get("agent_profile", "LUNA_MAX")),
                trigger_fields=tuple(str(field) for field in row.get("trigger_fields", [])),
                conditions=conditions,
                minimum_confidence=float(row.get("minimum_confidence", 0.82)),
                allowed_values=allowed_values,
                allowed_tags=tuple(str(tag) for tag in row.get("allowed_tags", [])),
                enabled=bool(row.get("enabled", True)),
                version=int(row.get("version", 1)),
            )
        )
    return policies


def record_ai_review(
    transaction: Transaction,
    *,
    policy_id: str,
    field: str,
    final_value: Any,
    reviewer: str,
    reason: str,
) -> dict[str, Any]:
    """Record a human correction and lock the reviewed derived field."""
    if field not in AI_WRITABLE_FIELDS | {"tags"} or field in {"category_recommendation", "rule_recommendation"}:
        raise ValueError(f"Unsupported AI review field: {field}")
    if field == "tags":
        if not isinstance(final_value, list):
            raise ValueError("Reviewed tags must be a list")
        transaction.tags = {str(tag) for tag in final_value}
    elif field == "is_subscription":
        if not isinstance(final_value, bool):
            raise ValueError("Reviewed is_subscription must be a boolean")
        transaction.is_subscription = final_value
    elif field == "evidence_policy":
        transaction.evidence_policy = str(final_value)
        transaction.evidence_status = "REQUESTED"
    else:
        transaction.set_value(field, final_value)
    locked = set(transaction.metadata.get("locked_fields", []))
    locked.add(field)
    transaction.metadata["locked_fields"] = sorted(locked)
    review = {
        "policy_id": policy_id,
        "field": field,
        "final_value": final_value,
        "decision_status": "CORRECTED",
        "reviewer": reviewer,
        "reason": reason,
    }
    transaction.metadata.setdefault("ai_review_trace", []).append(review)
    return review


class OpenAICompatibleResolver:
    """Small replaceable adapter for OpenRouter or another chat-completions API."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: Callable[[urllib.request.Request, float], bytes] | None = None,
    ) -> None:
        self.provider = str(config.get("provider") or "openai-compatible")
        model_env = str(config.get("model_env") or "").strip()
        self.model = str(config.get("model") or (os.environ.get(model_env) if model_env else "") or "").strip()
        base_url = str(config.get("base_url") or "").rstrip("/")
        self.endpoint = str(config.get("endpoint") or (f"{base_url}/chat/completions" if base_url else "")).strip()
        self.api_key_env = str(config.get("api_key_env") or "").strip()
        self.timeout_seconds = float(config.get("timeout_seconds", 30))
        self.max_tokens = int(config.get("max_tokens", 800))
        if not self.model or not self.endpoint or not self.api_key_env:
            raise ValueError("AI provider config requires model, endpoint, and api_key_env")
        self.transport = transport or self._default_transport

    @staticmethod
    def _default_transport(request: urllib.request.Request, timeout: float) -> bytes:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured endpoint
            return response.read()

    def __call__(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ValueError(f"Missing AI provider secret environment variable {self.api_key_env}")
        prompt = (
            "Return only JSON matching the response_contract. Do not propose fields outside allowed_fields.\n"
            + json.dumps(request_payload, separators=(",", ":"))
        )
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        raw = json.loads(self.transport(request, self.timeout_seconds).decode("utf-8"))
        try:
            content = raw["choices"][0]["message"]["content"]
            result = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("AI provider returned an invalid chat-completions response") from error
        if not isinstance(result, dict):
            raise ValueError("AI provider response content must be a JSON object")
        return {**result, "provider": self.provider, "model": self.model}


def load_ai_provider(path: str | Path) -> OpenAICompatibleResolver:
    return OpenAICompatibleResolver(json.loads(Path(path).read_text(encoding="utf-8")))
