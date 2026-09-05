"""Build source-backed n8n 2.36.2 disposable workflow request bodies.

The REST create body is deliberately flat.  Passing an exported workflow under
``workflow`` (or deriving ``name`` from an absent wrapper) makes n8n report the
opaque ``invalid_type``/``name`` undefined error seen in the disposable probe.
This helper keeps create and activation inputs explicit and rejects malformed
workflow exports before they reach n8n.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"N8N_WORKFLOW_{field.upper()}_REQUIRED")
    return value.strip()


def build_create_payload(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Return the flat n8n workflow create/import body.

    Export-only identity and presentation fields (``id``, ``active``,
    ``meta``, and canvas groups) do not belong in the create request.  The
    source workflow name is required at the top level so n8n 2.36.2 cannot
    receive an undefined name through a nested wrapper.
    """

    if not isinstance(workflow, Mapping):
        raise ValueError("N8N_WORKFLOW_EXPORT_REQUIRED")
    name = _required_string(workflow.get("name"), "NAME")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("N8N_WORKFLOW_NODES_REQUIRED")
    connections = workflow.get("connections")
    if not isinstance(connections, Mapping):
        raise ValueError("N8N_WORKFLOW_CONNECTIONS_REQUIRED")
    settings = workflow.get("settings", {})
    if not isinstance(settings, Mapping):
        raise ValueError("N8N_WORKFLOW_SETTINGS_INVALID")
    pin_data = workflow.get("pinData", {})
    if not isinstance(pin_data, Mapping):
        raise ValueError("N8N_WORKFLOW_PIN_DATA_INVALID")
    tags = workflow.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError("N8N_WORKFLOW_TAGS_INVALID")
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": dict(settings),
        "staticData": workflow.get("staticData"),
        "pinData": dict(pin_data),
        "tags": tags,
    }


def build_publish_payload(workflow_id: object) -> dict[str, str]:
    """Return the explicit workflow identity used by the activation request."""

    return {"id": _required_string(workflow_id, "ID")}


def build_runtime_payload(
    workflow: Mapping[str, Any], create_response: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return create and publish bodies using the create response identity."""

    create_payload = build_create_payload(workflow)
    if not isinstance(create_response, Mapping):
        raise ValueError("N8N_WORKFLOW_CREATE_RESPONSE_REQUIRED")
    created_id = create_response.get("id")
    if not isinstance(created_id, str) or not created_id.strip():
        raise ValueError("N8N_WORKFLOW_CREATED_ID_REQUIRED")

    return {
        "create": create_payload,
        "publish": build_publish_payload(created_id),
    }
