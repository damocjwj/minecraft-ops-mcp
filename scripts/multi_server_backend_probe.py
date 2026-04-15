from __future__ import annotations

import json
import os
import secrets
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from mcp_integration_probe import McpProbe, ProbeResult, require_env
from msmp_temp_instance_probe import random_secret, resolve_server_jar_url


@dataclass(frozen=True)
class TempServer:
    label: str
    nickname: str
    cwd: str
    game_port: int
    rcon_port: int
    msmp_port: int
    rcon_ip: str
    rcon_password: str
    msmp_secret: str
    uuid: str = ""

    def ids(self, daemon_id: str) -> dict[str, str]:
        return {"daemonId": daemon_id, "uuid": self.uuid}


def main() -> int:
    env = require_env(["MCSM_BASE_URL", "MCSM_API_KEY", "MCSM_DEFAULT_DAEMON_ID", "MCSM_DEFAULT_INSTANCE_UUID"])
    env["MCSM_TIMEOUT_SECONDS"] = os.getenv("MCSM_TIMEOUT_SECONDS", "180")
    env["MINECRAFT_OPS_AUDIT_LOG"] = os.getenv("MINECRAFT_OPS_AUDIT_LOG", "/tmp/minecraft-ops-mcp-multi-probe-audit.jsonl")
    jar_url = os.getenv("MINECRAFT_SERVER_JAR_URL") or resolve_server_jar_url(os.getenv("MINECRAFT_VERSION", "1.21.9"))
    now = int(time.time())
    port_base = int(os.getenv("MULTI_PROBE_PORT_BASE", str(26000 + secrets.randbelow(2000))))
    prefix = os.getenv("MULTI_PROBE_PREFIX", f"codex-multi-probe-{now}")
    local_jar = os.getenv("MINECRAFT_SERVER_JAR_PATH") or f"/tmp/{prefix}-server.jar"
    if not os.path.exists(local_jar):
        download_file(jar_url, local_jar)
    servers = [
        TempServer(
            label="a",
            nickname=f"{prefix}-a",
            cwd=f"/opt/mcsmanager/daemon/data/InstanceData/{prefix}-a",
            game_port=port_base,
            rcon_port=port_base + 100,
            msmp_port=port_base + 200,
            rcon_ip="0.0.0.0",
            rcon_password=f"rconA{secrets.token_hex(12)}",
            msmp_secret=random_secret(),
        ),
        TempServer(
            label="b",
            nickname=f"{prefix}-b",
            cwd=f"/opt/mcsmanager/daemon/data/InstanceData/{prefix}-b",
            game_port=port_base + 1,
            rcon_port=port_base + 101,
            msmp_port=port_base + 201,
            rcon_ip="127.0.0.1",
            rcon_password=f"rconB{secrets.token_hex(12)}",
            msmp_secret=random_secret(),
        ),
    ]

    probe = McpProbe(env)
    probe.start()
    created: list[TempServer] = []
    try:
        try:
            created = run_probe(probe, env["MCSM_DEFAULT_DAEMON_ID"], servers, local_jar)
        except Exception as exc:  # noqa: BLE001
            probe.results.append(ProbeResult("probe.unhandled", False, f"{type(exc).__name__}: {exc}"))
    finally:
        if len(created) < len(servers):
            created = merge_discovered_servers(probe, env["MCSM_DEFAULT_DAEMON_ID"], created, servers)
        cleanup_servers(probe, env["MCSM_DEFAULT_DAEMON_ID"], created)
        probe.close()

    report = {
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "servers": [
            {
                "label": server.label,
                "nickname": server.nickname,
                "uuid": server.uuid,
                "gamePort": server.game_port,
                "rconPort": server.rcon_port,
                "msmpPort": server.msmp_port,
            }
            for server in (created or servers)
        ],
        "total": len(probe.results),
        "passed": sum(1 for result in probe.results if result.ok),
        "failed": [result.__dict__ for result in probe.results if not result.ok],
        "results": [result.__dict__ for result in probe.results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if not report["failed"] else 1


def download_file(url: str, target: str) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def run_probe(probe: McpProbe, daemon_id: str, servers: list[TempServer], local_jar: str) -> list[TempServer]:
    initialize(probe)
    tools = probe.expect_ok("tools/list", lambda: probe.request("tools/list")["result"]["tools"])
    names = {item["name"] for item in tools or []}
    probe.results.append(ProbeResult("tools/list.count", len(names) >= 84, f"tool_count={len(names)}"))

    created: list[TempServer] = []
    for server in servers:
        probe.expect_ok(
            f"{server.label}.instance.create",
            lambda server=server: probe.tool("instance.create", {"daemonId": daemon_id, "config": instance_config(server), "confirm": True}),
        )
        uuid = wait_for_instance_uuid(probe, daemon_id, server.nickname)
        probe.results.append(ProbeResult(f"{server.label}.instance.lookup", bool(uuid), f"uuid={uuid}"))
        if not uuid:
            continue
        server = TempServer(**{**server.__dict__, "uuid": uuid})
        created.append(server)
        ids = server.ids(daemon_id)
        probe.expect_ok(f"{server.label}.file.upload_local.server_jar", lambda ids=ids: probe.tool("file.upload_local", {**ids, "upload_dir": "/", "local_path": local_jar, "remote_name": "server.jar", "max_bytes": 128 * 1024 * 1024, "confirm": True}))
        probe.expect_ok(f"{server.label}.file.download_prepare.server_jar", lambda ids=ids: probe.tool("file.download_prepare", {**ids, "file_name": "server.jar"}))
        probe.expect_ok(f"{server.label}.file.write.eula", lambda ids=ids: probe.tool("file.write_new", {**ids, "target": "eula.txt", "text": "eula=true\n", "overwrite": True, "confirm": True}))
        probe.expect_ok(f"{server.label}.file.write.run_sh", lambda ids=ids: probe.tool("file.write_new", {**ids, "target": "run.sh", "text": "exec java -Xms256M -Xmx768M -jar server.jar nogui\n", "overwrite": True, "confirm": True}))
        probe.expect_ok(f"{server.label}.file.write.server_properties.initial", lambda ids=ids, server=server: probe.tool("file.write_new", {**ids, "target": "server.properties", "text": server_properties_initial(server), "overwrite": True, "confirm": True}))
        probe.expect_ok(f"{server.label}.rcon.config.set", lambda ids=ids, server=server: probe.tool("rcon.config.set", {**ids, "enabled": True, "rcon_ip": server.rcon_ip, "rcon_port": server.rcon_port, "rcon_password": server.rcon_password, "confirm": True}))
        probe.expect_ok(f"{server.label}.msmp.config.set", lambda ids=ids, server=server: probe.tool("msmp.config.set", {**ids, "enabled": True, "host": "0.0.0.0", "port": server.msmp_port, "secret": server.msmp_secret, "tls_enabled": False, "confirm": True}))
        assert_rcon_config(probe, server, ids)
        assert_msmp_config(probe, server, ids)

    if len(created) != len(servers):
        return created

    for server in created:
        ids = server.ids(daemon_id)
        probe.expect_ok(f"{server.label}.server.start", lambda ids=ids: probe.tool("server.start", {**ids, "confirm": True}))
        if not wait_for_msmp(probe, server, ids) or not wait_for_rcon(probe, server, ids):
            collect_logs(probe, server, ids)
            return created

    run_interleaved_management(probe, daemon_id, created)
    return created


def initialize(probe: McpProbe) -> None:
    probe.expect_ok(
        "initialize",
        lambda: probe.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "minecraft-ops-mcp-multi-server-probe", "version": "0.1.0"},
            },
        ),
    )
    probe.notify("notifications/initialized")


