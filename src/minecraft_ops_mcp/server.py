from __future__ import annotations

import json
import sys
from typing import Any

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from . import __version__
from .config import AppConfig
from .errors import OpsError
from .models import Prompt, Resource, Tool
from .policy import HIGH_RISK_TOOLS
from .tools import make_tools, tools_as_json


DEFAULT_PAGE_SIZE = 100


def make_resources(config: AppConfig, tools_json: str) -> list[Resource]:
    return [
        Resource(
            uri="minecraft-ops://config",
            name="Redacted configuration",
            title="Redacted Configuration",
            description="Shows which backends are configured without exposing secrets.",
            mime_type="application/json",
            read=lambda: json.dumps(config.redacted(), ensure_ascii=False, indent=2),
        ),
        Resource(
            uri="minecraft-ops://safety",
            name="Safety policy",
            title="Safety Policy",
            description="Lists high-risk tools and confirmation semantics.",
            mime_type="application/json",
            read=lambda: json.dumps(
                {
                    "highRiskTools": sorted(HIGH_RISK_TOOLS),
                    "rule": "High-risk tools require confirm=true, or dry_run=true for a non-executing preview.",
                    "msmpReadOnly": "msmp.call only skips confirmation for recognized read-only MSMP methods.",
                    "auditLog": config.audit_log,
                    "rawCommands": "Raw console and RCON commands must be single-line.",
                    "rawCommandAllowlist": list(config.raw_command_allowlist),
                    "rawCommandDenylist": list(config.raw_command_denylist),
                },
                ensure_ascii=False,
                indent=2,
            ),
        ),
        Resource(
            uri="minecraft-ops://tools",
            name="Tool catalog",
            title="Tool Catalog",
            description="Full MCP tool catalog with JSON schemas.",
            mime_type="application/json",
            read=lambda: tools_json,
        ),
    ]


def make_prompts() -> list[Prompt]:
    return [
        Prompt(
            name="minecraft_health_check",
            title="Minecraft Health Check",
            description="A cautious operational health-check flow for a Minecraft instance.",
            arguments=[
                {
                    "name": "instance_hint",
                    "description": "Optional human-readable instance name or UUID.",
                    "required": False,
                }
            ],
            get=lambda args: [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Run a cautious Minecraft server health check. Start with configuration/resource context, "
                            "then list instances, inspect the selected instance status, read recent logs, and if MSMP is "
                            "configured query server status and connected players. Do not run high-risk tools unless the "
                            "user explicitly approves confirm=true. "
                            f"Instance hint: {args.get('instance_hint', '')}"
                        ),
                    },
                }
            ],
        ),
        Prompt(
            name="minecraft_safe_restart",
            title="Minecraft Safe Restart",
            description="A staged restart plan that saves first and uses dry-run previews for risky steps.",
            arguments=[],
            get=lambda args: [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Prepare a safe Minecraft restart. Check players and logs first, broadcast a warning if useful, "
                            "save the world, then preview restart with dry_run=true. Only perform restart after explicit "
                            "user approval with confirm=true."
                        ),
                    },
                }
            ],
        ),
    ]


