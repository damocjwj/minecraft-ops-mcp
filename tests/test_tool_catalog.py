from __future__ import annotations

import unittest

from minecraft_ops_mcp.config import AppConfig
from minecraft_ops_mcp.policy import HIGH_RISK_TOOLS
from minecraft_ops_mcp.tools import make_tools, redact_sensitive_diff


class ToolCatalogTests(unittest.TestCase):
    def test_tool_catalog_has_unique_modern_tools(self) -> None:
        tools = make_tools(AppConfig.from_env())
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), 84)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("modpack.inspect_jar", names)
        self.assertIn("modpack.snapshot_modlist", names)
        self.assertIn("modpack.diff_snapshots", names)
        self.assertIn("modpack.apply_modlist", names)
        self.assertIn("modpack.rollback_snapshot", names)
        self.assertIn("modpack.classify_startup_result", names)
        self.assertIn("modpack.record_test_run", names)
        self.assertIn("modpack.list_test_runs", names)
        self.assertIn("modpack.get_test_run", names)
        self.assertIn("rcon.config.get", names)
        self.assertIn("rcon.config.set", names)
        self.assertIn("msmp.config.get", names)
        self.assertIn("msmp.config.set", names)
        for tool in tools:
            self.assertTrue(tool.title)
            self.assertIn("$schema", tool.input_schema)
            self.assertTrue(tool.output_schema)
            self.assertIsInstance(tool.annotations, dict)

    def test_high_risk_tools_are_registered(self) -> None:
        names = {tool.name for tool in make_tools(AppConfig.from_env())}
        self.assertFalse(HIGH_RISK_TOOLS - names)

    def test_sensitive_diff_values_are_redacted(self) -> None:
        diff = redact_sensitive_diff(
            {
                "rconPassword": {"before": "old-secret", "after": "new-secret"},
                "rconPort": {"before": 25575, "after": 25576},
            }
        )
        self.assertEqual(diff["rconPassword"], {"before": "<redacted>", "after": "<redacted>"})
        self.assertEqual(diff["rconPort"], {"before": 25575, "after": 25576})


if __name__ == "__main__":
    unittest.main()
