from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_csv(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class McsmConfig:
    base_url: str = ""
    api_key: str = ""
    default_daemon_id: str = ""
    default_instance_uuid: str = ""
    timeout_seconds: float = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass(frozen=True)
class RconConfig:
    timeout_seconds: float = 5.0
    encoding: str = "utf-8"


@dataclass(frozen=True)
class MsmpConfig:
    timeout_seconds: float = 8.0
    tls_verify: bool = True


@dataclass(frozen=True)
class AppConfig:
    mcsm: McsmConfig
    rcon: RconConfig
    msmp: MsmpConfig
    audit_log: str = "/tmp/minecraft-ops-mcp-audit.jsonl"
    allow_raw_commands: bool = False
    raw_command_allowlist: tuple[str, ...] = ()
    raw_command_denylist: tuple[str, ...] = ()
    max_bytes: int = 256 * 1024 * 1024
    upload_allowed_dirs: tuple[str, ...] = ()
    file_operation_whitelist: tuple[str, ...] = ()
    upload_url_allowed_domains: tuple[str, ...] = ()
    modpack_workspace: str = "/tmp/minecraft-ops-mcp-modpacks"

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            mcsm=McsmConfig(
                base_url=os.getenv("MCSM_BASE_URL", "").rstrip("/"),
                api_key=os.getenv("MCSM_API_KEY", ""),
                default_daemon_id=os.getenv("MCSM_DEFAULT_DAEMON_ID", ""),
                default_instance_uuid=os.getenv("MCSM_DEFAULT_INSTANCE_UUID", ""),
                timeout_seconds=_env_float("MCSM_TIMEOUT_SECONDS", 10.0),
            ),
            rcon=RconConfig(
                timeout_seconds=_env_float("MINECRAFT_OPS_RCON_TIMEOUT_SECONDS", 5.0),
                encoding=os.getenv("MINECRAFT_OPS_RCON_ENCODING", "utf-8"),
            ),
            msmp=MsmpConfig(
                timeout_seconds=_env_float("MINECRAFT_OPS_MSMP_TIMEOUT_SECONDS", 8.0),
                tls_verify=_env_bool("MINECRAFT_OPS_MSMP_TLS_VERIFY", True),
            ),
            audit_log=os.getenv("MINECRAFT_OPS_AUDIT_LOG", "/tmp/minecraft-ops-mcp-audit.jsonl"),
            allow_raw_commands=_env_bool("MINECRAFT_OPS_ALLOW_RAW_COMMANDS", False),
            raw_command_allowlist=_env_csv("MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST"),
            raw_command_denylist=_env_csv("MINECRAFT_OPS_RAW_COMMAND_DENYLIST"),
            max_bytes=_env_int("MINECRAFT_OPS_MAX_BYTES", 256 * 1024 * 1024),
            upload_allowed_dirs=_env_csv("MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS"),
            file_operation_whitelist=_env_csv("MINECRAFT_OPS_FILE_OPERATION_WHITELIST"),
            upload_url_allowed_domains=_env_csv("MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS"),
            modpack_workspace=os.getenv("MINECRAFT_OPS_MODPACK_WORKSPACE", "/tmp/minecraft-ops-mcp-modpacks"),
        )

    def redacted(self) -> dict:
        return {
            "mcsm": {
                "enabled": self.mcsm.enabled,
                "base_url": self.mcsm.base_url,
                "api_key_set": bool(self.mcsm.api_key),
                "default_daemon_id_set": bool(self.mcsm.default_daemon_id),
                "default_instance_uuid_set": bool(self.mcsm.default_instance_uuid),
            },
            "rcon": {
                "managed_by_mcsm": True,
                "timeout_seconds": self.rcon.timeout_seconds,
                "encoding": self.rcon.encoding,
            },
            "msmp": {
                "managed_by_mcsm": True,
                "timeout_seconds": self.msmp.timeout_seconds,
                "tls_verify": self.msmp.tls_verify,
            },
            "audit_log": self.audit_log,
            "allow_raw_commands": self.allow_raw_commands,
            "raw_command_allowlist": list(self.raw_command_allowlist),
            "raw_command_denylist": list(self.raw_command_denylist),
            "max_bytes": self.max_bytes,
            "upload_allowed_dirs": list(self.upload_allowed_dirs),
            "file_operation_whitelist": list(self.file_operation_whitelist),
            "upload_url_allowed_domains": list(self.upload_url_allowed_domains),
            "modpack_workspace": self.modpack_workspace,
        }