def run_interleaved_management(probe: McpProbe, daemon_id: str, servers: list[TempServer]) -> None:
    a, b = servers
    for server in (a, b, a, b):
        ids = server.ids(daemon_id)
        probe.expect_ok(f"{server.label}.rcon.list_players.interleaved", lambda ids=ids: probe.tool("rcon.list_players", ids))
        probe.expect_ok(f"{server.label}.msmp.server.status.interleaved", lambda ids=ids: probe.tool("msmp.server.status", ids))

    for server, query in ((a, "daytime"), (b, "gametime")):
        ids = server.ids(daemon_id)
        probe.expect_ok(f"{server.label}.rcon.time_query.{query}", lambda ids=ids, query=query: probe.tool("rcon.time_query", {**ids, "query": query}))
        probe.expect_ok(f"{server.label}.rcon.command.list", lambda ids=ids: probe.tool("rcon.command", {**ids, "command": "list", "confirm": True}))
        probe.expect_ok(f"{server.label}.rcon.save_all", lambda ids=ids: probe.tool("rcon.save_all", {**ids, "flush": False}))
        probe.expect_ok(f"{server.label}.msmp.call.status", lambda ids=ids: probe.tool("msmp.call", {**ids, "method": "minecraft:server/status", "read_only": True}))
        probe.expect_ok(f"{server.label}.msmp.players.list", lambda ids=ids: probe.tool("msmp.players.list", ids))
        probe.expect_ok(f"{server.label}.server.save_world.auto", lambda ids=ids: probe.tool("server.save_world", {**ids, "backend": "auto", "flush": True}))

    probe.expect_ok("a.msmp.server_settings.set.difficulty", lambda: probe.tool("msmp.server_settings.set", {**a.ids(daemon_id), "setting": "difficulty", "value": "peaceful", "confirm": True}))
    probe.expect_ok("b.msmp.server_settings.set.difficulty", lambda: probe.tool("msmp.server_settings.set", {**b.ids(daemon_id), "setting": "difficulty", "value": "normal", "confirm": True}))
    assert_setting(probe, "a", a.ids(daemon_id), "difficulty", "peaceful")
    assert_setting(probe, "b", b.ids(daemon_id), "difficulty", "normal")

    probe.expect_ok("a.server.broadcast.auto", lambda: probe.tool("server.broadcast", {**a.ids(daemon_id), "backend": "auto", "message": "multi-probe-a"}))
    probe.expect_ok("b.server.broadcast.rcon", lambda: probe.tool("server.broadcast", {**b.ids(daemon_id), "backend": "rcon", "message": "multi-probe-b"}))
    probe.expect_ok("a.server.get_logs", lambda: probe.tool("server.get_logs", {**a.ids(daemon_id), "size": 512}))
    probe.expect_ok("b.server.get_logs", lambda: probe.tool("server.get_logs", {**b.ids(daemon_id), "size": 512}))


