from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from minecraft_ops_mcp.adapters.mcsm import McsmClient
from minecraft_ops_mcp.config import AppConfig, McsmConfig, MsmpConfig, RconConfig
from minecraft_ops_mcp.errors import OpsError


def app_config(**overrides) -> AppConfig:
    values = {
        "mcsm": McsmConfig(),
        "rcon": RconConfig(),
        "msmp": MsmpConfig(),
        "max_bytes": 256,
        "upload_allowed_dirs": (),
        "file_operation_whitelist": (),
        "upload_url_allowed_domains": (),
    }
    values.update(overrides)
    return AppConfig(**values)


class ConfigAndMcsmSafetyTests(unittest.TestCase):
    def test_env_parses_transfer_limits_and_whitelists(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MINECRAFT_OPS_MAX_BYTES": "12345",
                "MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS": "/tmp/a,/tmp/b",
                "MINECRAFT_OPS_FILE_OPERATION_WHITELIST": "config,mods",
                "MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS": "example.com,cdn.example.org",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        self.assertEqual(config.max_bytes, 12345)
        self.assertEqual(config.upload_allowed_dirs, ("/tmp/a", "/tmp/b"))
        self.assertEqual(config.file_operation_whitelist, ("config", "mods"))
        self.assertEqual(config.upload_url_allowed_domains, ("example.com", "cdn.example.org"))

    def test_local_path_allowlist_accepts_children_and_rejects_outside(self) -> None:
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as denied:
            client = McsmClient(app_config(upload_allowed_dirs=(allowed,)))
            inside = os.path.join(allowed, "upload.txt")
            outside = os.path.join(denied, "upload.txt")
            self.assertEqual(client._ensure_local_path_allowed(inside, "test"), inside)
            with self.assertRaises(OpsError):
                client._ensure_local_path_allowed(outside, "test")

    def test_remote_path_whitelist_rejects_outside_and_traversal(self) -> None:
        client = McsmClient(app_config(file_operation_whitelist=("config", "server.properties")))
        self.assertEqual(client._ensure_remote_path_allowed("config/example.toml", "test"), "config/example.toml")
        self.assertEqual(client._ensure_remote_path_allowed("/server.properties", "test"), "server.properties")
        with self.assertRaises(OpsError):
            client._ensure_remote_path_allowed("mods/example.jar", "test")
        with self.assertRaises(OpsError):
            client._ensure_remote_path_allowed("../server.properties", "test")
        with self.assertRaises(OpsError):
            client._ensure_remote_path_allowed("/../server.properties", "test")

    def test_upload_url_allowlist_matches_subdomains(self) -> None:
        client = McsmClient(app_config(upload_url_allowed_domains=("example.com",)))
        client._ensure_upload_url_allowed("https://example.com/file.jar")
        client._ensure_upload_url_allowed("https://cdn.example.com/file.jar")
        with self.assertRaises(OpsError):
            client._ensure_upload_url_allowed("https://example.org/file.jar")

    def test_upload_local_rejects_oversize_before_network(self) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b"abcd")
            handle.flush()
            client = McsmClient(app_config(max_bytes=3))
            with self.assertRaisesRegex(OpsError, "exceeds max_bytes"):
                client.upload_local_file("/", handle.name)

    def test_upload_local_rejects_non_positive_max_bytes_before_network(self) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b"a")
            handle.flush()
            client = McsmClient(app_config())
            with self.assertRaisesRegex(OpsError, "positive integer"):
                client.upload_local_file("/", handle.name, max_bytes=0)

    def test_upload_url_internal_staging_bypasses_local_upload_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as allowed:
            client = McsmClient(app_config(upload_allowed_dirs=(allowed,), upload_url_allowed_domains=("example.com",)))

            def fake_stream(url: str, target: str, max_bytes: int, **kwargs) -> int:
                with open(target, "wb") as handle:
                    handle.write(b"abc")
                return 3

            with patch.object(client, "_stream_url_to_file", side_effect=fake_stream):
                with patch.object(client, "upload_local_file", return_value={"status": 200, "data": {}}) as upload:
                    client.upload_url_file("https://example.com/mod.jar", "mods")

            self.assertFalse(upload.call_args.kwargs["validate_local_path"])

    def test_download_local_rejects_disallowed_local_path_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as denied:
            client = McsmClient(app_config(upload_allowed_dirs=(allowed,)))
            with self.assertRaisesRegex(OpsError, "MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS"):
                client.download_local_file("server.properties", os.path.join(denied, "server.properties"))

    def test_write_file_rejects_disallowed_remote_path_before_network(self) -> None:
        client = McsmClient(app_config(file_operation_whitelist=("config",)))
        with self.assertRaisesRegex(OpsError, "MINECRAFT_OPS_FILE_OPERATION_WHITELIST"):
            client.write_file("mods/example.jar", "data")


if __name__ == "__main__":
    unittest.main()
