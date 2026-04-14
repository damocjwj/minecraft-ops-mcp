# Changelog

All notable changes to `minecraft-ops-mcp` are documented here.

## 0.3.0 - 2026-04-14

- Replace the custom stdio MCP protocol implementation with the official MCP Python SDK.
- Remove the internal lightweight JSON Schema validator; tool input/output validation now uses SDK/jsonschema behavior.
- Replace the custom MSMP WebSocket frame implementation with `websocket-client`.
- Update stdio probe scripts for the SDK transport format and initialized notification flow.

## 0.2.0 - 2026-04-14

- Align the stdio MCP protocol layer with MCP 2025-11-25 expectations:
  - protocol version negotiation
  - JSON-RPC request validation
  - `tools/list`, `resources/list`, and `prompts/list` pagination
  - `resources/templates/list`
  - tool `title`, `annotations`, `outputSchema`, and `structuredContent`
  - tool argument validation failures returned as tool errors instead of JSON-RPC failures
- Add integration probe scripts:
  - `scripts/mcp_integration_probe.py`
  - `scripts/msmp_temp_instance_probe.py`
- Add unit tests under `tests/`.
- Add package metadata, `LICENSE`, and `py.typed`.
- Harden audit/report redaction for text config lines containing password/secret material.
- Fix MSMP broadcast by always sending `overlay`, defaulting to `false`.

## 0.1.0 - 2026-04-14

- Initial zero-dependency MCP stdio server.
- MCSManager, RCON, and MSMP adapters.
- 71 operations tools across server, instance, file, RCON, and MSMP workflows.
