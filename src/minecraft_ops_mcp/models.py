from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


JSON = dict[str, Any]
Handler = Callable[[JSON], Any]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: JSON
    handler: Handler
    title: str | None = None
    output_schema: JSON | None = None
    annotations: JSON | None = None


@dataclass(frozen=True)
class Resource:
    uri: str
    name: str
    description: str
    mime_type: str
    read: Callable[[], str]
    title: str | None = None


@dataclass(frozen=True)
class Prompt:
    name: str
    description: str
    arguments: list[JSON]
    get: Callable[[JSON], list[JSON]]
    title: str | None = None
