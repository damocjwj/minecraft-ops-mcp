# 集成测试报告 2026-04-14

本次测试目标是尽量模拟 MCP 客户端真实调用流程，通过官方 MCP Python SDK 的 stdio transport 启动 `minecraft-ops-mcp`，使用 `initialize`、`tools/list`、`tools/call`、`resources/*`、`prompts/*` 调用工具，而不是直接调用 Python handler。

## 结论

- 工具目录：71 个工具全部被覆盖。
- 基础 MCP 探针：75/75 通过。
- 临时 MSMP 实例探针：58/58 通过。
- 单元测试：10/10 通过。
- MCP 初始化协议版本：`2025-11-25`。
- 打包验证：生成 `minecraft_ops_mcp-0.3.0-py3-none-any.whl`。
- 临时实例：已停止并删除，未留下 `codex_probe_` 或 `codex-msmp-probe` 实例。
- 测试报告与审计日志已做敏感字段/配置行脱敏。

## 覆盖范围

协议层：

- `initialize`
- `notifications/initialized`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`
- `prompts/list`
- `prompts/get`
- 工具入参 schema 拒绝非法额外字段
- dict/list 工具结果的 `structuredContent`
- 官方 SDK transport、工具注册、resources、prompts、tool annotations 与 `outputSchema`

MCSManager：

- 节点/实例读取：`server.list_daemons`、`server.get_daemon_system`、`server.list_instances`、`server.get_instance`、`server.get_logs`
- 生命周期：`server.start`、`server.stop`、`server.restart` 在临时实例上实测；`server.kill` 使用 dry-run
- 实例配置：`instance.create`、`instance.update_config_patch`、`instance.delete` 在临时实例上实测；`instance.update_config`、`instance.clone_from_template`、`instance.reinstall`、`instance.run_update_task` 使用 dry-run
- 文件管理：`file.list`、`file.read`、`file.download_prepare`、`file.download_local`、`file.upload_prepare`、`file.upload_local`、`file.upload_url`、`file.write`、`file.write_new`、`file.delete`、`file.move`、`file.copy`、`file.mkdir`、`file.touch`、`file.compress`、`file.uncompress`
- 跨后端：`server.save_world` 通过 MCSManager 和 MSMP 实测；`server.broadcast` 通过 MCSManager 和 MSMP 实测

RCON：

- `rcon.command`
- `rcon.list_players`
- `rcon.time_query`
- `rcon.save_all`

MSMP：

- 真实启动 Minecraft Java 1.21.9 临时实例，开启 management server。
- 使用 `websocket-client` 连接 MSMP WebSocket，并完成 JSON-RPC 请求/响应。
- 只读：`msmp.discover`、`msmp.call`、`msmp.players.list`、`msmp.server.status`、`msmp.bans.get`、`msmp.ip_bans.get`、`msmp.allowlist.get`、`msmp.operators.get`、`msmp.gamerules.get`、`msmp.server_settings.get`、`msmp.server_settings.list`
- 写入：`msmp.server.save`、`msmp.server.stop`、`msmp.players.kick`、`msmp.gamerules.update`、`msmp.server_settings.set`、`msmp.allowlist.add/remove/set/clear`、`msmp.operators.add/remove/set/clear`、`msmp.bans.add/remove/set/clear`、`msmp.ip_bans.add/remove/set/clear`

## 发现并修复的问题

- `server.broadcast` 使用 MSMP 时没有默认传入 `overlay` 字段，Minecraft 1.21.9 返回 `Invalid params`。已修复为默认 `overlay=false`。
- 审计日志和测试报告原先会保留 `server.properties` 文本里的 secret/password 行。已增强脱敏逻辑，按敏感字段名和配置行标记脱敏。
- 迁移到官方 MCP Python SDK 后，集成探针从旧 `Content-Length` 帧切换为 SDK stdio transport 使用的单行 JSON-RPC 消息，并补充 `notifications/initialized`。
- MSMP 客户端已从手写 WebSocket 帧解析迁移到 `websocket-client`，并通过临时实例探针验证。

## 复跑方式

基础探针：

```bash
MCSM_BASE_URL=http://your-mcsm-host:23333 \
MCSM_API_KEY=replace-me \
MCSM_DEFAULT_DAEMON_ID=replace-me \
MCSM_DEFAULT_INSTANCE_UUID=replace-me \
RCON_HOST=your-rcon-host \
RCON_PORT=25575 \
RCON_PASSWORD=replace-me \
MINECRAFT_OPS_AUDIT_LOG=/tmp/minecraft-ops-mcp-probe-audit.jsonl \
python3 -B scripts/mcp_integration_probe.py > /tmp/minecraft-ops-mcp-probe-report.json
```

临时 MSMP 实例探针：

```bash
MCSM_BASE_URL=http://your-mcsm-host:23333 \
MCSM_API_KEY=replace-me \
MCSM_DEFAULT_DAEMON_ID=replace-me \
MCSM_DEFAULT_INSTANCE_UUID=replace-me \
MCSM_TIMEOUT_SECONDS=180 \
MINECRAFT_OPS_AUDIT_LOG=/tmp/minecraft-ops-mcp-msmp-probe-audit.jsonl \
python3 -B scripts/msmp_temp_instance_probe.py > /tmp/minecraft-ops-mcp-msmp-probe-report.json
```

可选环境变量：

- `MINECRAFT_VERSION`：默认 `1.21.9`
- `MINECRAFT_SERVER_JAR_URL`：指定服务端 jar URL，省略时从 Mojang version manifest 获取
- `MSMP_PROBE_PORT`：默认 `25686`
- `MSMP_PROBE_GAME_PORT`：默认 `25666`
- `MSMP_PROBE_SECRET`：省略时随机生成 40 位字母数字 secret
