from __future__ import annotations

import unittest

from minecraft_ops_mcp.errors import OpsError
from minecraft_ops_mcp.managed_backends import (
    derive_connection_host,
    msmp_runtime_config,
    parse_properties,
    rcon_runtime_config,
    update_properties_text,
    validate_msmp_secret,
)


class ManagedBackendTests(unittest.TestCase):
    def test_rcon_runtime_uses_mcsm_host_when_rcon_ip_is_loopback(self) -> None:
        runtime = rcon_runtime_config(
            {
                "enableRcon": True,
                "rconIp": "127.0.0.1",
                "rconPort": 25575,
                "rconPassword": "secret",
            },
            mcsm_base_url="http://panel.example.test:23333",
            timeout_seconds=5,
            encoding="utf-8",
        )

        self.assertEqual(runtime.host, "panel.example.test")
        self.assertEqual(runtime.port, 25575)
        self.assertTrue(runtime.redacted()["passwordSet"])

    def test_rcon_runtime_keeps_non_loopback_host(self) -> None:
        runtime = rcon_runtime_config(
            {"enableRcon": True, "rconIp": "10.0.0.12", "rconPort": 25576, "rconPassword": "secret"},
            mcsm_base_url="http://panel.example.test:23333",
            timeout_seconds=5,
            encoding="utf-8",
        )

        self.assertEqual(runtime.host, "10.0.0.12")
        self.assertEqual(runtime.port, 25576)

    def test_rcon_runtime_parses_false_string_as_disabled(self) -> None:
        runtime = rcon_runtime_config(
            {"enableRcon": "false", "rconIp": "0.0.0.0", "rconPort": "25575", "rconPassword": "secret"},
            mcsm_base_url="http://panel.example.test:23333",
            timeout_seconds=5,
            encoding="utf-8",
        )

        self.assertFalse(runtime.enabled)

    def test_connection_host_override_wins(self) -> None:
        self.assertEqual(derive_connection_host("127.0.0.1", "http://panel.example.test:23333", "daemon.example.test"), "daemon.example.test")

    def test_msmp_runtime_parses_server_properties(self) -> None:
        text = "\n".join(
            [
                "server-port=25565",
                "management-server-enabled=true",
                "management-server-host=0.0.0.0",
                "management-server-port=25586",
                "management-server-secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678901234",
                "management-server-tls-enabled=false",
            ]
        )

        runtime = msmp_runtime_config(
            text,
            mcsm_base_url="http://panel.example.test:23333",
            timeout_seconds=8,
            tls_verify=True,
        )

        self.assertTrue(runtime.enabled)
        self.assertEqual(runtime.host, "panel.example.test")
        self.assertEqual(runtime.connection().url, "ws://panel.example.test:25586")
        self.assertTrue(runtime.redacted()["secretSet"])

    def test_update_properties_preserves_existing_lines_and_appends_missing(self) -> None:
        updated = update_properties_text(
            "# comment\nserver-port=25565\nmanagement-server-enabled=false\n",
            {
                "management-server-enabled": "true",
                "management-server-port": "25586",
            },
        )

        self.assertIn("# comment", updated)
        self.assertIn("server-port=25565", updated)
        self.assertIn("management-server-enabled=true", updated)
        self.assertIn("management-server-port=25586", updated)
        self.assertEqual(parse_properties(updated)["management-server-enabled"], "true")

    def test_msmp_secret_validation(self) -> None:
        validate_msmp_secret("A" * 40)
        with self.assertRaises(OpsError):
            validate_msmp_secret("not-valid")


if __name__ == "__main__":
    unittest.main()
