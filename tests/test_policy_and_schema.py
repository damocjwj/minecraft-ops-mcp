from __future__ import annotations

import unittest

from minecraft_ops_mcp.errors import SafetyError
from minecraft_ops_mcp.policy import ensure_raw_command_allowed


class PolicyTests(unittest.TestCase):
    def test_raw_command_allowlist(self) -> None:
        ensure_raw_command_allowed("list", ("list",), ())
        ensure_raw_command_allowed("time query daytime", ("time",), ())
        with self.assertRaises(SafetyError):
            ensure_raw_command_allowed("stop", ("list",), ())

    def test_raw_command_denylist(self) -> None:
        with self.assertRaises(SafetyError):
            ensure_raw_command_allowed("ban Steve", (), ("ban",))


if __name__ == "__main__":
    unittest.main()
