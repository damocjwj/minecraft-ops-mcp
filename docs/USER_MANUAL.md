# Minecraft Ops MCP 用户手册

本手册面向通过 MCP 客户端调用 `minecraft-ops-mcp` 的 agent 或运维人员，重点说明如何安全选工具。

## 1. 基本模型

MCSManager 是主接口。RCON 和 MSMP 不作为全局客户端 endpoint 配置。每次工具调用会先通过 `daemonId` 和 `uuid` 选择目标实例，再从该实例读取协议配置：

- RCON：MCSManager 实例配置中的 `enableRcon`、`rconIp`、`rconPort`、`rconPassword`。
- MSMP：实例文件 `server.properties` 中的 `management-server-*`。

如果调用时不传 `daemonId` 或 `uuid`，会使用 `MCSM_DEFAULT_DAEMON_ID` 和 `MCSM_DEFAULT_INSTANCE_UUID`。多服务器操作必须显式传目标 id。

当协议 host 是 `0.0.0.0`、`127.0.0.1`、`localhost` 或空值时，MCP 默认使用 MCSManager 的 hostname 作为连接 host。复杂网络环境可以在 RCON/MSMP 工具中传 `connection_host` 覆盖。

## 2. 客户端配置

最小 MCP 客户端配置：

```json
{
  "mcpServers": {
    "minecraft-ops": {
      "command": "python3",
      "args": ["-m", "minecraft_ops_mcp"],
      "cwd": "/home/damoc/codes/minecraft-ops-mcp",
      "env": {
        "PYTHONPATH": "src",
        "MCSM_BASE_URL": "http://127.0.0.1:23333",
        "MCSM_API_KEY": "replace-me",
        "MCSM_DEFAULT_DAEMON_ID": "replace-me",
        "MCSM_DEFAULT_INSTANCE_UUID": "replace-me",
        "MINECRAFT_OPS_AUDIT_LOG": "/tmp/minecraft-ops-mcp-audit.jsonl"
      }
    }
  }
}
```

不要在客户端配置固定 RCON host/password 或 MSMP URL/secret。请用 `rcon.config.*` 和 `msmp.config.*` 按实例管理。

重要可选变量：

- `MCSM_TIMEOUT_SECONDS`：MCSManager HTTP 超时。
- `MINECRAFT_OPS_RCON_TIMEOUT_SECONDS`、`MINECRAFT_OPS_RCON_ENCODING`：解析出实例 RCON endpoint 后使用的连接默认值。
- `MINECRAFT_OPS_MSMP_TIMEOUT_SECONDS`、`MINECRAFT_OPS_MSMP_TLS_VERIFY`：解析出实例 MSMP endpoint 后使用的连接默认值。
- `MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST`：原始命令前缀白名单。
- `MINECRAFT_OPS_RAW_COMMAND_DENYLIST`：原始命令前缀黑名单。
- `MINECRAFT_OPS_MAX_BYTES`：上传/下载大小限制。
- `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS`：MCP 宿主机本地上传来源和下载目标目录白名单。
- `MINECRAFT_OPS_FILE_OPERATION_WHITELIST`：实例内写入、上传、下载路径前缀白名单。
- `MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS`：`file.upload_url` 允许拉取的域名。
- `MINECRAFT_OPS_MODPACK_WORKSPACE`：快照、jar 缓存和测试记录工作区。

## 3. 安全约定

高风险工具必须传 `dry_run=true` 或 `confirm=true`。

高风险类别：

- 实例启动、停止、重启、kill；
- MCSManager 控制台命令、RCON 原始命令、任意 MSMP call；
- 文件写入、上传、删除、移动、复制、压缩、解压；
- 实例创建、更新、patch、克隆、删除、重装；
- 协议配置修改：`rcon.config.set`、`msmp.config.set`；
- 踢人、封禁、白名单、OP、gamerule、server settings 修改；
- 整合包应用和回滚。

推荐步骤：

1. 读取当前状态；
2. 用 `dry_run=true` 预览；
3. 给出目标实例和预期改动；
4. 用户明确批准后用 `confirm=true` 执行；
5. 再读一次状态验证。

不要输出 API key、RCON password、MSMP secret、上传/下载 token 或包含 secret 的完整配置行。

## 4. MCP 资源和提示词

常用资源：

- `minecraft-ops://config`：脱敏后的运行配置。
- `minecraft-ops://safety`：高风险工具和原始命令策略。
- `minecraft-ops://tools`：完整工具目录和 schema。

内置提示词：

- `minecraft_health_check`
- `minecraft_safe_restart`

提示词只是起点，实际工具调用仍遵守安全约定。

## 5. 工具路由

MCSManager 平台操作：

- `server.list_daemons`
- `server.list_instances`
- `server.get_instance`
- `server.get_logs`
- `server.start`、`server.stop`、`server.restart`、`server.kill`
- `server.send_command`
- `instance.*`
- `file.*`

RCON 适用于旧版本服务端或 MSMP 未覆盖的命令：

- `rcon.config.get`、`rcon.config.set`
- `rcon.list_players`
- `rcon.time_query`
- `rcon.save_all`
- `rcon.command`

MSMP 适用于 Minecraft Java 1.21.9+ 的结构化管理：

