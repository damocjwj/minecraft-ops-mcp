from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import AppConfig


_SENSITIVE_KEY_PARTS = ("apikey", "api_key", "password", "secret", "token")
_SENSITIVE_LINE_MARKERS = ("password=", "secret=", "rcon.password", "management-server-secret")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_string(value: str) -> str:
    redacted_lines: list[str] = []
    changed = False
    for line in value.splitlines(keepends=True):
        lower = line.lower()
        if any(marker in lower for marker in _SENSITIVE_LINE_MARKERS):
            prefix = line.split("=", 1)[0] if "=" in line else "<sensitive>"
            suffix = "\n" if line.endswith("\n") else ""
            redacted_lines.append(f"{prefix}=<redacted>{suffix}")
            changed = True
        else:
            redacted_lines.append(line)
    return "".join(redacted_lines) if changed else value


def audit(config: AppConfig, tool_name: str, args: dict, outcome: str, error: str | None = None) -> None:
    if not config.audit_log:
        return
    record = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool_name,
        "args": _redact(args),
        "outcome": outcome,
    }
    if error:
        record["error"] = error
    try:
        path = Path(config.audit_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        # Audit logging should never break the MCP server.
        return
