from __future__ import annotations

import json
from typing import Any, Callable

from .adapters.mcsm import McsmClient
from .adapters.msmp import MsmpClient
from .adapters.rcon import RconClient
from .audit import audit
from .config import AppConfig
from .errors import OpsError
from .managed_backends import (
    extract_text_response,
    msmp_runtime_config,
    parse_properties,
    rcon_runtime_config,
    update_properties_text,
    validate_msmp_secret,
)
from .models import Tool
from .modpack import ModpackManager
from .policy import HIGH_RISK_TOOLS, ensure_plain_command, ensure_raw_command_allowed, guard_high_risk


Handler = Callable[[dict], Any]


def make_tools(config: AppConfig) -> list[Tool]:
    mcsm = McsmClient(config)
    rcon = RconClient(config)
    msmp = MsmpClient(config)
    modpack = ModpackManager(config, mcsm)

    def wrap(name: str, handler: Handler) -> Handler:
        def inner(args: dict) -> Any:
            try:
                result = handler(args)
                audit(config, name, args, "ok")
                return result
            except Exception as exc:  # noqa: BLE001
                audit(config, name, args, "error", str(exc))
                raise

        return inner

    def action(tool_name: str, args: dict, preview: dict, run: Callable[[], Any]) -> Any:
        dry = guard_high_risk(tool_name, args, preview)
        if dry is not None:
            return dry
        return run()

    def require_str(args: dict, key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or value == "":
            raise OpsError(f"Missing required string argument: {key}")
        return value

    def require_list(args: dict, key: str) -> list:
        value = args.get(key)
        if not isinstance(value, list):
            raise OpsError(f"Missing required list argument: {key}")
        return value

    def require_dict(args: dict, key: str) -> dict:
        value = args.get(key)
        if not isinstance(value, dict):
            raise OpsError(f"Missing required object argument: {key}")
        return value

    def mcsm_ids(args: dict) -> tuple[str | None, str | None]:
        return args.get("daemonId"), args.get("uuid")

    def run_server_save(args: dict) -> Any:
        backend = args.get("backend", "auto")
        flush = bool(args.get("flush", True))
        if backend == "msmp":
            return msmp_call(args, "minecraft:server/save", [flush])
        if backend == "rcon":
            return rcon.save_all(flush, rcon_runtime(args).connection())
        if backend == "auto":
            for attempt in (
                lambda: msmp_call(args, "minecraft:server/save", [flush]),
                lambda: rcon.save_all(flush, rcon_runtime(args).connection()),
            ):
                try:
                    return attempt()
                except OpsError:
                    pass
        if backend in {"mcsm", "auto"} and config.mcsm.enabled:
            daemon_id, uuid = mcsm_ids(args)
            return mcsm.send_command("save-all", daemon_id, uuid)
        raise OpsError("No backend configured for save_world.")

    def run_broadcast(args: dict) -> Any:
        message = require_str(args, "message")
        if "\n" in message or "\r" in message:
            raise OpsError("message must be a single line.")
        backend = args.get("backend", "auto")
        if backend == "msmp":
            params: dict[str, Any] = {"message": {"literal": message}, "overlay": bool(args.get("overlay", False))}
            if "targets" in args:
                params["receivingPlayers"] = args["targets"]
            return msmp_call(args, "minecraft:server/system_message", [params])
        command = f"say {message}"
        if backend == "rcon":
            return rcon.command(command, rcon_runtime(args).connection())
        if backend == "auto":
            for attempt in (
                lambda: msmp_call(args, "minecraft:server/system_message", [params_from_broadcast_args(args, message)]),
                lambda: rcon.command(command, rcon_runtime(args).connection()),
            ):
                try:
                    return attempt()
                except OpsError:
                    pass
        if backend in {"mcsm", "auto"} and config.mcsm.enabled:
            daemon_id, uuid = mcsm_ids(args)
            return mcsm.send_command(command, daemon_id, uuid)
        raise OpsError("No backend configured for broadcast.")

    def params_from_broadcast_args(args: dict, message: str) -> dict[str, Any]:
        params: dict[str, Any] = {"message": {"literal": message}, "overlay": bool(args.get("overlay", False))}
        if "targets" in args:
            params["receivingPlayers"] = args["targets"]
        return params

    def player_object(value: Any) -> dict:
        if isinstance(value, str):
            return {"name": value}
        if isinstance(value, dict) and ("name" in value or "id" in value):
            return value
        raise OpsError("Player entries must be a username string or an object with name/id.")

    def list_player_objects(args: dict) -> list[dict]:
        return [player_object(item) for item in require_list(args, "players")]

    def game_rule_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def operator_objects(args: dict) -> list[dict]:
        return [
            {
                "player": player,
                "permissionLevel": int(args.get("permission_level", 4)),
                "bypassesPlayerLimit": bool(args.get("bypasses_player_limit", False)),
            }
            for player in list_player_objects(args)
        ]

    def raw_command_policy(command: str) -> None:
        ensure_raw_command_allowed(command, config.raw_command_allowlist, config.raw_command_denylist)

    def extract_instance_config(instance_response: Any) -> dict:
        if not isinstance(instance_response, dict):
            raise OpsError("MCSManager instance response was not an object.")
        data = instance_response.get("data", instance_response)
        if not isinstance(data, dict):
            raise OpsError("MCSManager instance data was not an object.")
        config_data = data.get("config") if isinstance(data.get("config"), dict) else data
        if not isinstance(config_data, dict):
            raise OpsError("MCSManager instance config was not an object.")
        return dict(config_data)

    def current_instance_config(args: dict) -> dict:
        return extract_instance_config(mcsm.get_instance(*mcsm_ids(args)))

    def rcon_runtime(args: dict):
        return rcon_runtime_config(
            current_instance_config(args),
            mcsm_base_url=config.mcsm.base_url,
            timeout_seconds=config.rcon.timeout_seconds,
            encoding=config.rcon.encoding,
            connection_host=args.get("connection_host"),
        )

    def rcon_config_get(args: dict) -> dict[str, Any]:
        return rcon_runtime(args).redacted()

    def rcon_config_set(args: dict) -> Any:
        patch: dict[str, Any] = {}
        if "enabled" in args:
            patch["enableRcon"] = bool(args["enabled"])
        if args.get("rcon_ip") is not None:
            patch["rconIp"] = args.get("rcon_ip")
        if args.get("rcon_port") is not None:
            patch["rconPort"] = int(args["rcon_port"])
        if args.get("rcon_password") is not None:
            patch["rconPassword"] = args.get("rcon_password")
        if not patch:
            raise OpsError("Pass at least one RCON config field to update.")
        current = current_instance_config(args)
        merged = deep_merge_dict(current, patch)
        diff = redact_sensitive_diff(shallow_diff(current, merged))
        preview_patch = dict(patch)
        if "rconPassword" in preview_patch:
            preview_patch["rconPassword"] = "<redacted>"
        return action(
            "rcon.config.set",
            args,
            {"backend": "mcsm", "target": id_preview(config, args), "patch": preview_patch, "diff": diff},
            lambda: mcsm.update_instance_config(merged, *mcsm_ids(args)),
        )

    def server_properties_text(args: dict) -> str:
        target = args.get("properties_path") or "server.properties"
        return extract_text_response(mcsm.read_file(target, *mcsm_ids(args)))

    def msmp_runtime(args: dict):
        return msmp_runtime_config(
            server_properties_text(args),
            mcsm_base_url=config.mcsm.base_url,
            timeout_seconds=config.msmp.timeout_seconds,
            tls_verify=config.msmp.tls_verify,
            connection_host=args.get("connection_host"),
        )

    def msmp_call(args: dict, method: str, params: Any | None = None) -> Any:
        return msmp.call(method, params, msmp_runtime(args).connection())

    def msmp_config_get(args: dict) -> dict[str, Any]:
        runtime = msmp_runtime(args)
        return {**runtime.redacted(), "propertiesPath": args.get("properties_path") or "server.properties"}

    def msmp_config_set(args: dict) -> Any:
        updates: dict[str, str] = {}
        if "enabled" in args:
            updates["management-server-enabled"] = "true" if bool(args["enabled"]) else "false"
        if args.get("host") is not None:
            updates["management-server-host"] = str(args["host"])
        if args.get("port") is not None:
            updates["management-server-port"] = str(int(args["port"]))
        if args.get("secret") is not None:
            secret = str(args["secret"])
            validate_msmp_secret(secret)
            updates["management-server-secret"] = secret
        if "tls_enabled" in args:
            updates["management-server-tls-enabled"] = "true" if bool(args["tls_enabled"]) else "false"
        if not updates:
            raise OpsError("Pass at least one MSMP config field to update.")
        target = args.get("properties_path") or "server.properties"
        current_text = server_properties_text(args)
        updated_text = update_properties_text(current_text, updates)
        preview_updates = dict(updates)
        if "management-server-secret" in preview_updates:
            preview_updates["management-server-secret"] = "<redacted>"
        before = parse_properties(current_text)
        after = parse_properties(updated_text)
        return action(
            "msmp.config.set",
            args,
            {
                "backend": "mcsm",
                "target": id_preview(config, args),
                "propertiesPath": target,
                "updates": preview_updates,
                "changedKeys": sorted(key for key in updates if before.get(key) != after.get(key)),
            },
            lambda: mcsm.write_file(target, updated_text, *mcsm_ids(args)),
        )

    def update_config_patch(args: dict) -> Any:
        patch = require_dict(args, "patch")
        current = extract_instance_config(mcsm.get_instance(*mcsm_ids(args)))
        merged = deep_merge_dict(current, patch)
        diff = shallow_diff(current, merged)
        return action(
            "instance.update_config_patch",
            args,
            {"backend": "mcsm", "target": id_preview(config, args), "patch": patch, "diff": diff},
            lambda: mcsm.update_instance_config(merged, *mcsm_ids(args)),
        )

    def clone_from_template(args: dict) -> Any:
        source_daemon_id = args.get("source_daemonId") or args.get("daemonId")
        source_uuid = require_str(args, "source_uuid")
        target_daemon_id = args.get("daemonId")
        template_config = extract_instance_config(mcsm.get_instance(source_daemon_id, source_uuid))
        new_config = dict(template_config)
        for key in ("uuid", "daemonId", "started", "pid", "status", "createDatetime", "lastDatetime"):
            new_config.pop(key, None)
        if args.get("nickname"):
            new_config["nickname"] = args["nickname"]
        if args.get("cwd"):
            new_config["cwd"] = args["cwd"]
        if args.get("overrides") is not None:
            new_config = deep_merge_dict(new_config, require_dict(args, "overrides"))
        return action(
            "instance.clone_from_template",
            args,
            {
                "backend": "mcsm",
                "source": {"daemonId": source_daemon_id or "<missing>", "uuid": source_uuid},
                "targetDaemonId": target_daemon_id or config.mcsm.default_daemon_id or "<missing>",
                "configKeys": sorted(new_config.keys()),
                "nickname": new_config.get("nickname"),
                "cwd": new_config.get("cwd"),
            },
            lambda: mcsm.create_instance(target_daemon_id, new_config),
        )

    def ban_objects(args: dict) -> list[dict]:
        entries: list[dict] = []
        for player in list_player_objects(args):
            entry: dict[str, Any] = {"player": player}
            for key in ("reason", "source", "expires"):
                if args.get(key):
                    entry[key] = args[key]
            entries.append(entry)
        return entries

    def ip_ban_objects(args: dict) -> list[dict]:
        entries: list[dict] = []
        for ip in require_list(args, "ips"):
            if not isinstance(ip, str) or not ip:
                raise OpsError("ips must contain non-empty strings.")
            entry: dict[str, Any] = {"ip": ip}
            for key in ("reason", "source", "expires"):
                if args.get(key):
                    entry[key] = args[key]
            entries.append(entry)
        return entries

    def msmp_server_settings_list(args: dict) -> Any:
        discover = msmp_call(args, "rpc.discover")
        methods = sorted(_collect_msmp_methods(discover))
        settings: dict[str, dict[str, Any]] = {}
        for method in methods:
            prefix = "minecraft:serversettings/"
            if not method.startswith(prefix):
                continue
            name = method.removeprefix(prefix)
            writable = name.endswith("/set")
            setting = name.removesuffix("/set")
            item = settings.setdefault(
                setting,
                {
                    "setting": setting,
                    "getMethod": f"minecraft:serversettings/{setting}",
                    "setMethod": f"minecraft:serversettings/{setting}/set",
                    "readable": False,
                    "writable": False,
                    "knownType": _jsonable_setting_type(SERVER_SETTING_TYPES.get(setting)),
                },
            )
            if writable:
                item["writable"] = True
            else:
                item["readable"] = True
        return {"settings": list(settings.values()), "methodCount": len(methods)}

    def validate_server_setting(setting: str, value: Any) -> None:
        expected = SERVER_SETTING_TYPES.get(setting)
        if expected is None:
            return
        if isinstance(expected, set):
            if value not in expected:
                raise OpsError(f"Invalid value for {setting}: expected one of {sorted(expected)}.")
        elif expected == "bool":
            if not isinstance(value, bool):
                raise OpsError(f"Invalid value for {setting}: expected boolean.")
        elif expected == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise OpsError(f"Invalid value for {setting}: expected integer.")

    def set_server_setting(args: dict) -> Any:
        setting = require_str(args, "setting")
        value = args.get("value")
        validate_server_setting(setting, value)
        return action(
            "msmp.server_settings.set",
            args,
            {"backend": "msmp", "setting": setting, "value": value},
            lambda: msmp_call(args, f"minecraft:serversettings/{setting}/set", [value]),
        )

    def msmp_props(extra: dict | None = None) -> dict:
        props = id_props(
            {
                "properties_path": string("Path to server.properties inside the instance.", default="server.properties"),
                "connection_host": string("Optional connection host override when management-server-host is a bind or loopback address."),
            }
        )
        if extra:
            props.update(extra)
        return props

    def apply_modlist_tool(args: dict) -> Any:
        plan = modpack.plan_apply_modlist(
            manifest=args.get("manifest"),
            manifest_path=args.get("manifest_path"),
            snapshot_id=args.get("snapshot_id"),
            mods_dir=args.get("mods_dir", "mods"),
            clean_extra=bool(args.get("clean_extra", True)),
            recursive=bool(args.get("recursive", False)),
            current_paths=args.get("current_paths"),
            daemon_public_base_url=args.get("daemon_public_base_url"),
            daemon_id=args.get("daemonId"),
            uuid=args.get("uuid"),
            max_bytes=int(args.get("max_bytes", config.max_bytes)),
        )
        return action(
            "modpack.apply_modlist",
            args,
            {"backend": "mcsm", "instance": id_preview(config, args), "plan": plan},
            lambda: modpack.apply_modlist(
                manifest=args.get("manifest"),
                manifest_path=args.get("manifest_path"),
                snapshot_id=args.get("snapshot_id"),
                mods_dir=args.get("mods_dir", "mods"),
                clean_extra=bool(args.get("clean_extra", True)),
                recursive=bool(args.get("recursive", False)),
                current_paths=args.get("current_paths"),
                before_snapshot_name=args.get("before_snapshot_name"),
                after_snapshot_name=args.get("after_snapshot_name"),
                daemon_public_base_url=args.get("daemon_public_base_url"),
                daemon_id=args.get("daemonId"),
                uuid=args.get("uuid"),
                max_bytes=int(args.get("max_bytes", config.max_bytes)),
            ),
        )

    def rollback_snapshot_tool(args: dict) -> Any:
        plan = modpack.plan_rollback_snapshot(
            snapshot=args.get("snapshot"),
            snapshot_path=args.get("snapshot_path"),
            snapshot_id=args.get("snapshot_id"),
            mods_dir=args.get("mods_dir", "mods"),
            clean_extra=bool(args.get("clean_extra", True)),
            recursive=bool(args.get("recursive", False)),
            current_paths=args.get("current_paths"),
            daemon_public_base_url=args.get("daemon_public_base_url"),
            daemon_id=args.get("daemonId"),
            uuid=args.get("uuid"),
            max_bytes=int(args.get("max_bytes", config.max_bytes)),
        )
        return action(
            "modpack.rollback_snapshot",
            args,
            {"backend": "mcsm", "instance": id_preview(config, args), "plan": plan},
            lambda: modpack.rollback_snapshot(
                snapshot=args.get("snapshot"),
                snapshot_path=args.get("snapshot_path"),
                snapshot_id=args.get("snapshot_id"),
                mods_dir=args.get("mods_dir", "mods"),
                clean_extra=bool(args.get("clean_extra", True)),
                recursive=bool(args.get("recursive", False)),
                current_paths=args.get("current_paths"),
                before_snapshot_name=args.get("before_snapshot_name"),
                after_snapshot_name=args.get("after_snapshot_name"),
                daemon_public_base_url=args.get("daemon_public_base_url"),
                daemon_id=args.get("daemonId"),
                uuid=args.get("uuid"),
                max_bytes=int(args.get("max_bytes", config.max_bytes)),
            ),
        )

    tools = [
        Tool(
            "server.list_daemons",
            "List MCSManager daemon nodes.",
            schema({}),
            wrap("server.list_daemons", lambda args: mcsm.list_daemons()),
        ),
        Tool(
            "server.get_daemon_system",
            "Get MCSManager daemon system status summary.",
            schema({}),
            wrap("server.get_daemon_system", lambda args: mcsm.get_daemon_system()),
        ),
        Tool(
            "server.list_instances",
            "List instances on an MCSManager daemon.",
            schema(
                {
                    "daemonId": string("MCSManager daemon id. Uses MCSM_DEFAULT_DAEMON_ID if omitted."),
                    "page": integer("Page number.", default=1),
                    "page_size": integer("Page size.", default=20),
                    "instance_name": string("Optional name filter."),
                    "status": string("Optional status filter.", default=""),
                }
            ),
            wrap(
                "server.list_instances",
                lambda args: mcsm.list_instances(
                    args.get("daemonId"),
                    int(args.get("page", 1)),
                    int(args.get("page_size", 20)),
                    args.get("instance_name"),
                    args.get("status", ""),
                ),
            ),
        ),
        Tool(
            "server.get_instance",
            "Get MCSManager instance details.",
            id_schema(),
            wrap("server.get_instance", lambda args: mcsm.get_instance(*mcsm_ids(args))),
        ),
        Tool(
            "instance.create",
            "Create an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(
                {
                    "daemonId": string("MCSManager daemon id. Uses MCSM_DEFAULT_DAEMON_ID if omitted."),
                    "config": {"type": "object", "description": "MCSManager InstanceConfig object.", "additionalProperties": True},
                },
                ["config"],
            ),
            wrap(
                "instance.create",
                lambda args: action(
                    "instance.create",
                    args,
                    {
                        "backend": "mcsm",
                        "daemonId": args.get("daemonId") or config.mcsm.default_daemon_id or "<missing>",
                        "configKeys": sorted(require_dict(args, "config").keys()),
                    },
                    lambda: mcsm.create_instance(args.get("daemonId"), require_dict(args, "config")),
                ),
            ),
        ),
        Tool(
            "instance.update_config",
            "Update an MCSManager instance config. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props({"config": {"type": "object", "description": "MCSManager InstanceConfig object.", "additionalProperties": True}}),
                ["config"],
            ),
            wrap(
                "instance.update_config",
                lambda args: action(
                    "instance.update_config",
                    args,
                    {
                        "backend": "mcsm",
                        "target": id_preview(config, args),
                        "configKeys": sorted(require_dict(args, "config").keys()),
                    },
                    lambda: mcsm.update_instance_config(require_dict(args, "config"), *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "instance.update_config_patch",
            "Read the current MCSManager instance config, deep-merge a patch, and update it. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props({"patch": {"type": "object", "description": "Partial InstanceConfig patch.", "additionalProperties": True}}),
                ["patch"],
            ),
            wrap("instance.update_config_patch", update_config_patch),
        ),
        Tool(
            "instance.clone_from_template",
            "Create a new MCSManager instance from an existing instance config. Requires confirm=true or dry_run=true.",
            confirm_schema(
                {
                    "source_daemonId": string("Source daemon id. Uses target daemonId if omitted."),
                    "source_uuid": string("Source instance UUID."),
                    "daemonId": string("Target daemon id. Uses MCSM_DEFAULT_DAEMON_ID if omitted."),
                    "nickname": string("Optional new instance nickname."),
                    "cwd": string("Optional new instance working directory."),
                    "overrides": {"type": "object", "description": "Additional config overrides.", "additionalProperties": True},
                },
                ["source_uuid"],
            ),
            wrap("instance.clone_from_template", clone_from_template),
        ),
        Tool(
            "instance.delete",
            "Delete MCSManager instances. Requires confirm=true or dry_run=true.",
            confirm_schema(
                {
                    "daemonId": string("MCSManager daemon id. Uses MCSM_DEFAULT_DAEMON_ID if omitted."),
                    "uuids": array("Instance UUIDs to delete.", {"type": "string"}),
                    "deleteFile": boolean("Also delete instance files.", default=False),
                },
                ["uuids"],
            ),
            wrap(
                "instance.delete",
                lambda args: action(
                    "instance.delete",
                    args,
                    {
                        "backend": "mcsm",
                        "daemonId": args.get("daemonId") or config.mcsm.default_daemon_id or "<missing>",
                        "uuids": args.get("uuids"),
                        "deleteFile": bool(args.get("deleteFile", False)),
                    },
                    lambda: mcsm.delete_instances(require_list(args, "uuids"), bool(args.get("deleteFile", False)), args.get("daemonId")),
                ),
            ),
        ),
        Tool(
            "instance.reinstall",
            "Reinstall an MCSManager instance from a package URL. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "targetUrl": string("Package URL."),
                        "title": string("Install task title."),
                        "description": string("Install task description.", default=""),
                    }
                ),
                ["targetUrl", "title"],
            ),
            wrap(
                "instance.reinstall",
                lambda args: action(
                    "instance.reinstall",
                    args,
                    {
                        "backend": "mcsm",
                        "target": id_preview(config, args),
                        "targetUrl": args.get("targetUrl"),
                        "title": args.get("title"),
                    },
                    lambda: mcsm.reinstall_instance(
                        require_str(args, "targetUrl"),
                        require_str(args, "title"),
                        args.get("description", ""),
                        *mcsm_ids(args),
                    ),
                ),
            ),
        ),
        Tool(
            "instance.run_update_task",
            "Run the configured MCSManager update task. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props()),
            wrap(
                "instance.run_update_task",
                lambda args: action(
                    "instance.run_update_task",
                    args,
                    {"backend": "mcsm", "target": id_preview(config, args), "task_name": "update"},
                    lambda: mcsm.run_update_task(*mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "server.start",
            "Start an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props()),
            wrap(
                "server.start",
                lambda args: action(
                    "server.start",
                    args,
                    {"backend": "mcsm", "action": "open", "target": id_preview(config, args)},
                    lambda: mcsm.instance_action("open", *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "server.stop",
            "Stop an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props()),
            wrap(
                "server.stop",
                lambda args: action(
                    "server.stop",
                    args,
                    {"backend": "mcsm", "action": "stop", "target": id_preview(config, args)},
                    lambda: mcsm.instance_action("stop", *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "server.restart",
            "Restart an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props()),
            wrap(
                "server.restart",
                lambda args: action(
                    "server.restart",
                    args,
                    {"backend": "mcsm", "action": "restart", "target": id_preview(config, args)},
                    lambda: mcsm.instance_action("restart", *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "server.kill",
            "Force-kill an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props()),
            wrap(
                "server.kill",
                lambda args: action(
                    "server.kill",
                    args,
                    {"backend": "mcsm", "action": "kill", "target": id_preview(config, args)},
                    lambda: mcsm.instance_action("kill", *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "server.send_command",
            "Send a raw console command through MCSManager. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props({"command": string("Single-line server command.")}), required=["command"]),
            wrap(
                "server.send_command",
                lambda args: _run_command_tool(
                    args,
                    "server.send_command",
                    {"backend": "mcsm", "command": args.get("command"), "target": id_preview(config, args)},
                    lambda command: mcsm.send_command(command, *mcsm_ids(args)),
                    action,
                    require_str,
                    raw_command_policy,
                ),
            ),
        ),
        Tool(
            "server.get_logs",
            "Read MCSManager instance output logs.",
            schema(id_props({"size": integer("Optional log size in KB, 1-2048.")})),
            wrap("server.get_logs", lambda args: mcsm.get_logs(*mcsm_ids(args), args.get("size"))),
        ),
        Tool(
            "server.save_world",
            "Save the Minecraft world using MSMP, RCON, or MCSManager.",
            schema(backend_props(id_props({"flush": boolean("Whether MSMP should flush data to disk.", default=True)}))),
            wrap("server.save_world", run_server_save),
        ),
        Tool(
            "server.broadcast",
            "Broadcast a system chat message using MSMP, RCON, or MCSManager.",
            schema(
                backend_props(
                    {
                        "message": string("Message to send to players."),
                        "targets": {"description": "Optional MSMP targets.", "type": ["array", "object", "string"]},
                        "overlay": boolean("Optional MSMP overlay/hotbar flag."),
                        **id_props(),
                    }
                ),
                required=["message"],
            ),
            wrap("server.broadcast", run_broadcast),
        ),
        Tool(
            "file.list",
            "List files in an MCSManager instance directory.",
            schema(
                id_props(
                    {
                        "target": string("Directory path.", default="/"),
                        "page": integer("Page number.", default=0),
                        "page_size": integer("Page size.", default=100),
                    }
                )
            ),
            wrap(
                "file.list",
                lambda args: mcsm.list_files(
                    args.get("target", "/"),
                    *mcsm_ids(args),
                    int(args.get("page", 0)),
                    int(args.get("page_size", 100)),
                ),
            ),
        ),
        Tool(
            "file.read",
            "Read a text file from an MCSManager instance.",
            schema(id_props({"target": string("File path.")}), required=["target"]),
            wrap("file.read", lambda args: mcsm.read_file(require_str(args, "target"), *mcsm_ids(args))),
        ),
        Tool(
            "file.download_prepare",
            "Create a temporary MCSManager daemon download token for one instance file.",
            schema(id_props({"file_name": string("File path to download.")}), required=["file_name"]),
            wrap("file.download_prepare", lambda args: mcsm.prepare_download(require_str(args, "file_name"), *mcsm_ids(args))),
        ),
        Tool(
            "file.download_local",
            "Download one MCSManager instance file to the MCP server local filesystem. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "file_name": string("Remote file path to download."),
                        "local_path": string("Optional local output path. Defaults to /tmp/minecraft-ops-mcp-downloads/<file>."),
                        "daemon_public_base_url": string("Optional daemon base URL override, for example http://host:24444."),
                        "overwrite": boolean("Overwrite local_path if it already exists.", default=False),
                        "max_bytes": integer("Maximum accepted download size in bytes.", default=config.max_bytes),
                    }
                ),
                ["file_name"],
            ),
            wrap(
                "file.download_local",
                lambda args: action(
                    "file.download_local",
                    args,
                    {
                        "backend": "mcsm",
                        "file_name": args.get("file_name"),
                        "local_path": args.get("local_path") or "<default>",
                        "overwrite": bool(args.get("overwrite", False)),
                        "max_bytes": int(args.get("max_bytes", config.max_bytes)),
                        "instance": id_preview(config, args),
                    },
                    lambda: mcsm.download_local_file(
                        require_str(args, "file_name"),
                        args.get("local_path"),
                        args.get("daemon_public_base_url"),
                        bool(args.get("overwrite", False)),
                        *mcsm_ids(args),
                        max_bytes=int(args.get("max_bytes", config.max_bytes)),
                    ),
                ),
            ),
        ),
        Tool(
            "file.upload_prepare",
            "Create a temporary MCSManager daemon upload token for one instance directory.",
            schema(id_props({"upload_dir": string("Directory path to upload into.")}), required=["upload_dir"]),
            wrap("file.upload_prepare", lambda args: mcsm.prepare_upload(require_str(args, "upload_dir"), *mcsm_ids(args))),
        ),
        Tool(
            "file.upload_local",
            "Upload a local file into an MCSManager instance directory. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "upload_dir": string("Instance directory path to upload into."),
                        "local_path": string("Local filesystem path readable by this MCP server."),
                        "remote_name": string("Optional remote filename."),
                        "daemon_public_base_url": string("Optional daemon base URL override, for example http://host:24444."),
                        "max_bytes": integer("Maximum accepted upload size in bytes.", default=config.max_bytes),
                    }
                ),
                ["upload_dir", "local_path"],
            ),
            wrap(
                "file.upload_local",
                lambda args: action(
                    "file.upload_local",
                    args,
                    {
                        "backend": "mcsm",
                        "upload_dir": args.get("upload_dir"),
                        "local_path": args.get("local_path"),
                        "remote_name": args.get("remote_name"),
                        "max_bytes": int(args.get("max_bytes", config.max_bytes)),
                        "instance": id_preview(config, args),
                    },
                    lambda: mcsm.upload_local_file(
                        require_str(args, "upload_dir"),
                        require_str(args, "local_path"),
                        args.get("remote_name"),
                        args.get("daemon_public_base_url"),
                        *mcsm_ids(args),
                        max_bytes=int(args.get("max_bytes", config.max_bytes)),
                    ),
                ),
            ),
        ),
        Tool(
            "file.upload_url",
            "Download a remote URL on the MCP server and upload it into an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "url": string("http:// or https:// URL to fetch."),
                        "upload_dir": string("Instance directory path to upload into."),
                        "remote_name": string("Optional remote filename."),
                        "daemon_public_base_url": string("Optional daemon base URL override, for example http://host:24444."),
                        "max_bytes": integer("Maximum accepted remote file size in bytes.", default=config.max_bytes),
                    }
                ),
                ["url", "upload_dir"],
            ),
            wrap(
                "file.upload_url",
                lambda args: action(
                    "file.upload_url",
                    args,
                    {
                        "backend": "mcsm",
                        "url": args.get("url"),
                        "upload_dir": args.get("upload_dir"),
                        "remote_name": args.get("remote_name"),
                        "max_bytes": int(args.get("max_bytes", config.max_bytes)),
                        "instance": id_preview(config, args),
                    },
                    lambda: mcsm.upload_url_file(
                        require_str(args, "url"),
                        require_str(args, "upload_dir"),
                        args.get("remote_name"),
                        args.get("daemon_public_base_url"),
                        int(args.get("max_bytes", config.max_bytes)),
                        *mcsm_ids(args),
                    ),
                ),
            ),
        ),
        Tool(
            "file.write",
            "Write a text file in an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props({"target": string("File path."), "text": string("New file content.")}), ["target", "text"]),
            wrap(
                "file.write",
                lambda args: action(
                    "file.write",
                    args,
                    {
                        "backend": "mcsm",
                        "target": args.get("target"),
                        "bytes": len(str(args.get("text", "")).encode("utf-8")),
                        "instance": id_preview(config, args),
                    },
                    lambda: mcsm.write_file(require_str(args, "target"), require_str(args, "text"), *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "file.write_new",
            "Create a text file with touch -> write, optionally refusing to overwrite existing files. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "target": string("File path."),
                        "text": string("New file content."),
                        "overwrite": boolean("Overwrite if the file already exists.", default=False),
                    }
                ),
                ["target", "text"],
            ),
            wrap(
                "file.write_new",
                lambda args: action(
                    "file.write_new",
                    args,
                    {
                        "backend": "mcsm",
                        "target": args.get("target"),
                        "bytes": len(str(args.get("text", "")).encode("utf-8")),
                        "overwrite": bool(args.get("overwrite", False)),
                        "instance": id_preview(config, args),
                    },
                    lambda: mcsm.write_new_file(
                        require_str(args, "target"),
                        require_str(args, "text"),
                        bool(args.get("overwrite", False)),
                        *mcsm_ids(args),
                    ),
                ),
            ),
        ),
        Tool(
            "file.delete",
            "Delete files or folders from an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props({"targets": array("Paths to delete.", {"type": "string"})}), ["targets"]),
            wrap(
                "file.delete",
                lambda args: action(
                    "file.delete",
                    args,
                    {"backend": "mcsm", "targets": args.get("targets"), "instance": id_preview(config, args)},
                    lambda: mcsm.delete_files(require_list(args, "targets"), *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "file.move",
            "Move or rename files in an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props({"targets": pair_array("Pairs of [source, target].")}), ["targets"]),
            wrap(
                "file.move",
                lambda args: action(
                    "file.move",
                    args,
                    {"backend": "mcsm", "targets": args.get("targets"), "instance": id_preview(config, args)},
                    lambda: mcsm.move_files(require_list(args, "targets"), *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "file.copy",
            "Copy files in an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props({"targets": pair_array("Pairs of [source, target].")}), ["targets"]),
            wrap(
                "file.copy",
                lambda args: action(
                    "file.copy",
                    args,
                    {"backend": "mcsm", "targets": args.get("targets"), "instance": id_preview(config, args)},
                    lambda: mcsm.copy_files(require_list(args, "targets"), *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "file.mkdir",
            "Create a folder in an MCSManager instance.",
            schema(id_props({"target": string("Folder path.")}), required=["target"]),
            wrap("file.mkdir", lambda args: mcsm.mkdir(require_str(args, "target"), *mcsm_ids(args))),
        ),
        Tool(
            "file.touch",
            "Create an empty file in an MCSManager instance.",
            schema(id_props({"target": string("File path.")}), required=["target"]),
            wrap("file.touch", lambda args: mcsm.touch(require_str(args, "target"), *mcsm_ids(args))),
        ),
        Tool(
            "file.compress",
            "Create a zip archive in an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "source": string("Output zip path, for example /backup/world.zip."),
                        "targets": array("Files/folders to include.", {"type": "string"}),
                    }
                ),
                ["source", "targets"],
            ),
            wrap(
                "file.compress",
                lambda args: action(
                    "file.compress",
                    args,
                    {"backend": "mcsm", "source": args.get("source"), "targets": args.get("targets")},
                    lambda: mcsm.compress(require_str(args, "source"), require_list(args, "targets"), *mcsm_ids(args)),
                ),
            ),
        ),
        Tool(
            "file.uncompress",
            "Extract a zip archive in an MCSManager instance. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "source": string("Zip file path."),
                        "target": string("Destination folder."),
                        "code": string("Archive encoding: utf-8, gbk, or big5.", default="utf-8"),
                    }
                ),
                ["source", "target"],
            ),
            wrap(
                "file.uncompress",
                lambda args: action(
                    "file.uncompress",
                    args,
                    {"backend": "mcsm", "source": args.get("source"), "target": args.get("target")},
                    lambda: mcsm.uncompress(
                        require_str(args, "source"),
                        require_str(args, "target"),
                        args.get("code", "utf-8"),
                        *mcsm_ids(args),
                    ),
                ),
            ),
        ),
        Tool(
            "modpack.inspect_jar",
            "Inspect one Minecraft mod jar and extract loader metadata, mod ids, versions, dependencies, and sha256.",
            schema(
                id_props(
                    {
                        "local_path": string("Local jar path on the MCP server. Mutually exclusive with remote_path."),
                        "remote_path": string("Jar path inside the MCSManager instance. Mutually exclusive with local_path."),
                        "daemon_public_base_url": string("Optional daemon base URL override for remote_path downloads."),
                        "max_bytes": integer("Maximum accepted jar size in bytes for remote_path.", default=config.max_bytes),
                    }
                )
            ),
            wrap(
                "modpack.inspect_jar",
                lambda args: modpack.inspect_jar(
                    local_path=args.get("local_path"),
                    remote_path=args.get("remote_path"),
                    daemon_public_base_url=args.get("daemon_public_base_url"),
                    daemon_id=args.get("daemonId"),
                    uuid=args.get("uuid"),
                    max_bytes=int(args.get("max_bytes", config.max_bytes)),
                ),
            ),
        ),
        Tool(
            "modpack.snapshot_modlist",
            "Create a structured snapshot of a mods directory, optionally saving it under the modpack workspace.",
            schema(
                id_props(
                    {
                        "mods_dir": string("Remote instance mods directory.", default="mods"),
                        "local_dir": string("Local mods directory on the MCP server. If set, remote mods_dir is ignored."),
                        "recursive": boolean("Scan nested directories too.", default=False),
                        "remote_paths": array("Explicit remote jar paths to snapshot when directory listing is unreliable.", {"type": "string"}),
                        "save": boolean("Save the snapshot JSON under MINECRAFT_OPS_MODPACK_WORKSPACE.", default=True),
                        "snapshot_name": string("Optional human-readable snapshot name."),
                        "minecraft_version": string("Optional target Minecraft version annotation."),
                        "loader": string("Optional loader annotation, for example fabric, forge, neoforge, quilt."),
                        "notes": string("Optional operator notes."),
                        "daemon_public_base_url": string("Optional daemon base URL override for remote jar downloads."),
                        "max_bytes": integer("Maximum accepted jar size in bytes for each remote jar.", default=config.max_bytes),
                    }
                )
            ),
            wrap(
                "modpack.snapshot_modlist",
                lambda args: modpack.snapshot_modlist(
                    mods_dir=args.get("mods_dir", "mods"),
                    local_dir=args.get("local_dir"),
                    recursive=bool(args.get("recursive", False)),
                    save=bool(args.get("save", True)),
                    remote_paths=args.get("remote_paths"),
                    snapshot_name=args.get("snapshot_name"),
                    minecraft_version=args.get("minecraft_version"),
                    loader=args.get("loader"),
                    notes=args.get("notes"),
                    daemon_public_base_url=args.get("daemon_public_base_url"),
                    daemon_id=args.get("daemonId"),
                    uuid=args.get("uuid"),
                    max_bytes=int(args.get("max_bytes", config.max_bytes)),
                ),
            ),
        ),
        Tool(
            "modpack.diff_snapshots",
            "Diff two modpack snapshots by jar file and parsed mod id/version metadata.",
            schema(
                {
                    "before": {"type": "object", "description": "Inline before snapshot object.", "additionalProperties": True},
                    "after": {"type": "object", "description": "Inline after snapshot object.", "additionalProperties": True},
                    "before_path": string("Path to before snapshot JSON under MINECRAFT_OPS_MODPACK_WORKSPACE."),
                    "after_path": string("Path to after snapshot JSON under MINECRAFT_OPS_MODPACK_WORKSPACE."),
                    "before_snapshot_id": string("Before snapshot id saved under workspace/snapshots."),
                    "after_snapshot_id": string("After snapshot id saved under workspace/snapshots."),
                }
            ),
            wrap(
                "modpack.diff_snapshots",
                lambda args: modpack.diff_snapshots(
                    before=args.get("before"),
                    after=args.get("after"),
                    before_path=args.get("before_path"),
                    after_path=args.get("after_path"),
                    before_snapshot_id=args.get("before_snapshot_id"),
                    after_snapshot_id=args.get("after_snapshot_id"),
                ),
            ),
        ),
        Tool(
            "modpack.apply_modlist",
            "Apply a desired modlist snapshot or lockfile to an MCSManager instance mods directory. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "manifest": {"type": "object", "description": "Inline desired modlist snapshot or lockfile.", "additionalProperties": True},
                        "manifest_path": string("Path to desired manifest JSON under MINECRAFT_OPS_MODPACK_WORKSPACE."),
                        "snapshot_id": string("Saved snapshot id to apply from workspace/snapshots."),
                        "mods_dir": string("Remote instance mods directory to manage.", default="mods"),
                        "clean_extra": boolean("Delete remote jar files that are not present in the desired manifest.", default=True),
                        "recursive": boolean("Compare nested jar paths under mods_dir too.", default=False),
                        "current_paths": array("Explicit current remote jar paths when directory listing is unreliable.", {"type": "string"}),
                        "before_snapshot_name": string("Optional name for the automatic before-apply rollback snapshot."),
                        "after_snapshot_name": string("Optional name for the automatic after-apply snapshot."),
                        "daemon_public_base_url": string("Optional daemon base URL override for jar downloads/uploads."),
                        "max_bytes": integer("Maximum accepted jar size in bytes.", default=config.max_bytes),
                    }
                )
            ),
            wrap("modpack.apply_modlist", apply_modlist_tool),
        ),
        Tool(
            "modpack.rollback_snapshot",
            "Restore an MCSManager instance mods directory to a saved modpack snapshot. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "snapshot": {"type": "object", "description": "Inline snapshot object to roll back to.", "additionalProperties": True},
                        "snapshot_path": string("Path to snapshot JSON under MINECRAFT_OPS_MODPACK_WORKSPACE."),
                        "snapshot_id": string("Saved snapshot id under workspace/snapshots."),
                        "mods_dir": string("Remote instance mods directory to restore.", default="mods"),
                        "clean_extra": boolean("Delete remote jar files that are not present in the rollback snapshot.", default=True),
                        "recursive": boolean("Compare nested jar paths under mods_dir too.", default=False),
                        "current_paths": array("Explicit current remote jar paths when directory listing is unreliable.", {"type": "string"}),
                        "before_snapshot_name": string("Optional name for the automatic before-rollback snapshot."),
                        "after_snapshot_name": string("Optional name for the automatic after-rollback snapshot."),
                        "daemon_public_base_url": string("Optional daemon base URL override for jar downloads/uploads."),
                        "max_bytes": integer("Maximum accepted jar size in bytes.", default=config.max_bytes),
                    }
                )
            ),
            wrap("modpack.rollback_snapshot", rollback_snapshot_tool),
        ),
        Tool(
            "modpack.classify_startup_result",
            "Classify Minecraft startup logs or crash reports into common modpack compatibility failure categories.",
            schema(
                id_props(
                    {
                        "log_text": string("Inline latest.log excerpt or full text."),
                        "crash_text": string("Inline crash report excerpt or full text."),
                        "log_path": string("Remote latest.log path inside the MCSManager instance."),
                        "crash_report_path": string("Remote crash report path inside the MCSManager instance."),
                        "max_chars": integer("Maximum trailing characters to analyze.", default=262144),
                    }
                )
            ),
            wrap(
                "modpack.classify_startup_result",
                lambda args: modpack.classify_startup_result(
                    log_text=args.get("log_text"),
                    crash_text=args.get("crash_text"),
                    log_path=args.get("log_path"),
                    crash_report_path=args.get("crash_report_path"),
                    daemon_id=args.get("daemonId"),
                    uuid=args.get("uuid"),
                    max_chars=int(args.get("max_chars", 262144)),
                ),
            ),
            output_schema=STARTUP_CLASSIFICATION_OUTPUT_SCHEMA,
        ),
        Tool(
            "modpack.record_test_run",
            "Record one modpack compatibility test run with snapshot references, classification, notes, and trace metadata.",
            schema(
                {
                    "run_name": string("Optional human-readable test run name."),
                    "scenario": string("Scenario or matrix cell name, for example sodium-iris-candidate-a."),
                    "outcome": string("Outcome such as passed, failed, blocked, rolled_back, or unknown."),
                    "target": {"type": "object", "description": "Instance/runtime target metadata.", "additionalProperties": True},
                    "candidate": {"type": "object", "description": "Candidate modlist or version-combination metadata.", "additionalProperties": True},
                    "before_snapshot": {"type": "object", "description": "Inline before snapshot object.", "additionalProperties": True},
                    "before_snapshot_path": string("Path to before snapshot JSON under MINECRAFT_OPS_MODPACK_WORKSPACE."),
                    "before_snapshot_id": string("Before snapshot id saved under workspace/snapshots."),
                    "after_snapshot": {"type": "object", "description": "Inline after snapshot object.", "additionalProperties": True},
                    "after_snapshot_path": string("Path to after snapshot JSON under MINECRAFT_OPS_MODPACK_WORKSPACE."),
                    "after_snapshot_id": string("After snapshot id saved under workspace/snapshots."),
                    "apply_result": {"type": "object", "description": "Result returned by modpack.apply_modlist.", "additionalProperties": True},
                    "rollback_result": {"type": "object", "description": "Result returned by modpack.rollback_snapshot.", "additionalProperties": True},
                    "classification": {"type": "object", "description": "Result returned by modpack.classify_startup_result.", "additionalProperties": True},
                    "log_excerpt": string("Small log excerpt to store with the run record."),
                    "crash_excerpt": string("Small crash-report excerpt to store with the run record."),
                    "notes": string("Operator or agent notes."),
                    "tags": array("Tags for filtering runs.", {"type": "string"}),
                    "external_references": array("External references consulted by the agent.", {"type": "object", "additionalProperties": True}),
                    "metadata": {"type": "object", "description": "Additional structured metadata.", "additionalProperties": True},
                }
            ),
            wrap(
                "modpack.record_test_run",
                lambda args: modpack.record_test_run(
                    run_name=args.get("run_name"),
                    scenario=args.get("scenario"),
                    outcome=args.get("outcome"),
                    target=args.get("target"),
                    candidate=args.get("candidate"),
                    before_snapshot=args.get("before_snapshot"),
                    before_snapshot_path=args.get("before_snapshot_path"),
                    before_snapshot_id=args.get("before_snapshot_id"),
                    after_snapshot=args.get("after_snapshot"),
                    after_snapshot_path=args.get("after_snapshot_path"),
                    after_snapshot_id=args.get("after_snapshot_id"),
                    apply_result=args.get("apply_result"),
                    rollback_result=args.get("rollback_result"),
                    classification=args.get("classification"),
                    log_excerpt=args.get("log_excerpt"),
                    crash_excerpt=args.get("crash_excerpt"),
                    notes=args.get("notes"),
                    tags=args.get("tags"),
                    external_references=args.get("external_references"),
                    metadata=args.get("metadata"),
                ),
            ),
            output_schema=TEST_RUN_RECORD_OUTPUT_SCHEMA,
        ),
        Tool(
            "modpack.list_test_runs",
            "List saved modpack compatibility test run records from the modpack workspace.",
            schema(
                {
                    "limit": integer("Maximum number of runs to return.", default=20),
                    "outcome": string("Optional exact outcome filter."),
                    "scenario": string("Optional exact scenario filter."),
                    "tag": string("Optional tag filter."),
                }
            ),
            wrap(
                "modpack.list_test_runs",
                lambda args: modpack.list_test_runs(
                    limit=int(args.get("limit", 20)),
                    outcome=args.get("outcome"),
                    scenario=args.get("scenario"),
                    tag=args.get("tag"),
                ),
            ),
            output_schema=TEST_RUN_LIST_OUTPUT_SCHEMA,
        ),
        Tool(
            "modpack.get_test_run",
            "Read a saved modpack compatibility test run record by run id or workspace path.",
            schema(
                {
                    "run_id": string("Saved test run id under workspace/runs."),
                    "run_path": string("Path to test run JSON under MINECRAFT_OPS_MODPACK_WORKSPACE."),
                }
            ),
            wrap(
                "modpack.get_test_run",
                lambda args: modpack.get_test_run(run_id=args.get("run_id"), run_path=args.get("run_path")),
            ),
            output_schema=TEST_RUN_GET_OUTPUT_SCHEMA,
        ),
        Tool(
            "rcon.config.get",
            "Read RCON settings from the MCSManager instance config.",
            schema(id_props({"connection_host": string("Optional connection host override when rconIp is a bind or loopback address.")})),
            wrap("rcon.config.get", rcon_config_get),
        ),
        Tool(
            "rcon.config.set",
            "Update RCON settings in the MCSManager instance config. Requires confirm=true or dry_run=true.",
            confirm_schema(
                id_props(
                    {
                        "enabled": boolean("Enable or disable RCON."),
                        "rcon_ip": string("RCON bind IP stored in the MCSManager instance config."),
                        "rcon_port": integer("RCON port."),
                        "rcon_password": string("RCON password."),
                    }
                )
            ),
            wrap("rcon.config.set", rcon_config_set),
        ),
        Tool(
            "rcon.command",
            "Send a raw RCON command. Requires confirm=true or dry_run=true.",
            confirm_schema(id_props({"command": string("Single-line RCON command."), "connection_host": string("Optional connection host override.")}), ["command"]),
            wrap(
                "rcon.command",
                lambda args: _run_command_tool(
                    args,
                    "rcon.command",
                    {"backend": "rcon", "command": args.get("command")},
                    lambda command: rcon.command(command, rcon_runtime(args).connection()),
                    action,
                    require_str,
                    raw_command_policy,
                ),
            ),
        ),
        Tool(
            "rcon.list_players",
            "List online players through RCON using the fixed list command.",
            schema(id_props({"connection_host": string("Optional connection host override.")})),
            wrap("rcon.list_players", lambda args: rcon.list_players(rcon_runtime(args).connection())),
        ),
        Tool(
            "rcon.time_query",
            "Query server time through RCON using time query daytime/gametime/day.",
            schema(id_props({"query": enum("Time query type.", ["daytime", "gametime", "day"], default="daytime"), "connection_host": string("Optional connection host override.")})),
            wrap("rcon.time_query", lambda args: rcon.time_query(args.get("query", "daytime"), rcon_runtime(args).connection())),
        ),
        Tool(
            "rcon.save_all",
            "Save the world through RCON using save-all or save-all flush.",
            schema(id_props({"flush": boolean("Run save-all flush.", default=False), "connection_host": string("Optional connection host override.")})),
            wrap("rcon.save_all", lambda args: rcon.save_all(bool(args.get("flush", False)), rcon_runtime(args).connection())),
        ),
        Tool(
            "msmp.config.get",
            "Read MSMP management-server settings from server.properties through MCSManager.",
            schema(msmp_props()),
            wrap("msmp.config.get", msmp_config_get),
        ),
        Tool(
            "msmp.config.set",
            "Update MSMP management-server settings in server.properties through MCSManager. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props(
                    {
                        "enabled": boolean("Enable or disable the management server."),
                        "host": string("management-server-host value."),
                        "port": integer("management-server-port value."),
                        "secret": string("management-server-secret value. Minecraft 1.21.9 expects 40 alphanumeric characters."),
                        "tls_enabled": boolean("management-server-tls-enabled value."),
                    }
                )
            ),
            wrap("msmp.config.set", msmp_config_set),
        ),
        Tool(
            "msmp.discover",
            "Call rpc.discover on the Minecraft Server Management Protocol endpoint.",
            schema(msmp_props()),
            wrap("msmp.discover", lambda args: msmp.discover(msmp_runtime(args).connection())),
        ),
        Tool(
            "msmp.call",
            "Call an arbitrary MSMP JSON-RPC method. Set read_only=true for safe reads; otherwise confirm=true is required.",
            confirm_schema(
                msmp_props(
                    {
                    "method": string("MSMP method, for example minecraft:server/status."),
                    "params": {"description": "Raw JSON-RPC params array or object.", "type": ["array", "object", "string", "number", "boolean", "null"]},
                    "read_only": boolean("Declare this call as read-only to skip confirmation.", default=False),
                    }
                ),
                ["method"],
            ),
            wrap(
                "msmp.call",
                lambda args: action(
                    "msmp.call",
                    args,
                    {"backend": "msmp", "method": args.get("method"), "params": args.get("params")},
                    lambda: msmp_call(args, require_str(args, "method"), args.get("params")),
                ),
            ),
        ),
        Tool(
            "msmp.players.list",
            "List connected players through MSMP.",
            schema(msmp_props()),
            wrap("msmp.players.list", lambda args: msmp_call(args, "minecraft:players")),
        ),
        Tool(
            "msmp.players.kick",
            "Kick one or more players through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"players": array("Players to kick.", player_schema()), "message": string("Kick message.", default="")}), ["players"]),
            wrap(
                "msmp.players.kick",
                lambda args: action(
                    "msmp.players.kick",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(
                        args,
                        "minecraft:players/kick",
                        [
                            [
                                {
                                    "player": player,
                                    **({"message": {"literal": args["message"]}} if args.get("message") else {}),
                                }
                                for player in list_player_objects(args)
                            ]
                        ],
                    ),
                ),
            ),
        ),
        Tool(
            "msmp.server.status",
            "Get server status through MSMP.",
            schema(msmp_props()),
            wrap("msmp.server.status", lambda args: msmp_call(args, "minecraft:server/status")),
        ),
        Tool(
            "msmp.server.save",
            "Save server state through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"flush": boolean("Whether to flush data to disk.", default=True)})),
            wrap(
                "msmp.server.save",
                lambda args: action(
                    "msmp.server.save",
                    args,
                    {"backend": "msmp", "method": "minecraft:server/save", "flush": bool(args.get("flush", True))},
                    lambda: msmp_call(args, "minecraft:server/save", [bool(args.get("flush", True))]),
                ),
            ),
        ),
        Tool(
            "msmp.server.stop",
            "Stop server through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props()),
            wrap(
                "msmp.server.stop",
                lambda args: action(
                    "msmp.server.stop",
                    args,
                    {"backend": "msmp", "method": "minecraft:server/stop"},
                    lambda: msmp_call(args, "minecraft:server/stop"),
                ),
            ),
        ),
        Tool(
            "msmp.bans.get",
            "Get the player ban list through MSMP.",
            schema(msmp_props()),
            wrap("msmp.bans.get", lambda args: msmp_call(args, "minecraft:bans")),
        ),
        Tool(
            "msmp.bans.add",
            "Add player bans through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props({
                    "players": array("Players to ban.", player_schema()),
                    "reason": string("Optional ban reason."),
                    "source": string("Optional ban source."),
                    "expires": string("Optional expiration timestamp/string accepted by the server."),
                }),
                ["players"],
            ),
            wrap(
                "msmp.bans.add",
                lambda args: action(
                    "msmp.bans.add",
                    args,
                    {"backend": "msmp", "players": args.get("players"), "reason": args.get("reason")},
                    lambda: msmp_call(args, "minecraft:bans/add", [ban_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.bans.remove",
            "Remove player bans through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"players": array("Players to unban.", player_schema())}), ["players"]),
            wrap(
                "msmp.bans.remove",
                lambda args: action(
                    "msmp.bans.remove",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:bans/remove", [list_player_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.bans.set",
            "Replace the player ban list through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props({
                    "players": array("Full player ban list.", player_schema()),
                    "reason": string("Optional ban reason applied to every entry."),
                    "source": string("Optional ban source applied to every entry."),
                    "expires": string("Optional expiration applied to every entry."),
                }),
                ["players"],
            ),
            wrap(
                "msmp.bans.set",
                lambda args: action(
                    "msmp.bans.set",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:bans/set", [ban_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.bans.clear",
            "Clear the player ban list through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props()),
            wrap(
                "msmp.bans.clear",
                lambda args: action(
                    "msmp.bans.clear",
                    args,
                    {"backend": "msmp"},
                    lambda: msmp_call(args, "minecraft:bans/clear"),
                ),
            ),
        ),
        Tool(
            "msmp.ip_bans.get",
            "Get the IP ban list through MSMP.",
            schema(msmp_props()),
            wrap("msmp.ip_bans.get", lambda args: msmp_call(args, "minecraft:ip_bans")),
        ),
        Tool(
            "msmp.ip_bans.add",
            "Add IP bans through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props({
                    "ips": array("IP addresses to ban.", {"type": "string"}),
                    "reason": string("Optional ban reason."),
                    "source": string("Optional ban source."),
                    "expires": string("Optional expiration timestamp/string accepted by the server."),
                }),
                ["ips"],
            ),
            wrap(
                "msmp.ip_bans.add",
                lambda args: action(
                    "msmp.ip_bans.add",
                    args,
                    {"backend": "msmp", "ips": args.get("ips"), "reason": args.get("reason")},
                    lambda: msmp_call(args, "minecraft:ip_bans/add", [ip_ban_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.ip_bans.remove",
            "Remove IP bans through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"ips": array("IP addresses to unban.", {"type": "string"})}), ["ips"]),
            wrap(
                "msmp.ip_bans.remove",
                lambda args: action(
                    "msmp.ip_bans.remove",
                    args,
                    {"backend": "msmp", "ips": args.get("ips")},
                    lambda: msmp_call(args, "minecraft:ip_bans/remove", [require_list(args, "ips")]),
                ),
            ),
        ),
        Tool(
            "msmp.ip_bans.set",
            "Replace the IP ban list through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props({
                    "ips": array("Full IP ban list.", {"type": "string"}),
                    "reason": string("Optional ban reason applied to every entry."),
                    "source": string("Optional ban source applied to every entry."),
                    "expires": string("Optional expiration applied to every entry."),
                }),
                ["ips"],
            ),
            wrap(
                "msmp.ip_bans.set",
                lambda args: action(
                    "msmp.ip_bans.set",
                    args,
                    {"backend": "msmp", "ips": args.get("ips")},
                    lambda: msmp_call(args, "minecraft:ip_bans/set", [ip_ban_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.ip_bans.clear",
            "Clear the IP ban list through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props()),
            wrap(
                "msmp.ip_bans.clear",
                lambda args: action(
                    "msmp.ip_bans.clear",
                    args,
                    {"backend": "msmp"},
                    lambda: msmp_call(args, "minecraft:ip_bans/clear"),
                ),
            ),
        ),
        Tool(
            "msmp.allowlist.get",
            "Get the allowlist through MSMP.",
            schema(msmp_props()),
            wrap("msmp.allowlist.get", lambda args: msmp_call(args, "minecraft:allowlist")),
        ),
        Tool(
            "msmp.allowlist.add",
            "Add players to the allowlist through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"players": array("Players to add.", player_schema())}), ["players"]),
            wrap(
                "msmp.allowlist.add",
                lambda args: action(
                    "msmp.allowlist.add",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:allowlist/add", [list_player_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.allowlist.remove",
            "Remove players from the allowlist through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"players": array("Players to remove.", player_schema())}), ["players"]),
            wrap(
                "msmp.allowlist.remove",
                lambda args: action(
                    "msmp.allowlist.remove",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:allowlist/remove", [list_player_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.allowlist.set",
            "Replace the allowlist through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"players": array("Full allowlist players.", player_schema())}), ["players"]),
            wrap(
                "msmp.allowlist.set",
                lambda args: action(
                    "msmp.allowlist.set",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:allowlist/set", [list_player_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.allowlist.clear",
            "Clear the allowlist through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props()),
            wrap(
                "msmp.allowlist.clear",
                lambda args: action(
                    "msmp.allowlist.clear",
                    args,
                    {"backend": "msmp"},
                    lambda: msmp_call(args, "minecraft:allowlist/clear"),
                ),
            ),
        ),
        Tool(
            "msmp.operators.get",
            "Get operators through MSMP.",
            schema(msmp_props()),
            wrap("msmp.operators.get", lambda args: msmp_call(args, "minecraft:operators")),
        ),
        Tool(
            "msmp.operators.add",
            "Add operators through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props({
                    "players": array("Players to op.", player_schema()),
                    "permission_level": integer("Operator permission level.", default=4),
                    "bypasses_player_limit": boolean("Whether ops bypass the player limit.", default=False),
                }),
                ["players"],
            ),
            wrap(
                "msmp.operators.add",
                lambda args: action(
                    "msmp.operators.add",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:operators/add", [operator_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.operators.remove",
            "Remove operators through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"players": array("Players to deop.", player_schema())}), ["players"]),
            wrap(
                "msmp.operators.remove",
                lambda args: action(
                    "msmp.operators.remove",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:operators/remove", [list_player_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.operators.set",
            "Replace the operator list through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props({
                    "players": array("Full operator list players.", player_schema()),
                    "permission_level": integer("Operator permission level.", default=4),
                    "bypasses_player_limit": boolean("Whether ops bypass the player limit.", default=False),
                }),
                ["players"],
            ),
            wrap(
                "msmp.operators.set",
                lambda args: action(
                    "msmp.operators.set",
                    args,
                    {"backend": "msmp", "players": args.get("players")},
                    lambda: msmp_call(args, "minecraft:operators/set", [operator_objects(args)]),
                ),
            ),
        ),
        Tool(
            "msmp.operators.clear",
            "Clear operators through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props()),
            wrap(
                "msmp.operators.clear",
                lambda args: action(
                    "msmp.operators.clear",
                    args,
                    {"backend": "msmp"},
                    lambda: msmp_call(args, "minecraft:operators/clear"),
                ),
            ),
        ),
        Tool(
            "msmp.gamerules.get",
            "Get game rules through MSMP.",
            schema(msmp_props()),
            wrap("msmp.gamerules.get", lambda args: msmp_call(args, "minecraft:gamerules")),
        ),
        Tool(
            "msmp.gamerules.update",
            "Update a game rule through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(msmp_props({"rule": string("Game rule key."), "value": {"description": "New value.", "type": ["boolean", "number", "string"]}}), ["rule", "value"]),
            wrap(
                "msmp.gamerules.update",
                lambda args: action(
                    "msmp.gamerules.update",
                    args,
                    {"backend": "msmp", "rule": args.get("rule"), "value": args.get("value")},
                    lambda: msmp_call(
                        args,
                        "minecraft:gamerules/update",
                        [{"key": require_str(args, "rule"), "value": game_rule_value(args.get("value"))}],
                    ),
                ),
            ),
        ),
        Tool(
            "msmp.server_settings.get",
            "Get one server setting through MSMP, for example difficulty or motd.",
            schema(msmp_props({"setting": string("Setting name after minecraft:serversettings/, for example difficulty.")}), ["setting"]),
            wrap("msmp.server_settings.get", lambda args: msmp_call(args, f"minecraft:serversettings/{require_str(args, 'setting')}")),
        ),
        Tool(
            "msmp.server_settings.list",
            "Discover readable and writable MSMP server settings from rpc.discover.",
            schema(msmp_props()),
            wrap("msmp.server_settings.list", msmp_server_settings_list),
        ),
        Tool(
            "msmp.server_settings.set",
            "Set one server setting through MSMP. Requires confirm=true or dry_run=true.",
            confirm_schema(
                msmp_props({
                    "setting": string("Setting name after minecraft:serversettings/, for example difficulty."),
                    "value": {"description": "New value.", "type": ["boolean", "number", "string"]},
                }),
                ["setting", "value"],
            ),
            wrap("msmp.server_settings.set", set_server_setting),
        ),
    ]
    return _with_tool_metadata(tools)


def _with_tool_metadata(tools: list[Tool]) -> list[Tool]:
    return [
        Tool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            handler=tool.handler,
            title=tool.title or _tool_title(tool.name),
            output_schema=tool.output_schema or GENERIC_TOOL_OUTPUT_SCHEMA,
            annotations=tool.annotations or _tool_annotations(tool.name),
        )
        for tool in tools
    ]


def _tool_title(name: str) -> str:
    words = name.replace("_", " ").replace(".", " ").split()
    return " ".join(word.upper() if word in {"rcon", "msmp"} else word.capitalize() for word in words)


def _tool_annotations(name: str) -> dict:
    high_risk = name in HIGH_RISK_TOOLS
    read_only = (
        name.startswith(("server.get_", "server.list_", "file.list", "file.read", "msmp.discover"))
        or name.endswith((".get", ".list", ".status"))
        or name
        in {
            "rcon.list_players",
            "rcon.time_query",
            "resources.list",
            "rcon.config.get",
            "modpack.inspect_jar",
            "modpack.diff_snapshots",
            "msmp.config.get",
            "modpack.classify_startup_result",
            "modpack.list_test_runs",
            "modpack.get_test_run",
        }
    )
    return {
        "title": _tool_title(name),
        "readOnlyHint": read_only and not high_risk,
        "destructiveHint": high_risk and any(part in name for part in ("delete", "clear", "kill", "stop", "reinstall", "uncompress")),
        "idempotentHint": read_only or name.endswith((".get", ".list", ".status")),
        "openWorldHint": True,
    }


GENERIC_TOOL_OUTPUT_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": True,
}

STARTUP_CLASSIFICATION_OUTPUT_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["kind", "status", "category", "confidence", "summary"],
    "properties": {
        "schemaVersion": {"type": "integer"},
        "kind": {"const": "modpackStartupClassification"},
        "status": {"type": "string", "enum": ["success", "failure", "unknown"]},
        "category": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "matchedCategories": {"type": "array", "items": {"type": "string"}},
        "signatures": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "recommendedNext": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "summary": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": True,
}

TEST_RUN_RECORD_OUTPUT_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["kind", "runId", "runPath", "record", "summary"],
    "properties": {
        "schemaVersion": {"type": "integer"},
        "kind": {"const": "modpackTestRunRecordResult"},
        "runId": {"type": "string"},
        "runPath": {"type": "string"},
        "record": {"type": "object", "additionalProperties": True},
        "summary": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": True,
}

TEST_RUN_LIST_OUTPUT_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["kind", "runs", "count"],
    "properties": {
        "schemaVersion": {"type": "integer"},
        "kind": {"const": "modpackTestRunList"},
        "runs": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "count": {"type": "integer"},
        "limit": {"type": "integer"},
        "workspace": {"type": "string"},
    },
    "additionalProperties": True,
}

TEST_RUN_GET_OUTPUT_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["kind", "run", "summary"],
    "properties": {
        "schemaVersion": {"type": "integer"},
        "kind": {"const": "modpackTestRun"},
        "run": {"type": "object", "additionalProperties": True},
        "summary": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": True,
}


SERVER_SETTING_TYPES: dict[str, set[str] | str] = {
    "difficulty": {"peaceful", "easy", "normal", "hard"},
    "game_mode": {"survival", "creative", "adventure", "spectator"},
    "force_game_mode": "bool",
    "hardcore": "bool",
    "pvp": "bool",
    "spawn_monsters": "bool",
    "spawn_animals": "bool",
    "spawn_npcs": "bool",
    "allow_flight": "bool",
    "use_allowlist": "bool",
    "enforce_allowlist": "bool",
    "hide_online_players": "bool",
    "online_mode": "bool",
    "prevent_proxy_connections": "bool",
    "max_players": "int",
    "view_distance": "int",
    "simulation_distance": "int",
    "spawn_protection": "int",
    "player_idle_timeout": "int",
    "max_world_size": "int",
    "entity_broadcast_range_percentage": "int",
    "function_permission_level": "int",
    "operator_permission_level": "int",
}


def deep_merge_dict(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def shallow_diff(before: dict, after: dict) -> dict:
    diff: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            diff[key] = {"before": old, "after": new}
    return diff


def redact_sensitive_diff(diff: dict) -> dict:
    redacted: dict[str, Any] = {}
    for key, value in diff.items():
        if any(part in str(key).lower() for part in ("apikey", "api_key", "password", "secret", "token")):
            redacted[key] = {"before": "<redacted>", "after": "<redacted>"}
        else:
            redacted[key] = value
    return redacted


def _collect_msmp_methods(value: Any) -> set[str]:
    methods: set[str] = set()
    if isinstance(value, str):
        if value == "rpc.discover" or value.startswith("minecraft:"):
            methods.add(value)
    elif isinstance(value, list):
        for item in value:
            methods.update(_collect_msmp_methods(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and (key == "rpc.discover" or key.startswith("minecraft:")):
                methods.add(key)
            methods.update(_collect_msmp_methods(item))
    return methods


def _jsonable_setting_type(value: set[str] | str | None) -> list[str] | str | None:
    if isinstance(value, set):
        return sorted(value)
    return value


def _run_command_tool(
    args: dict,
    tool_name: str,
    preview: dict,
    run: Callable[[str], Any],
    action: Callable[[str, dict, dict, Callable[[], Any]], Any],
    require_str: Callable[[dict, str], str],
    command_policy: Callable[[str], None] | None = None,
) -> Any:
    command = require_str(args, "command")
    ensure_plain_command(command)
    if command_policy is not None:
        command_policy(command)
    return action(tool_name, args, preview, lambda: run(command))


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def confirm_schema(properties: dict, required: list[str] | None = None) -> dict:
    merged = {
        **properties,
        "confirm": boolean("Required true for high-risk operations.", default=False),
        "dry_run": boolean("Return a preview without executing the operation.", default=False),
    }
    return schema(merged, required or [])


def id_props(extra: dict | None = None) -> dict:
    props = {
        "daemonId": string("MCSManager daemon id. Uses MCSM_DEFAULT_DAEMON_ID if omitted."),
        "uuid": string("MCSManager instance UUID. Uses MCSM_DEFAULT_INSTANCE_UUID if omitted."),
    }
    if extra:
        props.update(extra)
    return props


def id_schema() -> dict:
    return schema(id_props())


def backend_props(extra: dict | None = None) -> dict:
    props = {"backend": enum("Backend selection.", ["auto", "msmp", "rcon", "mcsm"], default="auto")}
    if extra:
        props.update(extra)
    return props


def id_preview(config: AppConfig, args: dict) -> dict:
    return {
        "daemonId": args.get("daemonId") or config.mcsm.default_daemon_id or "<missing>",
        "uuid": args.get("uuid") or config.mcsm.default_instance_uuid or "<missing>",
    }


def string(description: str, default: str | None = None) -> dict:
    item = {"type": "string", "description": description}
    if default is not None:
        item["default"] = default
    return item


def integer(description: str, default: int | None = None) -> dict:
    item = {"type": "integer", "description": description}
    if default is not None:
        item["default"] = default
    return item


def boolean(description: str, default: bool | None = None) -> dict:
    item = {"type": "boolean", "description": description}
    if default is not None:
        item["default"] = default
    return item


def enum(description: str, values: list[str], default: str | None = None) -> dict:
    item = {"type": "string", "description": description, "enum": values}
    if default is not None:
        item["default"] = default
    return item


def array(description: str, items: dict) -> dict:
    return {"type": "array", "description": description, "items": items}


def pair_array(description: str) -> dict:
    return {
        "type": "array",
        "description": description,
        "items": {
            "type": "array",
            "prefixItems": [{"type": "string"}, {"type": "string"}],
            "minItems": 2,
            "maxItems": 2,
        },
    }


def player_schema() -> dict:
    return {
        "anyOf": [
            {"type": "string", "description": "Player username."},
            schema(
                {
                    "name": string("Player username."),
                    "id": string("Player UUID."),
                }
            ),
        ]
    }


def tools_as_json(tools: list[Tool]) -> str:
    return json.dumps(
        [
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema,
                "annotations": tool.annotations,
            }
            for tool in tools
        ],
        ensure_ascii=False,
        indent=2,
    )
