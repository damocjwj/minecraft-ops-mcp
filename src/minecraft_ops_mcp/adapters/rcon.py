from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from ..config import AppConfig
from ..errors import ConfigError, OpsError


SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2


@dataclass(frozen=True)
class RconPacket:
    request_id: int
    packet_type: int
    body: str


@dataclass(frozen=True)
class RconConnection:
    host: str
    port: int
    password: str
    timeout_seconds: float
    encoding: str


class RconClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config.rcon

    def _connection(self, connection: RconConnection | None) -> RconConnection:
        if connection is None:
            raise ConfigError("RCON connection is not configured for this call. Read it from MCSManager instance config first.")
        return connection

    def command(self, command: str, connection: RconConnection | None = None) -> dict:
        target = self._connection(connection)
        with socket.create_connection(
            (target.host, target.port),
            timeout=target.timeout_seconds,
        ) as sock:
            sock.settimeout(target.timeout_seconds)
            self._send(sock, 1, SERVERDATA_AUTH, target.password, target.encoding)
            auth_ok = False
            for _ in range(4):
                packet = self._recv(sock, target.encoding)
                if packet.request_id == -1:
                    raise OpsError("RCON authentication failed.")
                if packet.request_id == 1 and packet.packet_type == SERVERDATA_AUTH_RESPONSE:
                    auth_ok = True
                    break
            if not auth_ok:
                raise OpsError("RCON authentication response was not received.")
            self._send(sock, 2, SERVERDATA_EXECCOMMAND, command, target.encoding)
            self._send(sock, 3, SERVERDATA_EXECCOMMAND, "", target.encoding)
            chunks: list[str] = []
            while True:
                try:
                    packet = self._recv(sock, target.encoding)
                except socket.timeout:
                    break
                if packet.request_id == 2:
                    chunks.append(packet.body)
                if packet.request_id == 3:
                    break
            return {"command": command, "response": "".join(chunks)}

    def list_players(self, connection: RconConnection | None = None) -> dict:
        return self.command("list", connection)

    def time_query(self, query: str = "daytime", connection: RconConnection | None = None) -> dict:
        if query not in {"daytime", "gametime", "day"}:
            raise OpsError("time query must be one of: daytime, gametime, day.")
        return self.command(f"time query {query}", connection)

    def save_all(self, flush: bool = False, connection: RconConnection | None = None) -> dict:
        command = "save-all flush" if flush else "save-all"
        return self.command(command, connection)

    def _send(self, sock: socket.socket, request_id: int, packet_type: int, body: str, encoding: str) -> None:
        encoded = body.encode(encoding, errors="replace")
        payload = struct.pack("<ii", request_id, packet_type) + encoded + b"\x00\x00"
        sock.sendall(struct.pack("<i", len(payload)) + payload)

    def _recv(self, sock: socket.socket, encoding: str) -> RconPacket:
        length_raw = self._recv_exact(sock, 4)
        length = struct.unpack("<i", length_raw)[0]
        if length < 10:
            raise OpsError(f"Invalid RCON packet length: {length}")
        payload = self._recv_exact(sock, length)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode(encoding, errors="replace")
        return RconPacket(request_id, packet_type, body)

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = sock.recv(remaining)
            if not chunk:
                raise OpsError("RCON connection closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
