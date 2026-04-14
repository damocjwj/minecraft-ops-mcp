from __future__ import annotations

import json
import os
import secrets
import string
import time
import urllib.request
from typing import Any

from mcp_integration_probe import McpProbe, ProbeResult, compact, require_env


def main() -> int:
    env = require_env(["MCSM_BASE_URL", "MCSM_API_KEY", "MCSM_DEFAULT_DAEMON_ID", "MCSM_DEFAULT_INSTANCE_UUID"])
    env["MCSM_TIMEOUT_SECONDS"] = os.getenv("MCSM_TIMEOUT_SECONDS", "120")
    env["MINECRAFT_OPS_AUDIT_LOG"] = os.getenv("MINECRAFT_OPS_AUDIT_LOG", "/tmp/minecraft-ops-mcp-msmp-probe-audit.jsonl")
    jar_url = os.getenv("MINECRAFT_SERVER_JAR_URL") or resolve_server_jar_url(os.getenv("MINECRAFT_VERSION", "1.21.9"))
    secret = os.getenv("MSMP_PROBE_SECRET", random_secret())
    now = int(time.time())
    nickname = os.getenv("MSMP_PROBE_NICKNAME", f"codex-msmp-probe-{now}")
    game_port = int(os.getenv("MSMP_PROBE_GAME_PORT", "25666"))
    msmp_port = int(os.getenv("MSMP_PROBE_PORT", "25686"))
    cwd = os.getenv("MSMP_PROBE_CWD", f"/opt/mcsmanager/daemon/data/InstanceData/{nickname}")

    probe = McpProbe(env)
    probe.start()
    instance_uuid = ""
    try:
        instance_uuid = run_probe(probe, nickname, cwd, game_port, msmp_port, secret, jar_url)
    finally:
        if instance_uuid:
            cleanup_instance(probe, instance_uuid)
        probe.close()

    report = {
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "nickname": nickname,
        "msmpPort": msmp_port,
        "total": len(probe.results),
        "passed": sum(1 for result in probe.results if result.ok),
        "failed": [result.__dict__ for result in probe.results if not result.ok],
        "results": [result.__dict__ for result in probe.results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if not report["failed"] else 1


def resolve_server_jar_url(version: str) -> str:
    manifest = json.load(urllib.request.urlopen("https://launchermeta.mojang.com/mc/game/version_manifest.json", timeout=30))
    entry = next(item for item in manifest["versions"] if item["id"] == version)
    metadata = json.load(urllib.request.urlopen(entry["url"], timeout=30))
    return metadata["downloads"]["server"]["url"]


def random_secret() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(40))


def run_probe(
    probe: McpProbe,
    nickname: str,
    cwd: str,
    game_port: int,
    msmp_port: int,
    secret: str,
    jar_url: str,
) -> str:
    probe.expect_ok(
        "initialize",
        lambda: probe.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "minecraft-ops-mcp-msmp-probe", "version": "0.3.0"},
            },
        ),
    )
    probe.notify("notifications/initialized")
    probe.expect_ok("tools/list", lambda: probe.request("tools/list")["result"]["tools"])
    config = instance_config(nickname, cwd, game_port)
    probe.expect_ok("instance.create.msmp_temp", lambda: probe.tool("instance.create", {"config": config, "confirm": True}))
    instance_uuid = wait_for_instance_uuid(probe, nickname)
    probe.results.append(ProbeResult("instance.lookup.msmp_temp", bool(instance_uuid), f"uuid={instance_uuid}"))
    if not instance_uuid:
        return ""

    ids = {"daemonId": os.environ["MCSM_DEFAULT_DAEMON_ID"], "uuid": instance_uuid}
    probe.expect_ok("file.upload_url.server_jar", lambda: probe.tool("file.upload_url", {**ids, "url": jar_url, "upload_dir": "/", "remote_name": "server.jar", "max_bytes": 128 * 1024 * 1024, "confirm": True}))
    probe.expect_ok("file.write_new.eula", lambda: probe.tool("file.write_new", {**ids, "target": "eula.txt", "text": "eula=true\n", "overwrite": True, "confirm": True}))
    probe.expect_ok("file.write_new.run_sh", lambda: probe.tool("file.write_new", {**ids, "target": "run.sh", "text": "exec java -Xms256M -Xmx768M -jar server.jar nogui\n", "overwrite": True, "confirm": True}))
    properties = server_properties(game_port, msmp_port, secret)
    probe.expect_ok("file.write_new.server_properties", lambda: probe.tool("file.write_new", {**ids, "target": "server.properties", "text": properties, "overwrite": True, "confirm": True}))
    probe.expect_ok("file.read.server_properties", lambda: probe.tool("file.read", {**ids, "target": "server.properties"}))
    probe.expect_ok("instance.update_config_patch.actual", lambda: probe.tool("instance.update_config_patch", {**ids, "patch": {"tag": ["codex-probe"]}, "confirm": True}))
    probe.expect_ok("server.start.msmp_temp", lambda: probe.tool("server.start", {**ids, "confirm": True}))
    wait_for_msmp(probe, msmp_port, secret)
    probe.expect_ok("server.restart.msmp_temp", lambda: probe.tool("server.restart", {**ids, "confirm": True}))
    time.sleep(5)
    wait_for_msmp(probe, msmp_port, secret)

    msmp_env = {
        **probe.env,
        "MSMP_URL": f"ws://damoc-ms-7d42:{msmp_port}",
        "MSMP_SECRET": secret,
        "MSMP_TLS_VERIFY": "false",
    }
    msmp_probe = McpProbe(msmp_env)
    msmp_probe.start()
    try:
        msmp_probe.expect_ok(
            "initialize.msmp",
            lambda: msmp_probe.request(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "minecraft-ops-mcp-msmp-probe", "version": "0.3.0"},
                },
            ),
        )
        msmp_probe.notify("notifications/initialized")
        run_msmp_calls(msmp_probe)
    finally:
        msmp_probe.close()
    probe.results.extend(msmp_probe.results)

    probe.expect_ok("server.get_logs.msmp_temp", lambda: probe.tool("server.get_logs", {**ids, "size": 512}))
    wait_for_instance_stopped(probe, instance_uuid)
    probe.expect_ok("server.start.after_msmp_stop", lambda: probe.tool("server.start", {**ids, "confirm": True}))
    wait_for_msmp(probe, msmp_port, secret)
    probe.expect_ok("server.stop.msmp_temp", lambda: probe.tool("server.stop", {**ids, "confirm": True}))
    wait_for_instance_stopped(probe, instance_uuid)
    return instance_uuid


