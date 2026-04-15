from __future__ import annotations

from .errors import SafetyError


HIGH_RISK_TOOLS = {
    "server.start",
    "server.stop",
    "server.restart",
    "server.kill",
    "server.send_command",
    "instance.create",
    "instance.clone_from_template",
    "instance.update_config",
    "instance.update_config_patch",
    "instance.delete",
    "instance.reinstall",
    "instance.run_update_task",
    "file.write",
    "file.write_new",
    "file.delete",
    "file.move",
    "file.copy",
    "file.compress",
    "file.uncompress",
    "file.download_local",
    "file.upload_local",
    "file.upload_url",
    "modpack.apply_modlist",
    "modpack.rollback_snapshot",
    "rcon.command",
    "msmp.call",
    "msmp.server.save",
    "msmp.server.stop",
    "msmp.players.kick",
    "msmp.bans.add",
    "msmp.bans.remove",
    "msmp.bans.set",
    "msmp.bans.clear",
    "msmp.ip_bans.add",
    "msmp.ip_bans.remove",
    "msmp.ip_bans.set",
    "msmp.ip_bans.clear",
    "msmp.allowlist.add",
    "msmp.allowlist.remove",
    "msmp.allowlist.set",
    "msmp.allowlist.clear",
    "msmp.operators.add",
    "msmp.operators.remove",
    "msmp.operators.set",
    "msmp.operators.clear",
    "msmp.gamerules.update",
    "msmp.server_settings.set",
}


def guard_high_risk(tool_name: str, args: dict, preview: dict) -> dict | None:
    if tool_name == "msmp.call" and args.get("read_only") is True and _is_read_only_msmp_call(args):
        return None
    if tool_name not in HIGH_RISK_TOOLS:
        return None
    if args.get("dry_run") is True:
        return {"dryRun": True, "wouldRun": preview}
    if args.get("confirm") is not True:
        raise SafetyError(
            f"{tool_name} is a high-risk operation. Re-run with confirm=true "
            "after the user explicitly approves it, or use dry_run=true to preview."
        )
    return None


def ensure_plain_command(command: str) -> None:
    if "\n" in command or "\r" in command:
        raise SafetyError("Raw commands must be a single line.")


def ensure_raw_command_allowed(command: str, allowlist: tuple[str, ...], denylist: tuple[str, ...]) -> None:
    stripped = command.strip()
    for denied in denylist:
        if stripped == denied or stripped.startswith(f"{denied} "):
            raise SafetyError(f"Raw command is denied by MINECRAFT_OPS_RAW_COMMAND_DENYLIST: {denied}")
    if allowlist and not any(stripped == allowed or stripped.startswith(f"{allowed} ") for allowed in allowlist):
        raise SafetyError("Raw command is not allowed by MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST.")


def _is_read_only_msmp_call(args: dict) -> bool:
    method = args.get("method")
    if not isinstance(method, str):
        return False
    read_only_exact = {
        "rpc.discover",
        "minecraft:players",
        "minecraft:allowlist",
        "minecraft:bans",
        "minecraft:ip_bans",
        "minecraft:operators",
        "minecraft:gamerules",
        "minecraft:server/status",
    }
    if method in read_only_exact:
        return True
    return method.startswith("minecraft:serversettings/") and not method.endswith("/set")
