from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    code: str
    enabled: bool
    status: str
    display_order: int = 0


@dataclass(frozen=True, slots=True)
class DashboardBlock:
    name: str
    module_codes: tuple[str, ...]
    target: str
    block_type: str
    heading: str
    display_order: int
    enabled: bool = True
    page_url: str | None = None
    data_source_url: str | None = None
    view_url: str | None = None
    render_config: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


def active_blocks(
    target: str,
    modules: Iterable[ModuleDefinition],
    blocks: Iterable[DashboardBlock],
) -> list[DashboardBlock]:
    """Return enabled blocks for a target in deterministic display order.

    A shared block is active when at least one of its related modules is active.
    Blocks without a module relation are invalid because every visible surface
    must have an owning module that can be disabled cleanly.
    """
    module_list = list(modules)
    module_map = {module.code: module for module in module_list}
    if len(module_map) != len(module_list):
        raise ValueError("module codes must be unique")

    selected: list[DashboardBlock] = []
    for block in blocks:
        if not block.enabled or block.target != target:
            continue
        if not block.module_codes:
            raise ValueError(f"dashboard block {block.name!r} has no module")
        unknown = set(block.module_codes) - module_map.keys()
        if unknown:
            raise ValueError(
                f"dashboard block {block.name!r} references unknown modules: "
                + ", ".join(sorted(unknown))
            )
        if any(module_map[code].enabled for code in block.module_codes):
            selected.append(block)

    return sorted(selected, key=lambda block: (block.display_order, block.name.casefold()))


def render_page_links(blocks: Iterable[DashboardBlock]) -> str:
    """Render the page-link portion of a dashboard as Notion Markdown."""
    lines: list[str] = []
    for block in blocks:
        if block.block_type != "Page Link":
            continue
        if not block.page_url:
            raise ValueError(f"page-link block {block.name!r} has no page URL")
        lines.append(f"- [{block.heading}]({block.page_url})")
    return "\n".join(lines)


def linked_view_specs(blocks: Iterable[DashboardBlock]) -> list[dict[str, str | int]]:
    """Return stable create/reuse specifications for linked database views."""
    specs: list[dict[str, str | int]] = []
    for block in blocks:
        if block.block_type not in {"Linked View", "Action Queue", "Summary"}:
            continue
        if not block.data_source_url:
            raise ValueError(f"linked-view block {block.name!r} has no data source")
        spec: dict[str, str | int] = {
            "name": block.name,
            "heading": block.heading,
            "display_order": block.display_order,
            "data_source_url": block.data_source_url,
        }
        if block.view_url:
            spec["view_url"] = block.view_url
        if block.render_config:
            spec["render_config"] = block.render_config
        specs.append(spec)
    return specs


def dashboard_signature(blocks: Iterable[DashboardBlock]) -> str:
    """Hash a rendered block plan so the worker can skip unchanged rewrites."""
    payload = [asdict(block) for block in blocks]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