def run_msmp_calls(probe: McpProbe) -> None:
    for name, args in [
        ("msmp.discover", {}),
        ("msmp.call.status", {"method": "minecraft:server/status", "read_only": True}),
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
        tool_name = name.split(".status")[0] if name == "msmp.call.status" else name
        if name == "msmp.call.status":
            tool_name = "msmp.call"
        probe.expect_ok(name, lambda tool_name=tool_name, args=args: probe.tool(tool_name, args))

    for label, tool_name, args in [
        ("server.broadcast.msmp", "server.broadcast", {"backend": "msmp", "message": "minecraft-ops-mcp msmp probe"}),
        ("server.save_world.msmp", "server.save_world", {"backend": "msmp", "flush": True}),
        ("msmp.server.save", "msmp.server.save", {"flush": True, "confirm": True}),
        ("msmp.players.kick.fake", "msmp.players.kick", {"players": ["FakePlayer"], "message": "probe", "confirm": True}),
        ("msmp.gamerules.update", "msmp.gamerules.update", {"rule": "doDaylightCycle", "value": False, "confirm": True}),
        ("msmp.gamerules.restore", "msmp.gamerules.update", {"rule": "doDaylightCycle", "value": True, "confirm": True}),
        ("msmp.server_settings.set", "msmp.server_settings.set", {"setting": "difficulty", "value": "normal", "confirm": True}),
        ("msmp.allowlist.add", "msmp.allowlist.add", {"players": ["FakePlayer"], "confirm": True}),
        ("msmp.allowlist.remove", "msmp.allowlist.remove", {"players": ["FakePlayer"], "confirm": True}),
        ("msmp.allowlist.set", "msmp.allowlist.set", {"players": [], "confirm": True}),
        ("msmp.allowlist.clear", "msmp.allowlist.clear", {"confirm": True}),
        ("msmp.operators.add", "msmp.operators.add", {"players": ["FakePlayer"], "permission_level": 4, "confirm": True}),
        ("msmp.operators.remove", "msmp.operators.remove", {"players": ["FakePlayer"], "confirm": True}),
        ("msmp.operators.set", "msmp.operators.set", {"players": [], "confirm": True}),
        ("msmp.operators.clear", "msmp.operators.clear", {"confirm": True}),
        ("msmp.bans.add", "msmp.bans.add", {"players": ["FakePlayer"], "reason": "probe", "source": "mcp", "confirm": True}),
        ("msmp.bans.remove", "msmp.bans.remove", {"players": ["FakePlayer"], "confirm": True}),
        ("msmp.bans.set", "msmp.bans.set", {"players": [], "confirm": True}),
        ("msmp.bans.clear", "msmp.bans.clear", {"confirm": True}),
        ("msmp.ip_bans.add", "msmp.ip_bans.add", {"ips": ["203.0.113.10"], "reason": "probe", "source": "mcp", "confirm": True}),
        ("msmp.ip_bans.remove", "msmp.ip_bans.remove", {"ips": ["203.0.113.10"], "confirm": True}),
        ("msmp.ip_bans.set", "msmp.ip_bans.set", {"ips": [], "confirm": True}),
        ("msmp.ip_bans.clear", "msmp.ip_bans.clear", {"confirm": True}),
    ]:
        probe.expect_ok(label, lambda tool_name=tool_name, args=args: probe.tool(tool_name, args))

    probe.expect_ok("msmp.server.stop", lambda: probe.tool("msmp.server.stop", {"confirm": True}))


def cleanup_instance(probe: McpProbe, instance_uuid: str) -> None:
    ids = {"daemonId": os.environ["MCSM_DEFAULT_DAEMON_ID"], "uuid": instance_uuid}
    wait_for_instance_stopped(probe, instance_uuid)
    probe.expect_ok("cleanup.instance.delete", lambda: probe.tool("instance.delete", {"daemonId": ids["daemonId"], "uuids": [instance_uuid], "deleteFile": True, "confirm": True}))


def wait_for_instance_stopped(probe: McpProbe, instance_uuid: str) -> None:
    ids = {"daemonId": os.environ["MCSM_DEFAULT_DAEMON_ID"], "uuid": instance_uuid}
    for _ in range(60):
        data = probe.tool("server.get_instance", ids)
        status = (data.get("data") or {}).get("status")
        if status == 0:
            probe.results.append(ProbeResult("wait.instance.stopped", True, f"status={status}"))
            return
        time.sleep(2)
    probe.results.append(ProbeResult("wait.instance.stopped", False, "instance did not report status=0 before cleanup"))


def wait_for_instance_uuid(probe: McpProbe, nickname: str) -> str:
    for _ in range(30):
        data = probe.tool("server.list_instances", {"page": 1, "page_size": 100, "instance_name": nickname})
        for item in (data.get("data") or {}).get("data") or []:
            if (item.get("config") or {}).get("nickname") == nickname:
                return item.get("instanceUuid") or ""
        time.sleep(1)
    return ""


def wait_for_msmp(probe: McpProbe, port: int, secret: str) -> None:
    env = {**probe.env, "MSMP_URL": f"ws://damoc-ms-7d42:{port}", "MSMP_SECRET": secret, "MSMP_TLS_VERIFY": "false"}
    deadline = time.monotonic() + 180
    last_error = ""
    while time.monotonic() < deadline:
        temp = McpProbe(env)
        temp.start()
        try:
            temp.request(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "minecraft-ops-mcp-msmp-probe", "version": "0.3.0"},
                },
            )
            temp.notify("notifications/initialized")
            temp.tool("msmp.server.status")
            probe.results.append(ProbeResult("wait.msmp.ready", True, f"port={port}"))
            return
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        finally:
            temp.close()
        time.sleep(5)
    probe.results.append(ProbeResult("wait.msmp.ready", False, last_error))
    raise RuntimeError(f"MSMP did not become ready: {last_error}")


