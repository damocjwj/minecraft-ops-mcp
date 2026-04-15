from __future__ import annotations

import argparse
import contextlib
import hmac
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .config import AppConfig
from .errors import OpsError
from .models import Prompt, Resource, Tool
from .policy import HIGH_RISK_TOOLS
from .tools import make_tools, tools_as_json


DEFAULT_PAGE_SIZE = 100
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_SSE_PATH = "/sse"
DEFAULT_MESSAGE_PATH = "/messages/"
DEFAULT_STREAMABLE_HTTP_PATH = "/mcp"


@dataclass(frozen=True)
class HttpTransportOptions:
    host: str = DEFAULT_HTTP_HOST
    port: int = DEFAULT_HTTP_PORT
    sse_path: str = DEFAULT_SSE_PATH
    message_path: str = DEFAULT_MESSAGE_PATH
    streamable_http_path: str = DEFAULT_STREAMABLE_HTTP_PATH
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    log_level: str = "info"
    streamable_http_json_response: bool = False
    streamable_http_stateless: bool = False
    streamable_http_session_idle_timeout: float | None = 1800.0
    bearer_token: str = ""
    allow_unauthenticated_http: bool = False


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
            result = await anyio.to_thread.run_sync(tool.handler, arguments or {})
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


def make_default_mcp_server(config: AppConfig | None = None) -> Server:
    config = config or AppConfig.from_env()
    tools = make_tools(config)
    return make_mcp_server(config, tools, make_resources(config, tools_as_json(tools)), make_prompts())


def make_sse_asgi_app(app: Server, options: HttpTransportOptions) -> Starlette:
    sse = SseServerTransport(
        options.message_path,
        security_settings=_transport_security_settings(options),
    )

    async def handle_sse(scope: Any, receive: Any, send: Any) -> Response:
        async with sse.connect_sse(scope, receive, send) as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())
        return Response()

    async def sse_endpoint(request: Request) -> Response:
        return await handle_sse(request.scope, request.receive, request._send)  # type: ignore[attr-defined]

    routes = [
        Route("/", endpoint=_health_response("sse", options), methods=["GET"]),
        Route("/health", endpoint=_health_response("sse", options), methods=["GET"]),
        Route(options.sse_path, endpoint=sse_endpoint, methods=["GET"]),
        Mount(options.message_path, app=sse.handle_post_message),
    ]
    return Starlette(routes=routes, middleware=_http_middlewares(options))


class _StreamableHTTPASGIApp:
    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self.session_manager = session_manager

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self.session_manager.handle_request(scope, receive, send)


def make_streamable_http_asgi_app(app: Server, options: HttpTransportOptions) -> Starlette:
    session_manager = StreamableHTTPSessionManager(
        app=app,
        json_response=options.streamable_http_json_response,
        stateless=options.streamable_http_stateless,
        security_settings=_transport_security_settings(options),
        session_idle_timeout=options.streamable_http_session_idle_timeout,
    )
    streamable_http_app = _StreamableHTTPASGIApp(session_manager)

    @contextlib.asynccontextmanager
    async def lifespan(_starlette_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    routes = [
        Route("/", endpoint=_health_response("streamable-http", options), methods=["GET"]),
        Route("/health", endpoint=_health_response("streamable-http", options), methods=["GET"]),
        Route(options.streamable_http_path, endpoint=streamable_http_app),
    ]
    return Starlette(routes=routes, middleware=_http_middlewares(options), lifespan=lifespan)


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str, public_paths: tuple[str, ...] = ("/", "/health")):
        self.app = app
        self.token = token
        self.public_paths = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.public_paths:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        expected = f"Bearer {self.token}"
        if hmac.compare_digest(headers.get("authorization", ""), expected):
            await self.app(scope, receive, send)
            return
        response = Response(
            "Unauthorized",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


def _http_middlewares(options: HttpTransportOptions) -> list[Middleware]:
    if not options.bearer_token:
        return []
    return [Middleware(BearerAuthMiddleware, token=options.bearer_token)]


def _health_response(transport: str, options: HttpTransportOptions):
    async def endpoint(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "minecraft-ops-mcp",
                "version": __version__,
                "transport": transport,
                "ssePath": options.sse_path,
                "messagePath": options.message_path,
                "streamableHttpPath": options.streamable_http_path,
            }
        )

    return endpoint


