from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import websocket

from ..config import AppConfig
from ..errors import ConfigError, OpsError


@dataclass(frozen=True)
class MsmpConnection:
    url: str
    secret: str = ""
    timeout_seconds: float = 8.0
    tls_verify: bool = True


class MsmpClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config.msmp

    def _connection(self, connection: MsmpConnection | None) -> MsmpConnection:
        if connection is None:
            raise ConfigError("MSMP connection is not configured for this call. Read it from MCSManager server.properties first.")
        return connection

    def call(self, method: str, params: Any | None = None, connection: MsmpConnection | None = None) -> Any:
        return _WebSocketJsonRpc(self._connection(connection)).call(method, params)

    def discover(self, connection: MsmpConnection | None = None) -> Any:
        return self.call("rpc.discover", connection=connection)


class _WebSocketJsonRpc:
    def __init__(self, config: MsmpConnection) -> None:
        self.config = config

    def call(self, method: str, params: Any | None = None) -> Any:
        parsed = urlparse(self.config.url)
        if parsed.scheme not in {"ws", "wss"}:
            raise ConfigError("MSMP connection URL must start with ws:// or wss://.")
        if not parsed.hostname:
            raise ConfigError("MSMP connection URL is missing a host.")

        headers: list[str] = []
        if self.config.secret:
            headers.append(f"Authorization: Bearer {self.config.secret}")

        sslopt: dict[str, Any] | None = None
        if parsed.scheme == "wss" and not self.config.tls_verify:
            sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

        request_id = 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        try:
            ws = websocket.create_connection(
                self.config.url,
                timeout=self.config.timeout_seconds,
                header=headers,
                sslopt=sslopt,
            )
        except websocket.WebSocketException as exc:
            raise OpsError(f"MSMP WebSocket connection failed: {exc}") from exc
        except OSError as exc:
            raise OpsError(f"MSMP WebSocket connection failed: {exc}") from exc

        try:
            ws.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            while True:
                raw_message = ws.recv()
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                data = json.loads(raw_message)
                if data.get("id") != request_id:
                    continue
                if "error" in data:
                    raise OpsError(f"MSMP JSON-RPC error: {data['error']}")
                return data.get("result")
        except json.JSONDecodeError as exc:
            raise OpsError(f"MSMP returned invalid JSON-RPC data: {exc}") from exc
        except websocket.WebSocketException as exc:
            raise OpsError(f"MSMP WebSocket request failed: {exc}") from exc
        finally:
            ws.close()