def instance_config(nickname: str, cwd: str, game_port: int) -> dict[str, Any]:
    return {
        "nickname": nickname,
        "startCommand": "sh run.sh",
        "stopCommand": "stop",
        "cwd": cwd,
        "ie": "utf8",
        "oe": "utf8",
        "type": "minecraft/java",
        "tag": ["codex-probe"],
        "endTime": 0,
        "fileCode": "utf8",
        "processType": "general",
        "updateCommand": "",
        "runAs": "",
        "crlf": 1,
        "category": 0,
        "basePort": game_port,
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
        "pingConfig": {"ip": "", "port": game_port, "type": 1},
        "extraServiceConfig": {"openFrpTunnelId": "", "openFrpToken": "", "isOpenFrp": False},
    }


def server_properties(game_port: int, msmp_port: int, secret: str) -> str:
    return "\n".join(
        [
            "eula=true",
            "online-mode=false",
            f"server-port={game_port}",
            "motd=minecraft-ops-mcp-msmp-probe",
            "enable-rcon=false",
            "management-server-enabled=true",
            "management-server-host=0.0.0.0",
            f"management-server-port={msmp_port}",
            f"management-server-secret={secret}",
            "management-server-tls-enabled=false",
            "allow-flight=true",
            "white-list=false",
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