def make_mcp_server(config: AppConfig, tools: list[Tool], resources: list[Resource], prompts: list[Prompt]) -> Server:
    app = Server(
        "minecraft-ops-mcp",
        version=__version__,
        instructions=(
            "Minecraft server operations over MCSManager, RCON, and MSMP. "
            "High-risk tools require confirm=true or dry_run=true."
        ),
    )
    tool_map = {tool.name: tool for tool in tools}
    resource_map = {resource.uri: resource for resource in resources}
    prompt_map = {prompt.name: prompt for prompt in prompts}

    @app.list_tools()
    async def list_tools(req: types.ListToolsRequest) -> types.ListToolsResult:
        cursor = req.params.cursor if req and req.params else None
        page, next_cursor = _page_items(tools, cursor)
        return types.ListToolsResult(
            tools=[_to_sdk_tool(tool) for tool in page],
            nextCursor=next_cursor,
        )

    @app.call_tool(validate_input=True)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
        tool = tool_map.get(name)
        if tool is None:
            return _tool_error(f"Unknown tool: {name}")
        try:
            result = tool.handler(arguments or {})
        except OpsError as exc:
            return _tool_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            return _tool_error(f"{type(exc).__name__}: {exc}")
        structured = result if isinstance(result, dict) else {"result": result}
        content = [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2, default=str))]
        return content, structured

    @app.list_resources()
    async def list_resources(req: types.ListResourcesRequest) -> types.ListResourcesResult:
        cursor = req.params.cursor if req and req.params else None
        page, next_cursor = _page_items(resources, cursor)
        return types.ListResourcesResult(
            resources=[_to_sdk_resource(resource) for resource in page],
            nextCursor=next_cursor,
        )

    @app.read_resource()
    async def read_resource(uri: Any) -> list[ReadResourceContents]:
        resource = resource_map.get(str(uri))
        if resource is None:
            raise OpsError(f"Unknown resource: {uri}")
        return [ReadResourceContents(content=resource.read(), mime_type=resource.mime_type)]

    @app.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return []

    @app.list_prompts()
    async def list_prompts(req: types.ListPromptsRequest) -> types.ListPromptsResult:
        cursor = req.params.cursor if req and req.params else None
        page, next_cursor = _page_items(prompts, cursor)
        return types.ListPromptsResult(
            prompts=[_to_sdk_prompt(prompt) for prompt in page],
            nextCursor=next_cursor,
        )

    @app.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        prompt = prompt_map.get(name)
        if prompt is None:
            raise OpsError(f"Unknown prompt: {name}")
        return types.GetPromptResult(
            description=prompt.description,
            messages=[_to_sdk_prompt_message(message) for message in prompt.get(arguments or {})],
        )

    return app


def _to_sdk_tool(tool: Tool) -> types.Tool:
    annotations = types.ToolAnnotations(**tool.annotations) if tool.annotations else None
    return types.Tool(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        inputSchema=tool.input_schema,
        outputSchema=tool.output_schema,
        annotations=annotations,
    )


def _to_sdk_resource(resource: Resource) -> types.Resource:
    return types.Resource(
        uri=resource.uri,
        name=resource.name,
        title=resource.title,
        description=resource.description,
        mimeType=resource.mime_type,
    )


def _to_sdk_prompt(prompt: Prompt) -> types.Prompt:
    return types.Prompt(
        name=prompt.name,
        title=prompt.title,
        description=prompt.description,
        arguments=[types.PromptArgument(**argument) for argument in prompt.arguments],
    )


def _to_sdk_prompt_message(message: dict[str, Any]) -> types.PromptMessage:
    role = message.get("role", "user")
    content = message.get("content") or {}
    if role not in {"user", "assistant"}:
        raise OpsError(f"Unsupported prompt message role: {role}")
    if content.get("type") != "text":
        raise OpsError(f"Unsupported prompt message content type: {content.get('type')}")
    return types.PromptMessage(
        role=role,
        content=types.TextContent(type="text", text=str(content.get("text", ""))),
    )


def _tool_error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], isError=True)


def _page_items(items: list[Any], cursor: Any) -> tuple[list[Any], str | None]:
    if cursor in (None, ""):
        start = 0
    elif isinstance(cursor, str) and cursor.isdigit():
        start = int(cursor)
    else:
        raise OpsError("Invalid pagination cursor.")
    end = start + DEFAULT_PAGE_SIZE
    next_cursor = str(end) if end < len(items) else None
    return items[start:end], next_cursor


async def async_main() -> None:
    config = AppConfig.from_env()
    tools = make_tools(config)
    app = make_mcp_server(config, tools, make_resources(config, tools_as_json(tools)), make_prompts())
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    try:
        anyio.run(async_main)
    except KeyboardInterrupt:
        return
    except Exception as exc:  # noqa: BLE001
        print(f"minecraft-ops-mcp fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
