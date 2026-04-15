# Changelog

All notable changes to `minecraft-ops-mcp` are documented here.

## 0.7.0 - 2026-04-15

- Add modpack test-run recording tools:
  - `modpack.classify_startup_result`
  - `modpack.record_test_run`
  - `modpack.list_test_runs`
  - `modpack.get_test_run`
- Classify common startup/crash signatures including dependency resolution, Java version, mixin failures, binary incompatibility, duplicate mods, wrong-side mods, config errors, and port conflicts.
- Save compatibility test records under `MINECRAFT_OPS_MODPACK_WORKSPACE/runs` with snapshot references, candidate metadata, classification, bounded log excerpts, tags, and external references.
- Extend unit and MCP stdio probe coverage for the full three-stage modpack workflow.

## 0.6.0 - 2026-04-15

- Add modpack apply and rollback tools:
  - `modpack.apply_modlist`
  - `modpack.rollback_snapshot`
- Cache snapshot jar contents under `MINECRAFT_OPS_MODPACK_WORKSPACE/blobs` so saved snapshots can be used as rollback sources.
- Add `remote_paths` and `current_paths` inputs for environments where MCSManager directory listing is unreliable.
- Mark modpack apply/rollback as high-risk operations requiring `dry_run=true` or `confirm=true`.
- Add unit and MCP probe coverage for apply/rollback planning, execution, and rollback to empty snapshots.

## 0.5.0 - 2026-04-15

- Add the first modpack compatibility metadata tools:
  - `modpack.inspect_jar`
  - `modpack.snapshot_modlist`
  - `modpack.diff_snapshots`
- Parse common mod metadata files: `fabric.mod.json`, `quilt.mod.json`, `META-INF/mods.toml`, `META-INF/neoforge.mods.toml`, and `mcmod.info`.
- Add `MINECRAFT_OPS_MODPACK_WORKSPACE` for saved modlist snapshot JSON files.
- Extend the stdio integration probe and unit tests to cover local mod jar inspection, snapshot creation, and snapshot diffs.

## 0.4.0 - 2026-04-15

- Run synchronous tool handlers in an AnyIO worker thread to avoid blocking the MCP stdio event loop.
- Add `httpx` as a direct dependency and use streaming HTTP for MCSManager daemon uploads, URL staging, and local downloads.
- Add configurable file-transfer guardrails:
  - `MINECRAFT_OPS_MAX_BYTES`
  - `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS`
  - `MINECRAFT_OPS_FILE_OPERATION_WHITELIST`
  - `MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS`
- Stop returning daemon upload/download token URLs in tool results; expose boolean URL-present flags instead.
- Add unit coverage for transfer limits, path/domain allowlists, and worker-thread tool execution.

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
