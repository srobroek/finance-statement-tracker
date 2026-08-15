from unittest import TestCase

from finance_tracker.dashboard import (
    DashboardBlock,
    ModuleDefinition,
    active_blocks,
    dashboard_signature,
    linked_view_specs,
    render_page_links,
)


class DashboardCompositionTests(TestCase):
    def setUp(self) -> None:
        self.modules = [
            ModuleDefinition("core", True, "Active", 10),
            ModuleDefinition("planned", False, "Planned", 20),
        ]

    def test_only_enabled_modules_render_in_stable_order(self) -> None:
        blocks = [
            DashboardBlock("Second", ("core",), "Landing", "Page Link", "Second", 20, page_url="https://example/2"),
            DashboardBlock("Hidden", ("planned",), "Landing", "Page Link", "Hidden", 5, page_url="https://example/hidden"),
            DashboardBlock("First", ("core",), "Landing", "Page Link", "First", 10, page_url="https://example/1"),
        ]
        selected = active_blocks("Landing", self.modules, blocks)
        self.assertEqual([block.name for block in selected], ["First", "Second"])
        self.assertEqual(
            render_page_links(selected),
            "- [First](https://example/1)\n- [Second](https://example/2)",
        )

    def test_linked_view_spec_reuses_existing_view(self) -> None:
        block = DashboardBlock(
            "Review queue",
            ("core",),
            "Landing",
            "Action Queue",
            "Needs attention",
            100,
            data_source_url="collection://transactions",
            view_url="view://review",
            render_config="Review Required = true",
        )
        self.assertEqual(
            linked_view_specs([block]),
            [{
                "name": "Review queue",
                "heading": "Needs attention",
                "display_order": 100,
                "data_source_url": "collection://transactions",
                "view_url": "view://review",
                "render_config": "Review Required = true",
            }],
        )

    def test_signature_changes_when_placement_changes(self) -> None:
        original = DashboardBlock("Block", ("core",), "Landing", "Page Link", "Block", 10, page_url="https://example")
        moved = DashboardBlock("Block", ("core",), "Financial", "Page Link", "Block", 10, page_url="https://example")
        self.assertNotEqual(dashboard_signature([original]), dashboard_signature([moved]))

    def test_unknown_module_is_rejected(self) -> None:
        block = DashboardBlock("Broken", ("missing",), "Landing", "Page Link", "Broken", 10, page_url="https://example")
        with self.assertRaises(ValueError):
            active_blocks("Landing", self.modules, [block])
