from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str = ""
    data: Any | None = None


@dataclass
class McpProbe:
    env: dict[str, str]
    proc: subprocess.Popen[bytes] | None = None
    next_id: int = 1
    results: list[ProbeResult] = field(default_factory=list)

    def start(self) -> None:
        run_env = os.environ.copy()
        run_env.update(self.env)
        run_env["PYTHONPATH"] = "src"
        self.proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "minecraft_ops_mcp"],
            cwd=ROOT,
            env=run_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def close(self) -> None:
        if self.proc is None:
            return
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        if self.proc.stderr:
            stderr = self.proc.stderr.read().decode("utf-8", errors="replace")
            if stderr.strip():
                self.results.append(ProbeResult("server.stderr", False, stderr.strip()[:2000]))

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("MCP server is not started.")
        request_id = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.proc.stdin.write(body + b"\n")
        self.proc.stdin.flush()
        return self._read_response()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("MCP server is not started.")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.proc.stdin.write(body + b"\n")
        self.proc.stdin.flush()

    def tool(self, name: str, args: dict[str, Any] | None = None) -> Any:
        response = self.request("tools/call", {"name": name, "arguments": args or {}})
        if "error" in response:
            raise RuntimeError(response["error"]["message"])
        result = response["result"]
        if result.get("isError") is True:
            content = result.get("content") or []
            message = content[0].get("text") if content else "Tool returned isError=true."
            raise RuntimeError(message)
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content") or []
        if content and content[0].get("type") == "text":
            return json.loads(content[0].get("text") or "null")
        return result

    def expect_ok(self, name: str, call, detail: str = "") -> Any:
        try:
            data = call()
        except Exception as exc:  # noqa: BLE001
            self.results.append(ProbeResult(name, False, f"{type(exc).__name__}: {exc}"))
            return None
        self.results.append(ProbeResult(name, True, detail, compact(data)))
        return data

    def expect_error(self, name: str, call, contains: str = "") -> Any:
        try:
            data = call()
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            ok = contains in message if contains else True
            self.results.append(ProbeResult(name, ok, message))
            return None
        self.results.append(ProbeResult(name, False, "Expected error, got success.", compact(data)))
        return data

    def _read_response(self) -> dict[str, Any]:
        if self.proc is None or self.proc.stdout is None:
            raise RuntimeError("MCP server stdout is not available.")
        line = self.proc.stdout.readline()
        if line == b"":
            raise RuntimeError("MCP server closed stdout.")
        return json.loads(line.decode("utf-8"))


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in list(value.items())[:20]:
            key_lower = key.lower()
            if any(part in key_lower for part in ("apikey", "api_key", "password", "secret", "token")):
                compacted[key] = "<redacted>"
            elif isinstance(item, str) and ("/upload/" in item or "/download/" in item):
                compacted[key] = "<redacted-url>"
            else:
                compacted[key] = compact(item)
        return compacted
    if isinstance(value, list):
        return [compact(item) for item in value[:8]]
    if isinstance(value, str) and any(marker in value.lower() for marker in ("password=", "secret=", "rcon.password", "management-server-secret")):
        lines: list[str] = []
        for line in value.splitlines(keepends=True):
            lower = line.lower()
            if any(marker in lower for marker in ("password=", "secret=", "rcon.password", "management-server-secret")):
                prefix = line.split("=", 1)[0] if "=" in line else "<sensitive>"
                suffix = "\n" if line.endswith("\n") else ""
                lines.append(f"{prefix}=<redacted>{suffix}")
            else:
                lines.append(line)
        return "".join(lines)
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "...<truncated>"
    return value


def require_env(names: list[str]) -> dict[str, str]:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing required env: {', '.join(missing)}")
    return {name: os.environ[name] for name in names if os.getenv(name)}


