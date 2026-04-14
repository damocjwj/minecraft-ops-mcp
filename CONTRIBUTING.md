# Contributing

## Development Setup

Install the project in editable mode to pull in the official MCP SDK and WebSocket dependency.

```bash
cd /home/damoc/codes/minecraft-ops-mcp
python3 -m pip install -e .
PYTHONPATH=src python3 -B -m unittest discover -s tests
python3 -m compileall -q src scripts
```

## Adding Tools

1. Add backend-specific HTTP/socket/WebSocket logic to an adapter under `src/minecraft_ops_mcp/adapters/` when needed.
2. Register the MCP tool in `src/minecraft_ops_mcp/tools.py`.
3. Add high-risk tools to `HIGH_RISK_TOOLS` in `src/minecraft_ops_mcp/policy.py`.
4. Add or update unit tests under `tests/`.
5. Update `README.md`, `docs/USER_MANUAL.md`, and `docs/DEVELOPER_GUIDE.md`.
6. For backend-changing tools, run the integration probes if you have a disposable MCSManager/Minecraft test environment.

## Integration Probes

The probe scripts start the MCP server over stdio and call it via SDK-compatible JSON-RPC messages.

```bash
python3 -B scripts/mcp_integration_probe.py > /tmp/minecraft-ops-mcp-probe-report.json
python3 -B scripts/msmp_temp_instance_probe.py > /tmp/minecraft-ops-mcp-msmp-probe-report.json
```

The MSMP probe creates and deletes a temporary Minecraft instance. Only run it against a non-production daemon with enough disk and network access to download a Minecraft server jar.

## Safety

Never add tests that mutate a user’s existing production instance without an explicit disposable target. Prefer `dry_run=true` unless the test creates and owns all data it mutates.
