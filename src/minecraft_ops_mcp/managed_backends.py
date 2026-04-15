from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .adapters.msmp import MsmpConnection
from .adapters.rcon import RconConnection
from .errors import OpsError


_BIND_OR_LOOPBACK_HOSTS = {"", "0.0.0.0", "::", "::0", "localhost", "127.0.0.1"}


@dataclass(frozen=True)
class RconRuntimeConfig:
    enabled: bool
    configured_host: str
    host: str
    port: int
    password: str
    timeout_seconds: float
    encoding: str

    def connection(self) -> RconConnection:
        if not self.enabled:
            raise OpsError("RCON is disabled in the MCSManager instance config.")
        if not self.password:
            raise OpsError("RCON password is empty in the MCSManager instance config.")
        return RconConnection(
            host=self.host,
            port=self.port,
            password=self.password,
            timeout_seconds=self.timeout_seconds,
            encoding=self.encoding,
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configuredHost": self.configured_host,
            "connectionHost": self.host,
            "port": self.port,
            "passwordSet": bool(self.password),
            "source": "mcsm.instance.config",
        }


@dataclass(frozen=True)
class MsmpRuntimeConfig:
    enabled: bool
    configured_host: str
    host: str
    port: int
    secret: str
    tls_enabled: bool
    timeout_seconds: float
    tls_verify: bool
    properties: dict[str, str]

    def connection(self) -> MsmpConnection:
        if not self.enabled:
            raise OpsError("MSMP is disabled in server.properties.")
        scheme = "wss" if self.tls_enabled else "ws"
        return MsmpConnection(
            url=f"{scheme}://{self.host}:{self.port}",
            secret=self.secret,
            timeout_seconds=self.timeout_seconds,
            tls_verify=self.tls_verify,
        )

    def redacted(self) -> dict[str, Any]:
        scheme = "wss" if self.tls_enabled else "ws"
        return {
            "enabled": self.enabled,
            "configuredHost": self.configured_host,
            "connectionHost": self.host,
            "port": self.port,
            "url": f"{scheme}://{self.host}:{self.port}",
            "secretSet": bool(self.secret),
            "tlsEnabled": self.tls_enabled,
            "tlsVerify": self.tls_verify,
            "source": "mcsm.file.server_properties",
        }


def rcon_runtime_config(
    instance_config: dict[str, Any],
    *,
    mcsm_base_url: str,
    timeout_seconds: float,
    encoding: str,
    connection_host: str | None = None,
) -> RconRuntimeConfig:
    configured_host = str(instance_config.get("rconIp") or "")
    return RconRuntimeConfig(
        enabled=_bool_value(instance_config.get("enableRcon"), False),
        configured_host=configured_host,
        host=derive_connection_host(configured_host, mcsm_base_url, connection_host),
        port=_int_value(instance_config.get("rconPort"), 25575),
        password=str(instance_config.get("rconPassword") or ""),
        timeout_seconds=timeout_seconds,
        encoding=encoding,
    )


def msmp_runtime_config(
    properties_text: str,
    *,
    mcsm_base_url: str,
    timeout_seconds: float,
    tls_verify: bool,
    connection_host: str | None = None,
) -> MsmpRuntimeConfig:
    properties = parse_properties(properties_text)
    configured_host = properties.get("management-server-host", "")
    return MsmpRuntimeConfig(
        enabled=_bool_value(properties.get("management-server-enabled"), False),
        configured_host=configured_host,
        host=derive_connection_host(configured_host, mcsm_base_url, connection_host),
        port=_int_value(properties.get("management-server-port"), 25585),
        secret=properties.get("management-server-secret", ""),
        tls_enabled=_bool_value(properties.get("management-server-tls-enabled"), False),
        timeout_seconds=timeout_seconds,
        tls_verify=tls_verify,
        properties=properties,
    )


def parse_properties(text: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
        elif ":" in stripped:
            key, value = stripped.split(":", 1)
        else:
            continue
        properties[key.strip()] = value.strip()
    return properties


def update_properties_text(text: str, updates: dict[str, str]) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            output.append(line)
            continue
        separator = "=" if "=" in line else ":" if ":" in line else ""
        if not separator:
            output.append(line)
            continue
        key = line.split(separator, 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    suffix = "\n" if text.endswith("\n") or output else ""
    return "\n".join(output) + suffix


def extract_text_response(response: Any) -> str:
    if isinstance(response, str):
        return response
    data = response.get("data", response) if isinstance(response, dict) else response
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("content", "text", "value", "file", "data"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    raise OpsError("MCSManager file response did not contain text.")


def validate_msmp_secret(secret: str) -> None:
    if not secret:
        return
    if not re.fullmatch(r"[A-Za-z0-9]{40}", secret):
        raise OpsError("management-server-secret must be 40 alphanumeric characters.")


def derive_connection_host(configured_host: str, mcsm_base_url: str, connection_host: str | None = None) -> str:
    if connection_host:
        return connection_host
    host = configured_host.strip()
    if host and not _is_bind_or_loopback_host(host):
        return host
    parsed = urlparse(mcsm_base_url)
    if parsed.hostname:
        return parsed.hostname
    return host or "127.0.0.1"


def _is_bind_or_loopback_host(host: str) -> bool:
    lowered = host.strip().lower()
    return lowered in _BIND_OR_LOOPBACK_HOSTS or lowered.startswith("127.")


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_value(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OpsError(f"Invalid integer value: {value}") from exc
