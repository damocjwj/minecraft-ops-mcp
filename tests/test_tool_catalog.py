from __future__ import annotations

import unittest

from minecraft_ops_mcp.config import AppConfig
from minecraft_ops_mcp.policy import HIGH_RISK_TOOLS
from minecraft_ops_mcp.tools import make_tools


class ToolCatalogTests(unittest.TestCase):
    def test_tool_catalog_has_unique_modern_tools(self) -> None:
        tools = make_tools(AppConfig.from_env())
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), 71)
        self.assertEqual(len(names), len(set(names)))
        for tool in tools:
            self.assertTrue(tool.title)
            self.assertIn("$schema", tool.input_schema)
            self.assertTrue(tool.output_schema)
            self.assertIsInstance(tool.annotations, dict)

    def test_high_risk_tools_are_registered(self) -> None:
        names = {tool.name for tool in make_tools(AppConfig.from_env())}
        self.assertFalse(HIGH_RISK_TOOLS - names)


if __name__ == "__main__":
    unittest.main()
