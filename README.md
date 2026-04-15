# Minecraft Ops MCP

一个面向 Minecraft 服务器管理和运维的 MCP stdio 服务。当前版本基于官方 MCP Python SDK 实现，后端适配：

- MCSManager API：实例生命周期、日志、文件管理、控制台命令。
- RCON：传统控制台命令兜底。
- Minecraft Server Management Protocol（MSMP）：Minecraft Java 1.21.9+ 的 JSON-RPC over WebSocket 管理协议。

## 文档

- 用户手册：[docs/USER_MANUAL.md](docs/USER_MANUAL.md)
- 开发者框架说明：[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
- 当前不足与后续路线：[docs/LIMITATIONS.md](docs/LIMITATIONS.md)
- 集成测试报告：[docs/TEST_REPORT_2026-04-14.md](docs/TEST_REPORT_2026-04-14.md)
- 发布检查清单：[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)
- 安全说明：[SECURITY.md](SECURITY.md)
- 变更日志：[CHANGELOG.md](CHANGELOG.md)

## Agent 使用建议

建议把本 MCP 与 `minecraft-ops-runbook` skill 配合使用：

- MCP 负责真实操作：MCSManager、RCON、MSMP、文件、快照、应用/回滚、测试运行记录。
- Skill 负责操作顺序：先读配置和安全策略，再做只读检查、dry-run、等待用户确认，最后记录证据。

常见任务入口：

- 健康检查：读取 `minecraft-ops://config`、`minecraft-ops://safety`，再查实例、日志、玩家和状态。
- 安全重启：先查在线玩家、广播、保存世界，再 dry-run 重启，最后确认执行。
- 整合包兼容测试：`snapshot -> diff -> apply dry-run -> apply confirm -> logs/crash -> classify -> record_test_run -> rollback dry-run/confirm`。
- 性能排查：优先用 spark/Observable 等 mod 自带命令采样，再用日志、TPS/MSPT、实体/区块证据定位。

## 运行方式

推荐在当前 Python 环境中安装项目依赖后运行：

```bash
cd /home/damoc/codes/minecraft-ops-mcp
python3 -m pip install -e .
minecraft-ops-mcp
```

开发调试时也可以显式指定 `PYTHONPATH`：

```bash
cd /home/damoc/codes/minecraft-ops-mcp
PYTHONPATH=src python3 -m minecraft_ops_mcp
```

## 开发与测试

单元测试：

```bash
cd /home/damoc/codes/minecraft-ops-mcp
PYTHONPATH=src python3 -B -m unittest discover -s tests
```

语法检查：

```bash
python3 -m compileall -q src scripts
```

协议级集成探针见 [docs/TEST_REPORT_2026-04-14.md](docs/TEST_REPORT_2026-04-14.md) 和 [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)。

## MCP 客户端配置示例

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
        "RCON_HOST": "127.0.0.1",
        "RCON_PORT": "25575",
        "RCON_PASSWORD": "replace-me",
        "MSMP_URL": "ws://127.0.0.1:25585",
        "MSMP_SECRET": "replace-me",
        "MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST": "list,time,help",
        "MINECRAFT_OPS_RAW_COMMAND_DENYLIST": "stop,op,deop,ban,ban-ip",
        "MINECRAFT_OPS_MAX_BYTES": "268435456",
        "MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS": "/tmp/minecraft-ops-mcp-downloads,/srv/minecraft-staging",
        "MINECRAFT_OPS_FILE_OPERATION_WHITELIST": "server.properties,config,mods,logs,crash-reports",
        "MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS": "example.com,cdn.example.com",
        "MINECRAFT_OPS_MODPACK_WORKSPACE": "/srv/minecraft-ops/modpack-workspace"
      }
    }
  }
}
```

也可以参考 `.env.example`，把变量放进你的 MCP 客户端配置里。这个服务本身不会自动读取 `.env` 文件，避免让运行时配置来源变得隐式。

## 主要工具

MCSManager：

- `server.list_daemons`
- `server.get_daemon_system`
- `server.list_instances`
- `server.get_instance`
- `server.start`
- `server.stop`
- `server.restart`
- `server.kill`
- `server.send_command`
- `server.get_logs`
- `instance.create`
- `instance.update_config`
- `instance.update_config_patch`
- `instance.clone_from_template`
- `instance.delete`
- `instance.reinstall`
- `instance.run_update_task`
- `file.list`
- `file.read`
- `file.download_prepare`
- `file.download_local`
- `file.upload_prepare`
- `file.upload_local`
- `file.upload_url`
- `file.write`
- `file.write_new`
- `file.delete`
- `file.move`
- `file.copy`
- `file.mkdir`
- `file.touch`
- `file.compress`
- `file.uncompress`

整合包元数据：

- `modpack.inspect_jar`
- `modpack.snapshot_modlist`
- `modpack.diff_snapshots`
- `modpack.apply_modlist`
- `modpack.rollback_snapshot`
- `modpack.classify_startup_result`
- `modpack.record_test_run`
- `modpack.list_test_runs`
- `modpack.get_test_run`

跨后端便捷工具：

- `server.save_world`：优先 MSMP，其次 RCON，再次 MCSManager。
- `server.broadcast`：优先 MSMP，其次 RCON，再次 MCSManager。

RCON：

- `rcon.command`
- `rcon.list_players`
- `rcon.time_query`
- `rcon.save_all`

MSMP：

- `msmp.discover`
- `msmp.call`
- `msmp.players.list`
- `msmp.players.kick`
- `msmp.server.status`
- `msmp.server.save`
- `msmp.server.stop`
- `msmp.bans.get`
- `msmp.bans.add`
- `msmp.bans.remove`
- `msmp.bans.set`
- `msmp.bans.clear`
- `msmp.ip_bans.get`
- `msmp.ip_bans.add`
- `msmp.ip_bans.remove`
- `msmp.ip_bans.set`
- `msmp.ip_bans.clear`
- `msmp.allowlist.get`
- `msmp.allowlist.add`
- `msmp.allowlist.remove`
- `msmp.allowlist.set`
- `msmp.allowlist.clear`
- `msmp.operators.get`
- `msmp.operators.add`
- `msmp.operators.remove`
- `msmp.operators.set`
- `msmp.operators.clear`
- `msmp.gamerules.get`
- `msmp.gamerules.update`
- `msmp.server_settings.get`
- `msmp.server_settings.list`
- `msmp.server_settings.set`

## 安全策略

高风险操作需要显式参数：

```json
{ "confirm": true }
```

如果只是想让 agent 先说明会做什么，传：

```json
{ "dry_run": true }
```

高风险工具包括实例启停/重启/kill、原始命令、文件写入/删除/移动/压缩/解压/上传、下载到 MCP 本机、踢人、封禁、白名单/OP/游戏规则/服务器设置修改等。所有工具调用会写入审计日志，默认位置：

```text
/tmp/minecraft-ops-mcp-audit.jsonl
```

可以通过 `MINECRAFT_OPS_AUDIT_LOG=` 置空来关闭。

原始命令还可以用前缀级 allowlist/denylist 进一步约束：

```text
MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST=list,time,help
MINECRAFT_OPS_RAW_COMMAND_DENYLIST=stop,op,deop,ban,ban-ip
```

文件传输也支持可选护栏：

```text
MINECRAFT_OPS_MAX_BYTES=268435456
MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS=/tmp/minecraft-ops-mcp-downloads,/srv/minecraft-staging
MINECRAFT_OPS_FILE_OPERATION_WHITELIST=server.properties,config,mods,logs,crash-reports
MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS=example.com,cdn.example.com
MINECRAFT_OPS_MODPACK_WORKSPACE=/srv/minecraft-ops/modpack-workspace
```

其中 `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS` 限制 MCP 宿主机本地上传来源和下载目标目录；`MINECRAFT_OPS_FILE_OPERATION_WHITELIST` 限制实例内文件写入/上传/下载目标前缀；`MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS` 限制 `file.upload_url` 可拉取的域名。
`MINECRAFT_OPS_MODPACK_WORKSPACE` 用于保存 `modpack.snapshot_modlist` 生成的快照 JSON、jar 缓存和 `modpack.record_test_run` 生成的测试运行记录，后续可以由 agent 或外部 git 流程提交留痕。

## 后端建议

- 实例生命周期、日志和文件管理：优先用 MCSManager API。
- 玩家、白名单、OP、gamerule、server settings：优先用 MSMP。
- 老版本服务端或 MSMP 不覆盖的能力：用 RCON 或 MCSManager raw command 兜底。

不要把 RCON、MSMP、MCSManager API key 暴露到公网。MSMP/RCON 最好只监听 localhost、VPN 或隧道；MCSManager 建议使用低权限 API key。

## MSMP 参数说明

`msmp.call` 会按原样传 JSON-RPC `params`，适合服务端/版本差异较大的新方法：

```json
{
  "method": "minecraft:server/status",
  "read_only": true
}
```

写操作请使用 `confirm=true` 或先 `dry_run=true`。
`read_only=true` 只会对白名单里的只读 MSMP 方法跳过确认，例如 `rpc.discover`、`minecraft:players`、`minecraft:server/status`、列表查询和 server settings 读取。
