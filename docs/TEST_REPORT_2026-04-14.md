# 集成测试报告 2026-04-14

本次测试目标是尽量模拟 MCP 客户端真实调用流程，通过官方 MCP Python SDK 的 stdio transport 启动 `minecraft-ops-mcp`，使用 `initialize`、`tools/list`、`tools/call`、`resources/*`、`prompts/*` 调用工具，而不是直接调用 Python handler。

## 结论

- 工具目录：84 个工具完成注册和目录校验。
- 基础 MCP 探针：90/90 通过。
- 临时 MSMP 实例探针：58/58 通过。
- 多服务器 RCON/MSMP 探针：69/69 通过。
- 单元测试：45/45 通过。
- MCP 初始化协议版本：`2025-11-25`。
- 打包验证：生成 `minecraft_ops_mcp-0.8.0-py3-none-any.whl`。
- 临时实例：已停止并删除，未留下 `codex_probe_`、`codex-msmp-probe` 或 `codex-multi-probe` 实例。
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
- 同步工具 handler 在线程池中执行，避免阻塞 MCP stdio 事件循环
- `modpack.inspect_jar`、`modpack.snapshot_modlist`、`modpack.diff_snapshots`、`modpack.apply_modlist`、`modpack.rollback_snapshot`、`modpack.classify_startup_result`、`modpack.record_test_run`、`modpack.list_test_runs`、`modpack.get_test_run` 的 MCP stdio 调用

MCSManager：

- 节点/实例读取：`server.list_daemons`、`server.get_daemon_system`、`server.list_instances`、`server.get_instance`、`server.get_logs`
- 生命周期：`server.start`、`server.stop`、`server.restart` 在临时实例上实测；`server.kill` 使用 dry-run
- 实例配置：`instance.create`、`instance.update_config_patch`、`instance.delete` 在临时实例上实测；`instance.update_config`、`instance.clone_from_template`、`instance.reinstall`、`instance.run_update_task` 使用 dry-run
- 文件管理：`file.list`、`file.read`、`file.download_prepare`、`file.download_local`、`file.upload_prepare`、`file.upload_local`、`file.upload_url`、`file.write`、`file.write_new`、`file.delete`、`file.move`、`file.copy`、`file.mkdir`、`file.touch`、`file.compress`、`file.uncompress`
- 流式传输：`file.upload_local` 和 `file.download_local` 已在真实后端基础探针中通过；`file.upload_url` 的 dry-run 路径通过，域名/大小限制由单元测试覆盖。
- 跨后端：`server.save_world` 通过 MCSManager 和 MSMP 实测；`server.broadcast` 通过 MCSManager 和 MSMP 实测

RCON：

- 配置读取与写入计划：`rcon.config.get`、`rcon.config.set` dry-run 通过 MCSManager 实例配置验证。
- `rcon.command`
- `rcon.list_players`
- `rcon.time_query`
- `rcon.save_all`

MSMP：

- 真实启动 Minecraft Java 1.21.9 临时实例，开启 management server。
- MCP 客户端不配置固定 MSMP URL/secret；探针通过 MCSManager 文件 API 写入和读取实例内 `server.properties` 后动态连接。
- 配置读取与写入计划：`msmp.config.get`、`msmp.config.set` dry-run 通过。
- 使用 `websocket-client` 连接 MSMP WebSocket，并完成 JSON-RPC 请求/响应。
- 只读：`msmp.discover`、`msmp.call`、`msmp.players.list`、`msmp.server.status`、`msmp.bans.get`、`msmp.ip_bans.get`、`msmp.allowlist.get`、`msmp.operators.get`、`msmp.gamerules.get`、`msmp.server_settings.get`、`msmp.server_settings.list`
- 写入：`msmp.server.save`、`msmp.server.stop`、`msmp.players.kick`、`msmp.gamerules.update`、`msmp.server_settings.set`、`msmp.allowlist.add/remove/set/clear`、`msmp.operators.add/remove/set/clear`、`msmp.bans.add/remove/set/clear`、`msmp.ip_bans.add/remove/set/clear`

多服务器管理：

- `scripts/multi_server_backend_probe.py` 创建两个临时 Minecraft 1.21.9 实例，分别配置独立的 game port、RCON port/password 和 MSMP port/secret。
- MCP 客户端只配置 MCSManager；探针通过 `rcon.config.set` 写入实例 RCON 配置，通过 `msmp.config.set` 写入各自 `server.properties`。
- 同一个 MCP stdio 进程中交替调用两个实例：`rcon.list_players`、`rcon.time_query`、`rcon.command`、`rcon.save_all`、`msmp.server.status`、`msmp.call`、`msmp.players.list`、`server.save_world`、`server.broadcast`。
- 配置隔离校验：A/B 实例读取到不同的 RCON/MSMP 端口；A 的 `difficulty` 设置为 `peaceful`，B 的 `difficulty` 设置为 `normal`，并分别读回验证。
- 当前真实环境只有一个 MCSManager daemon，因此本轮验证覆盖同一 daemon 下多实例管理；跨 daemon 管理依赖同样的 per-call `daemonId` 机制，但尚未在多 daemon 环境实测。

