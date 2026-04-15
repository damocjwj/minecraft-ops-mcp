# Minecraft Ops MCP

`minecraft-ops-mcp` 是面向 Minecraft 服务器管理和运维的 MCP stdio 服务。它以 MCSManager 作为主控制面，并提供：

- 通过 MCSManager 管理实例生命周期、日志、文件、上传和下载；
- 通过按实例解析的 RCON 执行老版本或命令型运维操作；
- 通过按实例解析的 MSMP 管理 Minecraft Java 1.21.9+；
- 为整合包开发提供 jar 元数据、modlist 快照、应用/回滚和测试记录。

## 当前后端模型

MCP 客户端只配置 MCSManager。RCON 和 MSMP 是实例级配置：

- RCON 从目标实例的 MCSManager 配置读取：`enableRcon`、`rconIp`、`rconPort`、`rconPassword`。
- MSMP 从目标实例的 `server.properties` 读取：`management-server-*`。

工具调用通过 `daemonId` 和 `uuid` 选择目标实例。同一个 MCP 进程可以管理多个服务器，因为 RCON/MSMP 连接在每次调用时动态解析，而不是从客户端环境变量读取固定 endpoint。

## 安装运行

```bash
cd /home/damoc/codes/minecraft-ops-mcp
python3 -m pip install -e .
minecraft-ops-mcp
```

开发调试：

```bash
cd /home/damoc/codes/minecraft-ops-mcp
PYTHONPATH=src python3 -m minecraft_ops_mcp
```

## MCP 客户端配置

最小示例：

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

生产建议配置安全边界：

```bash
MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST=list,time,help
MINECRAFT_OPS_RAW_COMMAND_DENYLIST=stop,op,deop,ban,ban-ip
MINECRAFT_OPS_MAX_BYTES=268435456
MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS=/tmp/minecraft-ops-mcp-downloads,/srv/minecraft-staging
MINECRAFT_OPS_FILE_OPERATION_WHITELIST=server.properties,config,mods,logs,crash-reports
MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS=example.com,cdn.example.com
MINECRAFT_OPS_MODPACK_WORKSPACE=/srv/minecraft-ops/modpack-workspace
```

可选协议默认值：

```bash
MINECRAFT_OPS_RCON_TIMEOUT_SECONDS=5
MINECRAFT_OPS_RCON_ENCODING=utf-8
MINECRAFT_OPS_MSMP_TIMEOUT_SECONDS=8
MINECRAFT_OPS_MSMP_TLS_VERIFY=true
```

完整变量见 [.env.example](.env.example)。

## 工具分组

- `server.*`：实例状态、生命周期、日志、保存、广播、控制台命令。
- `instance.*`：创建、更新、patch、克隆、删除、重装、更新任务。
- `file.*`：列目录、读写、创建、上传、下载、复制、移动、压缩。
- `rcon.*`：RCON 配置读写、安全封装和原始 RCON 命令。
- `msmp.*`：MSMP 配置读写、状态、玩家、封禁、白名单、OP、gamerule、server settings、原始 JSON-RPC。
- `modpack.*`：jar 检查、modlist 快照、diff、应用/回滚、启动分类、测试记录。

高风险工具需要 `dry_run=true` 或 `confirm=true`。原始命令还会受到 allowlist/denylist 策略约束。

## 常见流程

健康检查：

1. `server.get_instance`
2. `server.get_logs`
3. `msmp.config.get`，启用后调用 `msmp.server.status` / `msmp.players.list`
4. `rcon.config.get`，需要时调用 `rcon.list_players` / `rcon.time_query`

启用或修改 RCON：

1. `rcon.config.get`
2. `rcon.config.set {"dry_run": true, ...}`
3. `rcon.config.set {"confirm": true, ...}`
4. 如需加载新配置，重启实例
5. 用 `rcon.list_players` 验证

启用或修改 MSMP：

1. `msmp.config.get`
2. `msmp.config.set {"dry_run": true, ...}`
3. `msmp.config.set {"confirm": true, ...}`
4. 如需加载新配置，重启实例
5. 用 `msmp.discover` 或 `msmp.server.status` 验证

整合包兼容测试：

1. `modpack.snapshot_modlist`
2. agent 外部检索候选版本资料
3. `modpack.apply_modlist {"dry_run": true}`
4. `modpack.apply_modlist {"confirm": true}`
5. 启动测试服并读日志
6. `modpack.classify_startup_result`
7. `modpack.record_test_run`
8. 必要时 `modpack.rollback_snapshot`

## 测试

本地检查：

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
python3 -m compileall -q src scripts
```

真实后端探针需要可丢弃的 MCSManager/Minecraft 测试环境：

```bash
python3 -B scripts/mcp_integration_probe.py > /tmp/minecraft-ops-mcp-probe-report.json
python3 -B scripts/msmp_temp_instance_probe.py > /tmp/minecraft-ops-mcp-msmp-probe-report.json
python3 -B scripts/multi_server_backend_probe.py > /tmp/minecraft-ops-mcp-multi-probe-report.json
```

多服务器探针会创建两个临时实例，验证同一个 MCP 进程可以按 `daemonId` 和 `uuid` 动态连接不同的 RCON/MSMP endpoint。

## 文档

- 用户手册：[docs/USER_MANUAL.md](docs/USER_MANUAL.md)
- 开发者手册：[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
- 当前不足与路线：[docs/LIMITATIONS.md](docs/LIMITATIONS.md)
- 测试报告：[docs/TEST_REPORT_2026-04-14.md](docs/TEST_REPORT_2026-04-14.md)
- 发布清单：[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)
- 安全说明：[SECURITY.md](SECURITY.md)
- 变更日志：[CHANGELOG.md](CHANGELOG.md)

建议与 `minecraft-ops-runbook` skill 配合使用。