def assert_rcon_config(probe: McpProbe, server: TempServer, ids: dict[str, str]) -> None:
    data = probe.expect_ok(f"{server.label}.rcon.config.get", lambda: probe.tool("rcon.config.get", ids))
    ok = isinstance(data, dict) and data.get("enabled") is True and data.get("port") == server.rcon_port and data.get("passwordSet") is True
    probe.results.append(ProbeResult(f"{server.label}.rcon.config.assert", ok, f"expected_port={server.rcon_port}", data))


def assert_msmp_config(probe: McpProbe, server: TempServer, ids: dict[str, str]) -> None:
    data = probe.expect_ok(f"{server.label}.msmp.config.get", lambda: probe.tool("msmp.config.get", ids))
    ok = isinstance(data, dict) and data.get("enabled") is True and data.get("port") == server.msmp_port and data.get("secretSet") is True
    probe.results.append(ProbeResult(f"{server.label}.msmp.config.assert", ok, f"expected_port={server.msmp_port}", data))


def assert_setting(probe: McpProbe, label: str, ids: dict[str, str], setting: str, expected: str) -> None:
    data = probe.expect_ok(f"{label}.msmp.server_settings.get.{setting}", lambda: probe.tool("msmp.server_settings.get", {**ids, "setting": setting}))
    actual = data.get("result") if isinstance(data, dict) else None
    probe.results.append(ProbeResult(f"{label}.msmp.server_settings.assert.{setting}", actual == expected, f"expected={expected} actual={actual}", data))


def wait_for_msmp(probe: McpProbe, server: TempServer, ids: dict[str, str]) -> bool:
    deadline = time.monotonic() + 210
    last_error = ""
    while time.monotonic() < deadline:
        try:
            probe.tool("msmp.server.status", ids)
            probe.results.append(ProbeResult(f"{server.label}.wait.msmp.ready", True, f"port={server.msmp_port}"))
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(5)
    probe.results.append(ProbeResult(f"{server.label}.wait.msmp.ready", False, last_error))
    return False


def wait_for_rcon(probe: McpProbe, server: TempServer, ids: dict[str, str]) -> bool:
    deadline = time.monotonic() + 120
    last_error = ""
    while time.monotonic() < deadline:
        try:
            probe.tool("rcon.list_players", ids)
            probe.results.append(ProbeResult(f"{server.label}.wait.rcon.ready", True, f"port={server.rcon_port}"))
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(3)
    probe.results.append(ProbeResult(f"{server.label}.wait.rcon.ready", False, last_error))
    return False


def collect_logs(probe: McpProbe, server: TempServer, ids: dict[str, str]) -> None:
    probe.expect_ok(f"{server.label}.server.get_logs.on_failure", lambda: probe.tool("server.get_logs", {**ids, "size": 4096}))


