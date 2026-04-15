# Security

`minecraft-ops-mcp` can control Minecraft server processes, files, player state, and management APIs. Treat it as an operations tool with production privileges.

## Supported Security Model

- Credentials are supplied through environment variables or a protected env file.
- The default stdio mode does not expose an HTTP listener.
- HTTP modes (`sse` and `streamable-http`) must be bound only to trusted interfaces or protected behind VPN, reverse proxy authentication, firewall rules, and TLS.
- HTTP modes validate Host and Origin headers through SDK transport security settings; configure allowed hosts/origins explicitly for domain or reverse-proxy deployments.
- HTTP modes support bearer-token authentication through `MINECRAFT_OPS_MCP_BEARER_TOKEN`; non-local binds require it unless `MINECRAFT_OPS_MCP_ALLOW_UNAUTHENTICATED_HTTP=true` is explicitly set.
- High-risk tools require `confirm=true` or return a `dry_run=true` preview.
- Tool calls are written to an audit log unless `MINECRAFT_OPS_AUDIT_LOG` is empty.
- Audit logs redact common secret fields and text lines containing password/secret markers.
- Raw commands are single-line only and can be constrained with:
  - `MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST`
  - `MINECRAFT_OPS_RAW_COMMAND_DENYLIST`
- File transfer and file write scope can be constrained with:
  - `MINECRAFT_OPS_MAX_BYTES`
  - `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS`
  - `MINECRAFT_OPS_FILE_OPERATION_WHITELIST`
  - `MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS`
- Modpack snapshot reads are constrained by `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS` for local jar inspection and by `MINECRAFT_OPS_MODPACK_WORKSPACE` for saved snapshot paths.
- `modpack.apply_modlist` and `modpack.rollback_snapshot` are high-risk tools because they can upload and delete jars under the target mods directory; always review their dry-run plan first.

## Operational Recommendations

- Run this MCP server on the same trusted host or private network as the Minecraft management backends.
- Prefer stdio for local-only use. Prefer Streamable HTTP over legacy SSE for new remote clients.
- For remote HTTP use, set a long random bearer token and provide it to the client through its secret or environment mechanism.
- Keep RCON and MSMP bound to localhost, VPN, or another trusted network boundary.
- Prefer least-privilege MCSManager API keys where possible.
- Do not commit `.env`, API keys, RCON passwords, MSMP secrets, generated reports, or audit logs.
- Review any action that requires `confirm=true`, especially destructive file and lifecycle operations.
- Use `dry_run=true` before destructive or broad operations.
- In production, set local directory, remote path, and URL-domain allowlists instead of relying on empty default allowlists.
- Put `MINECRAFT_OPS_MODPACK_WORKSPACE` in a private directory and commit exported snapshots to git only after reviewing that they do not contain sensitive local paths.

## Reporting Issues

Do not include real API keys, RCON passwords, MSMP secrets, or full server logs in bug reports. Redact secrets and include the relevant tool name, arguments shape, backend versions, and sanitized error text.