def _transport_security_settings(options: HttpTransportOptions) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(_effective_allowed_hosts(options)),
        allowed_origins=list(options.allowed_origins),
    )


def _effective_allowed_hosts(options: HttpTransportOptions) -> tuple[str, ...]:
    if options.allowed_hosts:
        return options.allowed_hosts
    hosts = {options.host}
    if options.host in {"0.0.0.0", "::", ""}:
        hosts.update({"127.0.0.1", "localhost", "[::1]"})
    if options.host in {"127.0.0.1", "localhost", "::1", "[::1]"}:
        hosts.update({"127.0.0.1", "localhost", "[::1]"})
    expanded: list[str] = []
    for host in sorted(host for host in hosts if host):
        expanded.append(host)
        expanded.append(_host_with_port(host, options.port))
    return tuple(dict.fromkeys(expanded))


def _host_with_port(host: str, port: int) -> str:
    if host.startswith("["):
        return f"{host}:{port}"
    if ":" in host and host.count(":") > 1:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


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


async def run_stdio(config: AppConfig | None = None) -> None:
    app = make_default_mcp_server(config)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


async def run_sse(options: HttpTransportOptions, config: AppConfig | None = None) -> None:
    import uvicorn

    app = make_sse_asgi_app(make_default_mcp_server(config), options)
    server_config = uvicorn.Config(app, host=options.host, port=options.port, log_level=options.log_level)
    await uvicorn.Server(server_config).serve()


async def run_streamable_http(options: HttpTransportOptions, config: AppConfig | None = None) -> None:
    import uvicorn

    app = make_streamable_http_asgi_app(make_default_mcp_server(config), options)
    server_config = uvicorn.Config(app, host=options.host, port=options.port, log_level=options.log_level)
    await uvicorn.Server(server_config).serve()