def wait_for_instance_uuid(probe: McpProbe, daemon_id: str, nickname: str) -> str:
    for _ in range(45):
        data = probe.tool("server.list_instances", {"daemonId": daemon_id, "page": 1, "page_size": 100, "instance_name": nickname})
        for item in (data.get("data") or {}).get("data") or []:
            if (item.get("config") or {}).get("nickname") == nickname:
                return item.get("instanceUuid") or ""
        time.sleep(1)
    return ""


def merge_discovered_servers(probe: McpProbe, daemon_id: str, created: list[TempServer], planned: list[TempServer]) -> list[TempServer]:
    by_label = {server.label: server for server in created}
    for server in planned:
        if server.label in by_label:
            continue
        uuid = ""
        try:
            uuid = wait_for_instance_uuid(probe, daemon_id, server.nickname)
        except Exception:
            uuid = ""
        if uuid:
            by_label[server.label] = TempServer(**{**server.__dict__, "uuid": uuid})
    return list(by_label.values())


def cleanup_servers(probe: McpProbe, daemon_id: str, servers: list[TempServer]) -> None:
    for server in servers:
        if not server.uuid:
            continue
        ids = server.ids(daemon_id)
        try:
            probe.tool("server.stop", {**ids, "confirm": True})
        except Exception:
            pass
    for server in servers:
        if not server.uuid:
            continue
        wait_for_instance_stopped(probe, daemon_id, server)
        probe.expect_ok(f"{server.label}.cleanup.instance.delete", lambda server=server: probe.tool("instance.delete", {"daemonId": daemon_id, "uuids": [server.uuid], "deleteFile": True, "confirm": True}))


def wait_for_instance_stopped(probe: McpProbe, daemon_id: str, server: TempServer) -> None:
    ids = server.ids(daemon_id)
    for _ in range(75):
        try:
            data = probe.tool("server.get_instance", ids)
            status = (data.get("data") or {}).get("status")
            if status == 0:
                probe.results.append(ProbeResult(f"{server.label}.wait.instance.stopped", True, f"status={status}"))
                return
        except Exception:
            return
        time.sleep(2)
    probe.results.append(ProbeResult(f"{server.label}.wait.instance.stopped", False, "instance did not report status=0 before cleanup"))


def instance_config(server: TempServer) -> dict[str, Any]:
    return {
        "nickname": server.nickname,
        "startCommand": "sh run.sh",
        "stopCommand": "stop",
        "cwd": server.cwd,
        "ie": "utf8",
        "oe": "utf8",
        "type": "minecraft/java",
        "tag": ["codex-probe", "multi-server"],
        "endTime": 0,
        "fileCode": "utf8",
        "processType": "general",
        "updateCommand": "",
        "runAs": "",
        "crlf": 1,
        "category": 0,
        "basePort": server.game_port,
        "enableRcon": False,
        "rconPassword": "",
        "rconPort": 0,
        "rconIp": "",
        "actionCommandList": [],
        "terminalOption": {"haveColor": True, "pty": False, "ptyWindowCol": 164, "ptyWindowRow": 40},
        "eventTask": {"autoStart": False, "autoRestart": False, "autoRestartMaxTimes": 3, "ignore": False},
        "java": {"id": ""},
        "docker": {
            "containerName": "",
            "image": "",
            "ports": [],
            "extraVolumes": [],
            "capAdd": [],
            "capDrop": [],
            "devices": [],
            "privileged": False,
            "memory": 0,
            "memorySwap": None,
            "memorySwappiness": None,
            "networkMode": "bridge",
            "networkAliases": [],
            "cpusetCpus": "",
            "cpuUsage": 0,
            "maxSpace": 0,
            "io": 0,
            "network": 0,
            "workingDir": "/data",
            "env": [],
            "changeWorkdir": True,
            "labels": [],
        },
        "pingConfig": {"ip": "", "port": server.game_port, "type": 1},
        "extraServiceConfig": {"openFrpTunnelId": "", "openFrpToken": "", "isOpenFrp": False},
    }


def server_properties_initial(server: TempServer) -> str:
    return "\n".join(
        [
            "eula=true",
            "online-mode=false",
            f"server-port={server.game_port}",
            f"motd=minecraft-ops-mcp-multi-probe-{server.label}",
            "enable-rcon=true",
            f"rcon.port={server.rcon_port}",
            f"rcon.password={server.rcon_password}",
            "management-server-enabled=false",
            "management-server-host=0.0.0.0",
            "management-server-tls-enabled=false",
            "allow-flight=true",
            "white-list=false",
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
