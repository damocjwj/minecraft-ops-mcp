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


class RconClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config.rcon

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise ConfigError("RCON is not configured. Set RCON_HOST and RCON_PASSWORD.")

    def command(self, command: str) -> dict:
        self._require_enabled()
        with socket.create_connection(
            (self.config.host, self.config.port),
            timeout=self.config.timeout_seconds,
        ) as sock:
            sock.settimeout(self.config.timeout_seconds)
            self._send(sock, 1, SERVERDATA_AUTH, self.config.password)
            auth_ok = False
            for _ in range(4):
                packet = self._recv(sock)
                if packet.request_id == -1:
                    raise OpsError("RCON authentication failed.")
                if packet.request_id == 1 and packet.packet_type == SERVERDATA_AUTH_RESPONSE:
                    auth_ok = True
                    break
            if not auth_ok:
                raise OpsError("RCON authentication response was not received.")
            self._send(sock, 2, SERVERDATA_EXECCOMMAND, command)
            self._send(sock, 3, SERVERDATA_EXECCOMMAND, "")
            chunks: list[str] = []
            while True:
                try:
                    packet = self._recv(sock)
                except socket.timeout:
                    break
                if packet.request_id == 2:
                    chunks.append(packet.body)
                if packet.request_id == 3:
                    break
            return {"command": command, "response": "".join(chunks)}

    def list_players(self) -> dict:
        return self.command("list")

    def time_query(self, query: str = "daytime") -> dict:
        if query not in {"daytime", "gametime", "day"}:
            raise OpsError("time query must be one of: daytime, gametime, day.")
        return self.command(f"time query {query}")

    def save_all(self, flush: bool = False) -> dict:
        command = "save-all flush" if flush else "save-all"
        return self.command(command)

    def _send(self, sock: socket.socket, request_id: int, packet_type: int, body: str) -> None:
        encoded = body.encode(self.config.encoding, errors="replace")
        payload = struct.pack("<ii", request_id, packet_type) + encoded + b"\x00\x00"
        sock.sendall(struct.pack("<i", len(payload)) + payload)

    def _recv(self, sock: socket.socket) -> RconPacket:
        length_raw = self._recv_exact(sock, 4)
        length = struct.unpack("<i", length_raw)[0]
        if length < 10:
            raise OpsError(f"Invalid RCON packet length: {length}")
        payload = self._recv_exact(sock, length)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode(self.config.encoding, errors="replace")
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