async def async_main() -> None:
    await run_stdio()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    options = http_transport_options_from_args(args)
    try:
        if args.transport == "stdio":
            anyio.run(run_stdio)
        elif args.transport == "sse":
            anyio.run(run_sse, options)
        elif args.transport == "streamable-http":
            anyio.run(run_streamable_http, options)
        else:  # pragma: no cover
            raise ValueError(f"Unsupported transport: {args.transport}")
    except KeyboardInterrupt:
        return
    except Exception as exc:  # noqa: BLE001
        print(f"minecraft-ops-mcp fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minecraft operations MCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default=os.getenv("MINECRAFT_OPS_MCP_TRANSPORT", "stdio"),
        help="MCP transport to run.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MINECRAFT_OPS_MCP_HOST", DEFAULT_HTTP_HOST),
        help="HTTP host for sse or streamable-http.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MINECRAFT_OPS_MCP_PORT", str(DEFAULT_HTTP_PORT))),
        help="HTTP port for sse or streamable-http.",
    )
    parser.add_argument(
        "--sse-path",
        default=os.getenv("MINECRAFT_OPS_MCP_SSE_PATH", DEFAULT_SSE_PATH),
        help="Legacy HTTP+SSE GET endpoint path.",
    )
    parser.add_argument(
        "--message-path",
        default=os.getenv("MINECRAFT_OPS_MCP_MESSAGE_PATH", DEFAULT_MESSAGE_PATH),
        help="Legacy HTTP+SSE POST message endpoint path.",
    )
    parser.add_argument(
        "--streamable-http-path",
        default=os.getenv("MINECRAFT_OPS_MCP_STREAMABLE_HTTP_PATH", DEFAULT_STREAMABLE_HTTP_PATH),
        help="Streamable HTTP endpoint path.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Allowed Host header for HTTP transports. Repeatable. Defaults to local host values.",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Allowed Origin header for HTTP transports. Repeatable. Required for browser clients that send Origin.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("MINECRAFT_OPS_MCP_LOG_LEVEL", "info"),
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="Uvicorn log level for HTTP transports.",
    )
    parser.add_argument(
        "--http-bearer-token",
        default=os.getenv("MINECRAFT_OPS_MCP_BEARER_TOKEN", ""),
        help="Bearer token required by HTTP transports. Prefer setting MINECRAFT_OPS_MCP_BEARER_TOKEN instead of passing this on the command line.",
    )
    parser.add_argument(
        "--allow-unauthenticated-http",
        action="store_true",
        default=_env_bool("MINECRAFT_OPS_MCP_ALLOW_UNAUTHENTICATED_HTTP", False),
        help="Allow HTTP transports without a bearer token even when bound to a non-local interface.",
    )
    parser.add_argument(
        "--streamable-http-json-response",
        action="store_true",
        default=_env_bool("MINECRAFT_OPS_MCP_STREAMABLE_HTTP_JSON_RESPONSE", False),
        help="Use JSON responses instead of SSE streams for Streamable HTTP request responses.",
    )
    parser.add_argument(
        "--streamable-http-stateless",
        action="store_true",
        default=_env_bool("MINECRAFT_OPS_MCP_STREAMABLE_HTTP_STATELESS", False),
        help="Run Streamable HTTP without session state.",
    )
    args = parser.parse_args(argv)
    if args.transport not in {"stdio", "sse", "streamable-http"}:
        parser.error(f"invalid transport: {args.transport}")
    if args.transport in {"sse", "streamable-http"} and _requires_http_auth(args.host):
        if not args.http_bearer_token and not args.allow_unauthenticated_http:
            parser.error(
                "HTTP transports bound to a non-local interface require MINECRAFT_OPS_MCP_BEARER_TOKEN "
                "or --allow-unauthenticated-http."
            )
    return args


def http_transport_options_from_args(args: argparse.Namespace) -> HttpTransportOptions:
    return HttpTransportOptions(
        host=args.host,
        port=args.port,
        sse_path=_http_path(args.sse_path, trailing_slash=False),
        message_path=_http_path(args.message_path, trailing_slash=True),
        streamable_http_path=_http_path(args.streamable_http_path, trailing_slash=False),
        allowed_hosts=_csv_env_and_args("MINECRAFT_OPS_MCP_ALLOWED_HOSTS", args.allowed_host),
        allowed_origins=_csv_env_and_args("MINECRAFT_OPS_MCP_ALLOWED_ORIGINS", args.allowed_origin),
        log_level=args.log_level,
        streamable_http_json_response=args.streamable_http_json_response,
        streamable_http_stateless=args.streamable_http_stateless,
        streamable_http_session_idle_timeout=None if args.streamable_http_stateless else 1800.0,
        bearer_token=args.http_bearer_token,
        allow_unauthenticated_http=args.allow_unauthenticated_http,
    )


def _csv_env_and_args(env_name: str, values: list[str]) -> tuple[str, ...]:
    items = [item.strip() for item in os.getenv(env_name, "").split(",") if item.strip()]
    for value in values:
        items.extend(item.strip() for item in value.split(",") if item.strip())
    return tuple(dict.fromkeys(items))


def _http_path(value: str, *, trailing_slash: bool) -> str:
    path = value.strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if trailing_slash and not path.endswith("/"):
        path += "/"
    if not trailing_slash and len(path) > 1:
        path = path.rstrip("/")
    return path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _requires_http_auth(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    return normalized not in {"", "127.0.0.1", "localhost", "::1"}