def main() -> int:
    env = require_env(["MCSM_BASE_URL", "MCSM_API_KEY", "MCSM_DEFAULT_DAEMON_ID", "MCSM_DEFAULT_INSTANCE_UUID"])
    env["MINECRAFT_OPS_AUDIT_LOG"] = os.getenv("MINECRAFT_OPS_AUDIT_LOG", "/tmp/minecraft-ops-mcp-probe-audit.jsonl")
    probe = McpProbe(env)
    probe.start()
    try:
        run_probe(probe)
    finally:
        probe.close()
    report = {
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": len(probe.results),
        "passed": sum(1 for result in probe.results if result.ok),
        "failed": [result.__dict__ for result in probe.results if not result.ok],
        "results": [result.__dict__ for result in probe.results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if not report["failed"] else 1


def run_probe(probe: McpProbe) -> None:
    probe.expect_ok(
        "initialize",
        lambda: probe.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "minecraft-ops-mcp-probe", "version": "0.3.0"},
            },
        ),
    )
    probe.notify("notifications/initialized")
    tools = probe.expect_ok("tools/list", lambda: probe.request("tools/list")["result"]["tools"])
    names = {item["name"] for item in tools or []}
    probe.results.append(ProbeResult("tools/list.count", len(names) >= 84, f"tool_count={len(names)}"))
    probe.expect_ok("resources/list", lambda: probe.request("resources/list")["result"]["resources"])
    for uri in ("minecraft-ops://config", "minecraft-ops://safety", "minecraft-ops://tools"):
        probe.expect_ok(f"resources/read:{uri}", lambda uri=uri: probe.request("resources/read", {"uri": uri})["result"])
    probe.expect_ok("prompts/list", lambda: probe.request("prompts/list")["result"]["prompts"])
    probe.expect_ok("prompts/get:health", lambda: probe.request("prompts/get", {"name": "minecraft_health_check", "arguments": {"instance_hint": "test"}})["result"])
    probe.expect_ok("prompts/get:restart", lambda: probe.request("prompts/get", {"name": "minecraft_safe_restart", "arguments": {}})["result"])

    probe.expect_error("schema.reject.extra", lambda: probe.tool("server.list_daemons", {"unexpected": True}), "validation")

    probe.expect_ok("server.list_daemons", lambda: probe.tool("server.list_daemons"))
    probe.expect_ok("server.get_daemon_system", lambda: probe.tool("server.get_daemon_system"))
    probe.expect_ok("server.list_instances", lambda: probe.tool("server.list_instances", {"page": 1, "page_size": 20}))
    instance = probe.expect_ok("server.get_instance", lambda: probe.tool("server.get_instance"))
    probe.expect_ok("server.get_logs", lambda: probe.tool("server.get_logs", {"size": 256}))
    probe.expect_ok("server.send_command.dry_run", lambda: probe.tool("server.send_command", {"command": "list", "dry_run": True}))
    probe.expect_ok("server.save_world.mcsm", lambda: probe.tool("server.save_world", {"backend": "mcsm", "flush": True}))
    probe.expect_ok("server.broadcast.mcsm", lambda: probe.tool("server.broadcast", {"backend": "mcsm", "message": "minecraft-ops-mcp probe"}))

    probe.expect_ok("instance.create.dry_run", lambda: probe.tool("instance.create", {"config": {"nickname": "dry-run"}, "dry_run": True}))
    probe.expect_ok("instance.update_config.dry_run", lambda: probe.tool("instance.update_config", {"config": {"nickname": "dry-run"}, "dry_run": True}))
    probe.expect_ok("instance.update_config_patch.dry_run", lambda: probe.tool("instance.update_config_patch", {"patch": {"description": "dry-run-probe"}, "dry_run": True}))
    probe.expect_ok(
        "instance.clone_from_template.dry_run",
        lambda: probe.tool(
            "instance.clone_from_template",
            {"source_uuid": os.environ["MCSM_DEFAULT_INSTANCE_UUID"], "nickname": "dry-run-clone", "dry_run": True},
        ),
    )
    probe.expect_ok("instance.delete.dry_run", lambda: probe.tool("instance.delete", {"uuids": ["dry-run-uuid"], "deleteFile": True, "dry_run": True}))
    probe.expect_ok("instance.reinstall.dry_run", lambda: probe.tool("instance.reinstall", {"targetUrl": "https://example.invalid/server.zip", "title": "dry-run", "dry_run": True}))
    probe.expect_ok("instance.run_update_task.dry_run", lambda: probe.tool("instance.run_update_task", {"dry_run": True}))
    for name in ("server.start", "server.stop", "server.restart", "server.kill"):
        probe.expect_ok(f"{name}.dry_run", lambda name=name: probe.tool(name, {"dry_run": True}))

    base = f"codex_probe_{int(time.time())}"
    probe.expect_ok("file.mkdir", lambda: probe.tool("file.mkdir", {"target": base}))
    probe.expect_ok("file.list.root", lambda: probe.tool("file.list", {"target": "/", "page": 0, "page_size": 20}))
    probe.expect_ok("file.touch", lambda: probe.tool("file.touch", {"target": f"{base}/touch.txt"}))
    probe.expect_ok("file.write", lambda: probe.tool("file.write", {"target": f"{base}/touch.txt", "text": "hello\n", "confirm": True}))
    probe.expect_ok("file.read", lambda: probe.tool("file.read", {"target": f"{base}/touch.txt"}))
    probe.expect_ok("file.write_new", lambda: probe.tool("file.write_new", {"target": f"{base}/new.txt", "text": "new\n", "confirm": True}))
    probe.expect_error("file.write_new.existing.reject", lambda: probe.tool("file.write_new", {"target": f"{base}/new.txt", "text": "again\n", "confirm": True}), "already exists")
    probe.expect_ok("file.copy", lambda: probe.tool("file.copy", {"targets": [[f"{base}/new.txt", f"{base}/copy.txt"]], "confirm": True}))
    probe.expect_ok("file.move", lambda: probe.tool("file.move", {"targets": [[f"{base}/copy.txt", f"{base}/moved.txt"]], "confirm": True}))
    probe.expect_ok("file.compress", lambda: probe.tool("file.compress", {"source": f"{base}/archive.zip", "targets": [f"{base}/new.txt", f"{base}/moved.txt"], "confirm": True}))
    probe.expect_ok("file.uncompress", lambda: probe.tool("file.uncompress", {"source": f"{base}/archive.zip", "target": f"{base}/unzipped", "confirm": True}))
    local_path = f"/tmp/{base}_server.properties"
    probe.expect_ok("file.download_prepare", lambda: probe.tool("file.download_prepare", {"file_name": "server.properties"}))
    probe.expect_ok("file.download_local", lambda: probe.tool("file.download_local", {"file_name": "server.properties", "local_path": local_path, "overwrite": True, "confirm": True}))
    upload_local = f"/tmp/{base}_upload.txt"
    with open(upload_local, "w", encoding="utf-8") as handle:
        handle.write("upload-local\n")
    probe.expect_ok("file.upload_prepare", lambda: probe.tool("file.upload_prepare", {"upload_dir": base}))
    probe.expect_ok("file.upload_local", lambda: probe.tool("file.upload_local", {"upload_dir": base, "local_path": upload_local, "remote_name": "uploaded-local.txt", "confirm": True}))
    probe.expect_ok("file.upload_url.dry_run", lambda: probe.tool("file.upload_url", {"url": "https://example.com/example.txt", "upload_dir": base, "remote_name": "uploaded-url.txt", "dry_run": True}))

    with tempfile.TemporaryDirectory(prefix="minecraft-ops-mcp-modpack-probe-before-") as before_dir:
        with tempfile.TemporaryDirectory(prefix="minecraft-ops-mcp-modpack-probe-after-") as after_dir:
            before_jar = os.path.join(before_dir, "alpha.jar")
            after_jar = os.path.join(after_dir, "alpha.jar")
            write_fabric_probe_jar(before_jar, "alpha", "1.0.0")
            write_fabric_probe_jar(after_jar, "alpha", "1.1.0")
            probe.expect_ok("modpack.inspect_jar", lambda: probe.tool("modpack.inspect_jar", {"local_path": before_jar}))
            before_snapshot = probe.expect_ok("modpack.snapshot_modlist.before", lambda: probe.tool("modpack.snapshot_modlist", {"local_dir": before_dir, "snapshot_name": "probe-before"}))
            after_snapshot = probe.expect_ok("modpack.snapshot_modlist.after", lambda: probe.tool("modpack.snapshot_modlist", {"local_dir": after_dir, "snapshot_name": "probe-after"}))
            probe.expect_ok("modpack.diff_snapshots", lambda: probe.tool("modpack.diff_snapshots", {"before": before_snapshot, "after": after_snapshot}))
            probe.expect_ok("modpack.apply_modlist.dry_run", lambda: probe.tool("modpack.apply_modlist", {"manifest": after_snapshot, "mods_dir": base, "dry_run": True}))
            probe.expect_ok("modpack.rollback_snapshot.dry_run", lambda: probe.tool("modpack.rollback_snapshot", {"snapshot": before_snapshot, "mods_dir": base, "dry_run": True}))
            classification = probe.expect_ok(
                "modpack.classify_startup_result",
                lambda: probe.tool(
                    "modpack.classify_startup_result",
                    {"log_text": "net.fabricmc.loader.impl.discovery.ModResolutionException: Mod alpha requires version >=1.21.1 of minecraft"},
                ),
            )
            scenario = f"{base}-startup"
            recorded = probe.expect_ok(
                "modpack.record_test_run",
                lambda: probe.tool(
                    "modpack.record_test_run",
                    {
                        "run_name": "probe-candidate-a",
                        "scenario": scenario,
                        "outcome": "failed",
                        "before_snapshot": before_snapshot,
                        "after_snapshot": after_snapshot,
                        "classification": classification,
                        "notes": "probe startup classification record",
                        "tags": ["probe", "startup"],
                    },
                ),
            )
            if isinstance(recorded, dict):
                probe.expect_ok("modpack.list_test_runs", lambda: probe.tool("modpack.list_test_runs", {"scenario": scenario, "limit": 5}))
                probe.expect_ok("modpack.get_test_run", lambda: probe.tool("modpack.get_test_run", {"run_id": recorded["runId"]}))

    probe.expect_ok("file.delete.cleanup", lambda: probe.tool("file.delete", {"targets": [base], "confirm": True}))
    for path in (local_path, upload_local):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    probe.expect_ok("rcon.config.get", lambda: probe.tool("rcon.config.get"))
    probe.expect_ok("rcon.config.set.dry_run", lambda: probe.tool("rcon.config.set", {"enabled": True, "dry_run": True}))
    probe.expect_ok("rcon.command.dry_run", lambda: probe.tool("rcon.command", {"command": "list", "dry_run": True}))
    instance_config = ((instance or {}).get("data") or {}).get("config") if isinstance(instance, dict) else {}
    if isinstance(instance_config, dict) and instance_config.get("enableRcon"):
        probe.expect_ok("rcon.list_players", lambda: probe.tool("rcon.list_players"))
        probe.expect_ok("rcon.time_query", lambda: probe.tool("rcon.time_query", {"query": "daytime"}))
        probe.expect_ok("rcon.save_all", lambda: probe.tool("rcon.save_all", {"flush": False}))
        probe.expect_ok("rcon.command", lambda: probe.tool("rcon.command", {"command": "list", "confirm": True}))

    probe.expect_ok("msmp.config.get", lambda: probe.tool("msmp.config.get"))
    probe.expect_ok("msmp.config.set.dry_run", lambda: probe.tool("msmp.config.set", {"enabled": True, "dry_run": True}))
    probe.expect_ok("msmp.call.dry_run", lambda: probe.tool("msmp.call", {"method": "minecraft:server/status", "dry_run": True}))
    for name, args in [
        ("msmp.server.save", {"flush": True, "dry_run": True}),
        ("msmp.server.stop", {"dry_run": True}),
        ("msmp.players.kick", {"players": ["FakePlayer"], "message": "probe", "dry_run": True}),
        ("msmp.bans.add", {"players": ["FakePlayer"], "reason": "probe", "dry_run": True}),
        ("msmp.bans.remove", {"players": ["FakePlayer"], "dry_run": True}),
        ("msmp.bans.set", {"players": ["FakePlayer"], "dry_run": True}),
        ("msmp.bans.clear", {"dry_run": True}),
        ("msmp.ip_bans.add", {"ips": ["203.0.113.10"], "reason": "probe", "dry_run": True}),
        ("msmp.ip_bans.remove", {"ips": ["203.0.113.10"], "dry_run": True}),
        ("msmp.ip_bans.set", {"ips": ["203.0.113.10"], "dry_run": True}),
        ("msmp.ip_bans.clear", {"dry_run": True}),
        ("msmp.allowlist.add", {"players": ["FakePlayer"], "dry_run": True}),
        ("msmp.allowlist.remove", {"players": ["FakePlayer"], "dry_run": True}),
        ("msmp.allowlist.set", {"players": ["FakePlayer"], "dry_run": True}),
        ("msmp.allowlist.clear", {"dry_run": True}),
        ("msmp.operators.add", {"players": ["FakePlayer"], "permission_level": 4, "dry_run": True}),
        ("msmp.operators.remove", {"players": ["FakePlayer"], "dry_run": True}),
        ("msmp.operators.set", {"players": ["FakePlayer"], "permission_level": 4, "dry_run": True}),
        ("msmp.operators.clear", {"dry_run": True}),
        ("msmp.gamerules.update", {"rule": "doDaylightCycle", "value": True, "dry_run": True}),
        ("msmp.server_settings.set", {"setting": "difficulty", "value": "normal", "dry_run": True}),
    ]:
        probe.expect_ok(f"{name}.dry_run", lambda name=name, args=args: probe.tool(name, args))
    probe.expect_error(
        "msmp.server_settings.set.invalid",
        lambda: probe.tool("msmp.server_settings.set", {"setting": "difficulty", "value": "invalid", "dry_run": True}),
        "Invalid value",
    )

    msmp_config = probe.expect_ok("msmp.config.get.after_dry_runs", lambda: probe.tool("msmp.config.get"))
    if isinstance(msmp_config, dict) and msmp_config.get("enabled"):
        for name, args in [
            ("msmp.discover", {}),
            ("msmp.call", {"method": "minecraft:server/status", "read_only": True}),
            ("msmp.players.list", {}),
            ("msmp.server.status", {}),
            ("msmp.bans.get", {}),
            ("msmp.ip_bans.get", {}),
            ("msmp.allowlist.get", {}),
            ("msmp.operators.get", {}),
            ("msmp.gamerules.get", {}),
            ("msmp.server_settings.get", {"setting": "difficulty"}),
            ("msmp.server_settings.list", {}),
        ]:
            probe.expect_ok(name, lambda name=name, args=args: probe.tool(name, args))


def write_fabric_probe_jar(path: str, mod_id: str, version: str) -> None:
    metadata = {
        "schemaVersion": 1,
        "id": mod_id,
        "version": version,
        "name": mod_id.title(),
        "depends": {"minecraft": "~1.21.1", "fabricloader": ">=0.16.0"},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps(metadata))


if __name__ == "__main__":
    raise SystemExit(main())