- `msmp.config.get`、`msmp.config.set`
- `msmp.discover`、`msmp.call`
- `msmp.server.status`、`msmp.server.save`、`msmp.server.stop`
- `msmp.players.*`
- `msmp.bans.*`、`msmp.ip_bans.*`
- `msmp.allowlist.*`、`msmp.operators.*`
- `msmp.gamerules.*`、`msmp.server_settings.*`

整合包工具：

- `modpack.inspect_jar`
- `modpack.snapshot_modlist`
- `modpack.diff_snapshots`
- `modpack.apply_modlist`
- `modpack.rollback_snapshot`
- `modpack.classify_startup_result`
- `modpack.record_test_run`
- `modpack.list_test_runs`
- `modpack.get_test_run`

## 6. 目标选择

默认实例：

```json
{}
```

指定实例：

```json
{
  "daemonId": "daemon-id",
  "uuid": "instance-uuid"
}
```

同时处理多个服务器时，每次调用都显式传 `daemonId` 和 `uuid`，不要依赖默认目标。

## 7. RCON 操作

读取配置：

```json
{
  "daemonId": "daemon-id",
  "uuid": "instance-uuid"
}
```

`rcon.config.get` 返回脱敏字段：`enabled`、`configuredHost`、`connectionHost`、`port`、`passwordSet`、`source`。

修改配置：

```json
{
  "daemonId": "daemon-id",
  "uuid": "instance-uuid",
  "enabled": true,
  "rcon_ip": "0.0.0.0",
  "rcon_port": 25575,
  "rcon_password": "replace-with-strong-password",
  "dry_run": true
}
```

确认后改用 `confirm=true`。Minecraft 通常需要重启后才会加载新的 RCON 设置。

常用检查：

```json
{ "query": "daytime" }
```

优先用 `rcon.list_players`、`rcon.time_query`、`rcon.save_all`，只有没有合适封装时才用 `rcon.command`。

## 8. MSMP 操作

读取配置：

```json
{
  "daemonId": "daemon-id",
  "uuid": "instance-uuid",
  "properties_path": "server.properties"
}
```

修改配置：

```json
{
  "daemonId": "daemon-id",
  "uuid": "instance-uuid",
  "enabled": true,
  "host": "0.0.0.0",
  "port": 25586,
  "secret": "Abcdefghij1234567890KLMNOPQRST1234567890",
  "tls_enabled": false,
  "dry_run": true
}
```

Minecraft 1.21.9 要求 secret 为 40 位字母数字。确认后改用 `confirm=true`，必要时重启实例。

用 `msmp.discover` 或 `msmp.server.status` 验证。

## 9. 文件操作

优先使用相对路径：

- `server.properties`
- `mods`
- `config`
- `logs/latest.log`
- `crash-reports`

常用步骤：

1. `file.read` 或 `file.list`
2. `file.write` / `file.write_new` / `file.upload_local` 加 `dry_run=true`
3. 用户批准
4. 改用 `confirm=true`
5. `file.read` 或 `file.list` 验证

`file.write_new` 更适合创建新文件：它执行 `touch -> write`，并默认拒绝覆盖已存在文件。

上传和下载都采用流式处理，并受 `MINECRAFT_OPS_MAX_BYTES` 限制。

## 10. 实例生命周期

安全重启：

1. `server.get_instance`
2. `server.get_logs`
3. `msmp.players.list` 或 `rcon.list_players`
4. 如需通知玩家，使用 `server.broadcast`
5. `server.save_world`
6. `server.restart {"dry_run": true}`
7. 用户批准
8. `server.restart {"confirm": true}`
9. 验证状态和日志

`server.kill` 只应作为明确批准后的升级手段。

## 11. 整合包兼容测试

推荐循环：

1. `modpack.snapshot_modlist` 保存基线。
2. agent 外部检索候选 mod 版本。
3. `modpack.apply_modlist {"dry_run": true}`。
4. 用户批准。
5. `modpack.apply_modlist {"confirm": true}`。
6. 启动或重启测试实例。
7. 读取日志和崩溃报告。
8. `modpack.classify_startup_result`。
9. `modpack.record_test_run`。
10. 必要时 `modpack.rollback_snapshot`。

如果 MCSManager 目录 listing 不可靠，向 modpack 工具显式传 `remote_paths` 或 `current_paths`。

## 12. 多服务器管理

同时处理两个服务器时，先记录各自目标：

```json
{
  "serverA": {"daemonId": "daemon-id", "uuid": "uuid-a"},
  "serverB": {"daemonId": "daemon-id", "uuid": "uuid-b"}
}
```

之后每次调用都带对应 id：

1. 分别调用 `rcon.config.get`。
2. 分别调用 `msmp.config.get`。
3. 确认端口和 `connectionHost`。
4. 用显式 id 执行操作。
5. 分别读回状态，确认没有串实例。

真实探针 `scripts/multi_server_backend_probe.py` 会创建两个临时实例，使用独立 RCON/MSMP 凭据，并在同一个 MCP 进程中交替调用以验证隔离性。

## 13. 测试命令

本地检查：

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
python3 -m compileall -q src scripts
```

真实后端探针：

```bash
python3 -B scripts/mcp_integration_probe.py > /tmp/minecraft-ops-mcp-probe-report.json
python3 -B scripts/msmp_temp_instance_probe.py > /tmp/minecraft-ops-mcp-msmp-probe-report.json
python3 -B scripts/multi_server_backend_probe.py > /tmp/minecraft-ops-mcp-multi-probe-report.json
```

真实探针会创建临时文件或实例，结束后清理。只在可丢弃测试环境运行。