## 发现并修复的问题

- `server.broadcast` 使用 MSMP 时没有默认传入 `overlay` 字段，Minecraft 1.21.9 返回 `Invalid params`。已修复为默认 `overlay=false`。
- 审计日志和测试报告原先会保留 `server.properties` 文本里的 secret/password 行。已增强脱敏逻辑，按敏感字段名和配置行标记脱敏。
- 迁移到官方 MCP Python SDK 后，集成探针从旧 `Content-Length` 帧切换为 SDK stdio transport 使用的单行 JSON-RPC 消息，并补充 `notifications/initialized`。
- MSMP 客户端已从手写 WebSocket 帧解析迁移到 `websocket-client`，并通过临时实例探针验证。
- 2026-04-15：MCSManager daemon 上传/下载改为 `httpx` 流式处理，新增 `max_bytes`、本地目录、实例内路径和 URL 域名白名单；基础探针 75/75、MSMP 临时实例探针 58/58、单元测试 20/20 通过。
- 2026-04-15：新增整合包元数据第一阶段工具，支持 jar 元数据解析、modlist 快照和快照 diff；基础探针 79/79、MSMP 临时实例探针 58/58、单元测试 27/27 通过。
- 2026-04-15：新增整合包应用与回滚工具，支持 cached snapshot、apply dry-run/confirm、rollback dry-run/confirm、空快照清理和 listing 不可靠时的 `current_paths`；基础探针 81/81、MSMP 临时实例探针 58/58、单元测试 31/31 通过。
- 2026-04-15：新增整合包测试运行记录第三阶段工具，支持启动/崩溃签名分类、测试运行写入、列表和读取；基础探针 85/85、MSMP 临时实例探针 58/58、单元测试 37/37 通过，并完成真实 MCSManager 临时目录 confirm 级 apply/record/rollback 回归。
- 2026-04-15：改为以 MCSManager API 作为 RCON/MSMP 的唯一配置来源；移除客户端固定 RCON/MSMP 连接配置，新增动态配置读取/写入工具；基础探针 90/90、MSMP 临时实例探针 58/58、单元测试 45/45 通过。
- 2026-04-15：新增多服务器后端探针，验证同一个 MCP 进程按不同 `daemonId/uuid` 动态读取并连接两个实例的 RCON/MSMP；多服务器探针 69/69 通过，临时实例已清理。

## 复跑方式

基础探针：

```bash
MCSM_BASE_URL=http://your-mcsm-host:23333 \
MCSM_API_KEY=replace-me \
MCSM_DEFAULT_DAEMON_ID=replace-me \
MCSM_DEFAULT_INSTANCE_UUID=replace-me \
MINECRAFT_OPS_AUDIT_LOG=/tmp/minecraft-ops-mcp-probe-audit.jsonl \
MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS=/tmp \
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
MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS=/tmp \
python3 -B scripts/msmp_temp_instance_probe.py > /tmp/minecraft-ops-mcp-msmp-probe-report.json
```

多服务器 RCON/MSMP 探针：

```bash
MCSM_BASE_URL=http://your-mcsm-host:23333 \
MCSM_API_KEY=replace-me \
MCSM_DEFAULT_DAEMON_ID=replace-me \
MCSM_DEFAULT_INSTANCE_UUID=replace-me \
MCSM_TIMEOUT_SECONDS=180 \
MINECRAFT_OPS_AUDIT_LOG=/tmp/minecraft-ops-mcp-multi-probe-audit.jsonl \
MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS=/tmp \
MINECRAFT_OPS_FILE_OPERATION_WHITELIST=/ \
python3 -B scripts/multi_server_backend_probe.py > /tmp/minecraft-ops-mcp-multi-probe-report.json
```

可选环境变量：

- `MINECRAFT_VERSION`：默认 `1.21.9`
- `MINECRAFT_SERVER_JAR_URL`：指定服务端 jar URL，省略时从 Mojang version manifest 获取
- `MSMP_PROBE_PORT`：默认 `25686`
- `MSMP_PROBE_GAME_PORT`：默认 `25666`
- `MSMP_PROBE_SECRET`：省略时随机生成 40 位字母数字 secret
- `MULTI_PROBE_PORT_BASE`：多服务器探针的端口基准，省略时随机选择。
- `MULTI_PROBE_PREFIX`：多服务器探针临时实例名前缀。
- `MINECRAFT_SERVER_JAR_PATH`：多服务器探针可复用本地 server jar，省略时先下载到 `/tmp` 再上传到各实例。
