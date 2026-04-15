# Minecraft Ops MCP 用户手册

本文面向 MCP 使用者，说明如何配置、调用和安全地使用 `minecraft-ops-mcp` 管理 Minecraft 服务器。

## 1. 能力概览

`minecraft-ops-mcp` 是一个通过 stdio 运行的 MCP 服务，给 agent 暴露 Minecraft 运维工具。它当前支持 3 类后端：

- MCSManager API：管理节点、实例、日志、文件、上传、实例启停、控制台命令。
- RCON：向 Minecraft 服务端发送传统 RCON 命令。
- MSMP：Minecraft Java 1.21.9+ 的 Minecraft Server Management Protocol，用于结构化查询和修改玩家、白名单、OP、游戏规则和服务器设置。

推荐使用方式：

- 实例、文件、日志、上传下载：优先使用 MCSManager 工具。
- 玩家、白名单、OP、gamerule、server settings：优先使用 MSMP 工具。
- 旧版本服务端或 MSMP 没覆盖的命令：使用 RCON 或 MCSManager 控制台命令兜底。

## 2. 安装与启动

项目需要 Python 3.12+，并依赖官方 MCP Python SDK 与 `websocket-client`。推荐先安装为可编辑包：

```bash
cd /home/damoc/codes/minecraft-ops-mcp
python3 -m pip install -e .
minecraft-ops-mcp
```

开发调试时也可以直接从源码运行：

```bash
cd /home/damoc/codes/minecraft-ops-mcp
PYTHONPATH=src python3 -m minecraft_ops_mcp
```

## 3. MCP 客户端配置

把下面配置加入你的 MCP 客户端。请替换其中的占位值，不要把真实密钥提交到仓库。

```json
{
  "mcpServers": {
    "minecraft-ops": {
      "command": "python3",
      "args": ["-m", "minecraft_ops_mcp"],
      "cwd": "/home/damoc/codes/minecraft-ops-mcp",
      "env": {
        "PYTHONPATH": "src",
        "MCSM_BASE_URL": "http://your-mcsm-host:23333",
        "MCSM_API_KEY": "replace-me",
        "MCSM_DEFAULT_DAEMON_ID": "replace-me",
        "MCSM_DEFAULT_INSTANCE_UUID": "replace-me",
        "RCON_HOST": "your-server-host",
        "RCON_PORT": "25575",
        "RCON_PASSWORD": "replace-me",
        "MSMP_URL": "ws://your-server-host:25586",
        "MSMP_SECRET": "replace-me",
        "MSMP_TLS_VERIFY": "true",
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

常用环境变量：

- `MCSM_BASE_URL`：MCSManager 面板地址，例如 `http://host:23333`。
- `MCSM_API_KEY`：MCSManager API key。
- `MCSM_DEFAULT_DAEMON_ID`：默认 daemon 节点 ID，省略工具参数时使用。
- `MCSM_DEFAULT_INSTANCE_UUID`：默认实例 UUID，省略工具参数时使用。
- `RCON_HOST`、`RCON_PORT`、`RCON_PASSWORD`：RCON 连接信息。
- `MSMP_URL`：MSMP WebSocket 地址，例如 `ws://host:25586` 或 `wss://host:25586`。
- `MSMP_SECRET`：MSMP Bearer secret。Minecraft 1.21.9 要求它是 40 位字母数字。
- `MSMP_TLS_VERIFY`：是否校验 wss 证书，默认 `true`。
- `MINECRAFT_OPS_AUDIT_LOG`：审计日志位置，默认 `/tmp/minecraft-ops-mcp-audit.jsonl`；置空可关闭。
- `MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST`：可选，逗号分隔的原始命令前缀白名单，例如 `list,time,help`。
- `MINECRAFT_OPS_RAW_COMMAND_DENYLIST`：可选，逗号分隔的原始命令前缀黑名单，例如 `stop,op,deop,ban,ban-ip`。
- `MINECRAFT_OPS_MAX_BYTES`：文件上传/下载最大字节数，默认 `268435456`。
- `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS`：可选，逗号分隔的 MCP 宿主机目录白名单，限制 `file.upload_local` 的本地来源和 `file.download_local` 的本地输出位置。
- `MINECRAFT_OPS_FILE_OPERATION_WHITELIST`：可选，逗号分隔的实例内路径前缀白名单，限制写入、上传目录和下载源文件路径。
- `MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS`：可选，逗号分隔的 `file.upload_url` 远程 URL 域名白名单；支持子域名匹配。
- `MINECRAFT_OPS_MODPACK_WORKSPACE`：整合包快照 JSON 保存目录，默认 `/tmp/minecraft-ops-mcp-modpacks`。

## 4. 安全模型

高风险工具默认不会执行，必须显式传入：

```json
{ "confirm": true }
```

如果只想让 agent 预览会执行什么，传：

```json
{ "dry_run": true }
```

高风险操作包括：

- 实例启停、重启、强杀、删除、重装、更新任务。
- 原始控制台命令和 RCON 命令。
- 文件写入、删除、移动、复制、压缩、解压、上传，以及下载到 MCP 本机。
- MSMP 的踢人、保存、停服、封禁、白名单、OP、游戏规则和服务器设置修改。

工具调用会写入审计日志，参数中的密码、token、secret 等敏感字段会被脱敏。审计日志不要公开。

如果配置了原始命令 allowlist，`server.send_command` 和 `rcon.command` 只允许命中白名单前缀的单行命令；如果配置了 denylist，命中黑名单前缀的命令会被拒绝。前缀按命令首段匹配，例如 `ban Steve` 会命中 `ban`。

## 5. MCP Resources 和 Prompts

可读取的 resources：

- `minecraft-ops://config`：脱敏后的后端配置状态。
- `minecraft-ops://safety`：高风险工具列表和确认规则。
- `minecraft-ops://tools`：完整工具目录和 JSON Schema。

内置 prompts：

- `minecraft_health_check`：健康检查流程，适合先看实例、日志、玩家和状态。
- `minecraft_safe_restart`：安全重启流程，先检查、广播、保存，再 dry-run，最后确认重启。

## 6. MCSManager 工具

### 6.1 节点与实例只读工具

`server.list_daemons`

列出 MCSManager daemon 节点。常用于获取 `daemonId`。

```json
{}
```

`server.get_daemon_system`

读取 daemon 系统状态摘要，例如 daemon 版本、CPU、内存、实例数量。

```json
{}
```

`server.list_instances`

列出某 daemon 下的实例。

```json
{
  "daemonId": "optional-daemon-id",
  "page": 1,
  "page_size": 20,
  "instance_name": "",
  "status": ""
}
```

`server.get_instance`

读取实例详情。如果配置了默认 daemon 和实例 UUID，可不传参数。

```json
{
  "daemonId": "optional-daemon-id",
  "uuid": "optional-instance-uuid"
}
```

`server.get_logs`

读取实例输出日志。

```json
{
  "daemonId": "optional-daemon-id",
  "uuid": "optional-instance-uuid",
  "size": 2048
}
```

### 6.2 实例生命周期工具

这些工具都要求 `confirm=true`，或用 `dry_run=true` 预览。

`server.start`

启动实例。

```json
{ "confirm": true }
```

`server.stop`

优雅停止实例。

```json
{ "confirm": true }
```

`server.restart`

重启实例。

```json
{ "confirm": true }
```

`server.kill`

强制结束实例进程。只在普通停止失败时使用。

```json
{ "dry_run": true }
```

`server.send_command`

通过 MCSManager 控制台发送单行命令。

```json
{
  "command": "list",
  "confirm": true
}
```

### 6.3 实例配置工具

`instance.create`

创建实例，`config` 使用 MCSManager 的 InstanceConfig 对象。建议先 dry-run，再确认执行。

```json
{
  "daemonId": "daemon-id",
  "config": {
    "nickname": "example",
    "startCommand": "java -jar server.jar nogui",
    "stopCommand": "stop",
    "cwd": "/opt/mcsmanager/daemon/data/InstanceData/example",
    "type": "minecraft/java",
    "processType": "general"
  },
  "dry_run": true
}
```

`instance.update_config`

更新实例配置。传入完整或 MCSManager 接受的配置对象。

```json
{
  "config": {
    "nickname": "example",
    "enableRcon": true,
    "rconIp": "127.0.0.1",
    "rconPort": 25575,
    "rconPassword": "replace-me"
  },
  "confirm": true
}
```

`instance.update_config_patch`

先读取当前实例配置，深度合并 `patch`，再提交更新。适合只改少数字段；建议先 dry-run 查看 diff。

```json
{
  "patch": {
    "enableRcon": true,
    "rconIp": "127.0.0.1",
    "rconPort": 25575
  },
  "dry_run": true
}
```

`instance.clone_from_template`

读取一个现有实例的配置，去掉明显的运行态字段后创建新实例。实际可用性仍取决于 MCSManager 的 InstanceConfig 字段是否完整，生产使用前必须 dry-run 并检查 `cwd`、`nickname`、端口和启动命令。

```json
{
  "source_daemonId": "source-daemon-id",
  "source_uuid": "source-instance-uuid",
  "daemonId": "target-daemon-id",
  "nickname": "new-pack-test",
  "cwd": "/opt/mcsmanager/daemon/data/InstanceData/new-pack-test",
  "overrides": {
    "startCommand": "java -Xmx4G -jar server.jar nogui"
  },
  "dry_run": true
}
```

`instance.delete`

删除实例，可选择一并删除实例文件。

```json
{
  "daemonId": "daemon-id",
  "uuids": ["instance-uuid"],
  "deleteFile": true,
  "confirm": true
}
```

`instance.reinstall`

从安装包 URL 重装实例。

```json
{
  "targetUrl": "https://example.com/server.zip",
  "title": "reinstall",
  "description": "operator approved",
  "dry_run": true
}
```

`instance.run_update_task`

运行实例配置里的 update task。

```json
{ "dry_run": true }
```

## 7. 文件工具

MCSManager 文件路径建议使用相对路径，例如 `server.properties`、`world/level.dat`。部分 MCSManager 版本对 `/xxx` 这类根路径写入会报 `Illegal access path`。

`file.list`

列出目录内容。

```json
{
  "target": "/",
  "page": 0,
  "page_size": 100
}
```

注意：实测某些 MCSManager 实例会返回正确 `absolutePath` 但 `items` 为空；这不影响 `file.read`、`file.write`、上传、复制、移动等工具。

`file.read`

读取文本文件。

```json
{ "target": "server.properties" }
```

`file.touch`

创建空文件。对不存在的新文件，建议先 `touch` 再 `write`。

```json
{ "target": "notes.txt" }
```

`file.write`

写入文本文件。要求 `confirm=true`。

```json
{
  "target": "notes.txt",
  "text": "hello\n",
  "confirm": true
}
```

`file.write_new`

对新文件执行 `touch -> write`，默认不覆盖已存在文件。这个工具用于规避部分 MCSManager 版本直接写不存在文件时的路径/创建问题。

```json
{
  "target": "notes.txt",
  "text": "hello\n",
  "overwrite": false,
  "confirm": true
}
```

`file.mkdir`

创建目录。

```json
{ "target": "backups" }
```

`file.copy`

复制文件或目录，`targets` 是 `[source, target]` 数组列表。

```json
{
  "targets": [["notes.txt", "notes-copy.txt"]],
  "confirm": true
}
```

`file.move`

移动或重命名文件或目录。

```json
{
  "targets": [["notes-copy.txt", "archive/notes.txt"]],
  "confirm": true
}
```

`file.delete`

删除文件或目录。

```json
{
  "targets": ["notes.txt", "archive"],
  "confirm": true
}
```

`file.compress`

创建 zip 压缩包。

```json
{
  "source": "world-backup.zip",
  "targets": ["world"],
  "confirm": true
}
```

`file.uncompress`

解压 zip。

```json
{
  "source": "world-backup.zip",
  "target": "restore",
  "code": "utf-8",
  "confirm": true
}
```

`file.upload_prepare`

创建临时上传 token。一般只有调试或自定义上传流程才需要直接用。

```json
{ "upload_dir": "/" }
```

`file.upload_local`

把 MCP 服务所在机器上的本地文件流式上传到实例目录，不会一次性把文件读入内存。受 `MINECRAFT_OPS_MAX_BYTES` 和 `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS` 约束。

```json
{
  "upload_dir": "/",
  "local_path": "/tmp/server.jar",
  "remote_name": "server.jar",
  "max_bytes": 268435456,
  "confirm": true
}
```

`file.upload_url`

MCP 服务先从 `http://` 或 `https://` URL 流式下载到临时文件，再流式上传到实例目录。默认最大文件大小来自 `MINECRAFT_OPS_MAX_BYTES`，也可用工具参数 `max_bytes` 调整。若配置了 `MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS`，只允许从白名单域名或其子域名拉取。

```json
{
  "url": "https://example.com/server.jar",
  "upload_dir": "/",
  "remote_name": "server.jar",
  "max_bytes": 536870912,
  "confirm": true
}
```

如果 MCSManager daemon 返回 `localhost:24444` 但 MCP 机器不能访问这个地址，可传：

```json
{
  "upload_dir": "/",
  "local_path": "/tmp/server.jar",
  "remote_name": "server.jar",
  "daemon_public_base_url": "http://daemon-host:24444",
  "confirm": true
}
```

`file.download_prepare`

创建临时下载 token。一般只有调试或自定义下载流程才需要直接用。

```json
{ "file_name": "server.properties" }
```

`file.download_local`

把实例中的单个文件流式下载到 MCP 服务所在机器。默认输出到 `/tmp/minecraft-ops-mcp-downloads/<文件名>`；如果指定 `local_path` 且文件已存在，默认会拒绝覆盖。受 `MINECRAFT_OPS_MAX_BYTES` 和 `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS` 约束。

```json
{
  "file_name": "server.properties",
  "local_path": "/tmp/minecraft-ops-mcp-downloads/server.properties",
  "max_bytes": 268435456,
  "overwrite": true,
  "confirm": true
}
```

## 8. 整合包元数据工具

这些工具用于兼容性测试：解析 mod jar 元数据、生成 modlist 快照、比较两次快照差异，并把目标 modlist 应用到测试实例或回滚到旧快照。外部版本资料查询仍由 agent 完成，MCP 负责真实文件、hash、jar 内元数据、应用计划和可追溯快照。

`modpack.inspect_jar`

解析单个 jar。可以读取 MCP 宿主机本地 jar，也可以读取实例内 jar；读取本地 jar 时受 `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS` 约束。

```json
{
  "remote_path": "mods/example.jar",
  "max_bytes": 268435456
}
```

返回字段包括 `sha256`、`metadataFiles`、`detectedLoaders`、`primaryMod`、`mods` 和解析错误列表。当前支持 `fabric.mod.json`、`quilt.mod.json`、`META-INF/mods.toml`、`META-INF/neoforge.mods.toml`、`mcmod.info`。

`modpack.snapshot_modlist`

扫描 `mods` 目录中的 jar，生成结构化快照。默认保存到 `MINECRAFT_OPS_MODPACK_WORKSPACE/snapshots/<snapshot_id>.json`，并把 jar 内容缓存到 `MINECRAFT_OPS_MODPACK_WORKSPACE/blobs/`，供后续应用或回滚使用。

```json
{
  "mods_dir": "mods",
  "snapshot_name": "baseline-before-updating-sodium",
  "minecraft_version": "1.21.1",
  "loader": "fabric",
  "save": true
}
```

也可以扫描 MCP 宿主机本地目录：

```json
{
  "local_dir": "/srv/minecraft-staging/mods",
  "snapshot_name": "candidate-a",
  "save": true
}
```

如果当前 MCSManager 版本的目录 listing 不可靠，可以显式传入实例内 jar 路径：

```json
{
  "mods_dir": "mods",
  "remote_paths": ["mods/sodium.jar", "mods/iris.jar"],
  "snapshot_name": "known-current"
}
```

`modpack.diff_snapshots`

比较两个快照。可以直接传入快照对象，也可以传 `before_path` / `after_path` 或 `before_snapshot_id` / `after_snapshot_id`。路径必须位于 `MINECRAFT_OPS_MODPACK_WORKSPACE` 内。

```json
{
  "before_snapshot_id": "20260415T010000Z-baseline-abcdef123456",
  "after_snapshot_id": "20260415T011500Z-candidate-a-fedcba654321"
}
```

结果会列出文件层面的新增、删除、变化，以及按 `modId` 解析出的新增、删除、版本变化；同版本但 hash 改变会进入 warnings。

`modpack.apply_modlist`

把目标快照或 lockfile 应用到实例内 `mods` 目录。该工具会先生成 before 快照作为回滚点，再上传缺失/变化 jar，并在 `clean_extra=true` 时删除目标快照中不存在的额外 jar。必须先 `dry_run=true` 查看计划，确认后再 `confirm=true`。

```json
{
  "manifest_path": "/srv/minecraft-ops/modpack-workspace/snapshots/candidate-a.json",
  "mods_dir": "mods",
  "clean_extra": true,
  "dry_run": true
}
```

如果目录 listing 不可靠，传入当前已知 jar 路径：

```json
{
  "manifest": { "kind": "modpackSnapshot" },
  "mods_dir": "mods",
  "current_paths": ["mods/old-sodium.jar", "mods/old-iris.jar"],
  "confirm": true
}
```

`modpack.rollback_snapshot`

将实例内 `mods` 目录恢复到某个快照。它内部复用 apply 计划：上传快照中的 jar，删除快照外的 jar。也必须先 dry-run。

```json
{
  "snapshot_id": "20260415T010000Z-baseline-abcdef123456",
  "mods_dir": "mods",
  "current_paths": ["mods/sodium.jar", "mods/iris.jar"],
  "dry_run": true
}
```

对空快照回滚是合法操作，含义是删除当前 `mods_dir` 中已知的 jar；在 listing 不可靠的环境中必须提供 `current_paths`。

`modpack.classify_startup_result`

根据 `latest.log`、控制台摘录或 crash report 判断一次启动结果。可以直接传文本，也可以传实例内远程路径。工具只做签名级辅助分类，不替代人工阅读完整日志。

```json
{
  "log_path": "logs/latest.log",
  "crash_report_path": "crash-reports/crash-2026-04-15_12.00.00-server.txt",
  "max_chars": 262144
}
```

常见分类包括：

- `mod_resolution`：Fabric/Quilt/Forge 依赖解析失败、缺依赖或版本范围不满足。
- `java_version`：Java 版本低于服务端或 mod 编译目标。
- `mixin_failure`：mixin 应用失败，通常需要定位 owning mod 与目标 MC/loader 版本。
- `binary_incompatibility`：`NoSuchMethodError`、`NoSuchFieldError` 等版本组合不兼容。
- `missing_dependency_or_wrong_side`：缺类、客户端 mod 放到服务端、环境侧错误。
- `duplicate_mod`、`config_error`、`port_conflict`、`startup_failure`。

返回结果包含 `status`、`category`、`confidence`、匹配签名、证据行和建议下一步。

`modpack.record_test_run`

保存一次兼容性测试运行记录到 `MINECRAFT_OPS_MODPACK_WORKSPACE/runs/<run_id>.json`。适合在每次版本组合测试后记录：候选组合、before/after 快照、apply/rollback 摘要、启动分类、日志摘录、外部资料链接和备注。

```json
{
  "run_name": "sodium-iris-candidate-a",
  "scenario": "startup",
  "outcome": "failed",
  "target": {
    "minecraftVersion": "1.21.1",
    "loader": "fabric"
  },
  "candidate": {
    "changedMods": ["sodium", "iris"]
  },
  "before_snapshot_id": "20260415T010000Z-baseline-abcdef123456",
  "after_snapshot_id": "20260415T011500Z-candidate-a-fedcba654321",
  "classification": {
    "status": "failure",
    "category": "mod_resolution"
  },
  "log_excerpt": "ModResolutionException: ...",
  "tags": ["compat", "startup"],
  "notes": "候选 A 缺少依赖，下一轮只替换该依赖。"
}
```

日志摘录会被限制长度；不要把完整大日志直接当作测试记录长期保存。完整日志仍应保留在实例或外部日志系统中。

`modpack.list_test_runs`

列出已保存的测试运行摘要，可按 `outcome`、`scenario` 或 `tag` 过滤。

```json
{
  "limit": 20,
  "outcome": "failed",
  "tag": "startup"
}
```

`modpack.get_test_run`

读取单条测试运行记录。可以传 `run_id` 或 workspace 内的 `run_path`。

```json
{
  "run_id": "20260415T020000Z-sodium-iris-candidate-a-abcdef123456"
}
```

## 9. RCON 工具

`rcon.command`

向 Minecraft RCON 发送单行命令。要求配置 `RCON_HOST`、`RCON_PORT`、`RCON_PASSWORD`，并传 `confirm=true`。

```json
{
  "command": "list",
  "confirm": true
}
```

RCON 是明文协议，不建议暴露到公网。优先绑定 localhost、VPN 或隧道。

低风险 RCON 封装：

`rcon.list_players`

固定执行 `list`，用于查看在线玩家，不需要 `confirm=true`。

```json
{}
```

`rcon.time_query`

固定执行 `time query daytime|gametime|day`。

```json
{ "query": "daytime" }
```

`rcon.save_all`

固定执行 `save-all` 或 `save-all flush`。

```json
{ "flush": true }
```

## 10. MSMP 工具

MSMP 需要 Minecraft Java 1.21.9+，并在 `server.properties` 开启管理服务。示例配置：

```properties
management-server-enabled=true
management-server-host=0.0.0.0
management-server-port=25586
management-server-secret=FortyAlphanumericCharactersOnly123456
management-server-tls-enabled=false
```

其中 `management-server-secret` 必须是 40 位字母数字。生产环境建议启用 TLS 或只在可信网络中开放端口。

`msmp.discover`

调用 `rpc.discover`，返回当前服务端支持的方法和 schema。

```json
{}
```

`msmp.call`

调用任意 MSMP JSON-RPC 方法。只读白名单方法可传 `read_only=true`，其他方法必须 `confirm=true` 或 `dry_run=true`。

```json
{
  "method": "minecraft:server/status",
  "read_only": true
}
```

`msmp.players.list`

获取在线玩家。

```json
{}
```

`msmp.players.kick`

踢出玩家。

```json
{
  "players": [{"name": "Steve"}],
  "message": "maintenance",
  "confirm": true
}
```

`msmp.server.status`

读取服务端状态和版本。

```json
{}
```

`msmp.server.save`

保存服务端状态。

```json
{
  "flush": true,
  "confirm": true
}
```

`msmp.server.stop`

停止服务端。

```json
{ "confirm": true }
```

`msmp.bans.get/add/remove/set/clear`

管理玩家封禁列表。`set` 会替换整个封禁列表，`clear` 会清空封禁列表；使用前建议先 `get` 保存当前状态。

```json
{
  "players": [{"name": "Steve"}],
  "reason": "rule violation",
  "source": "operator",
  "confirm": true
}
```

`msmp.ip_bans.get/add/remove/set/clear`

管理 IP 封禁列表。

```json
{
  "ips": ["203.0.113.10"],
  "reason": "abuse",
  "source": "operator",
  "confirm": true
}
```

`msmp.allowlist.get/add/remove/set/clear`

管理白名单。

```json
{
  "players": [{"name": "Alex"}],
  "confirm": true
}
```

`set` 会替换整个白名单，`clear` 会清空白名单，使用前建议先 `get` 保存当前状态。

`msmp.operators.get/add/remove/set/clear`

管理 OP。

```json
{
  "players": [{"name": "Alex"}],
  "permission_level": 4,
  "bypasses_player_limit": false,
  "confirm": true
}
```

`set` 会替换整个 OP 列表，`clear` 会清空 OP 列表，使用前建议先 `get` 保存当前状态。

`msmp.gamerules.get`

读取所有 game rules。

```json
{}
```

`msmp.gamerules.update`

修改一个 game rule。工具会把布尔值转换成 MSMP 要求的字符串。

```json
{
  "rule": "doDaylightCycle",
  "value": false,
  "confirm": true
}
```

`msmp.server_settings.get`

读取一个 server setting。

```json
{ "setting": "difficulty" }
```

`msmp.server_settings.list`

调用 `rpc.discover` 并提取当前服务端暴露的 `minecraft:serversettings/*` 方法，返回每个 setting 是否可读、可写，以及 MCP 已知的基础类型/枚举信息。

```json
{}
```

`msmp.server_settings.set`

设置一个 server setting。对已知 setting 会做基础类型校验，例如 `difficulty` 必须是 `peaceful/easy/normal/hard`，`view_distance` 必须是整数，布尔 setting 必须传布尔值。

```json
{
  "setting": "difficulty",
  "value": "normal",
  "confirm": true
}
```

常见 setting 包括 `difficulty`、`motd`、`max_players`、`view_distance`、`simulation_distance`、`use_allowlist`、`enforce_allowlist`、`game_mode` 等。完整列表以 `msmp.discover` 返回为准。

## 11. 跨后端便捷工具

`server.save_world`

按 `backend` 选择保存世界：

- `auto`：优先 MSMP，其次 RCON，再次 MCSManager。
- `msmp`：调用 `minecraft:server/save`。
- `rcon`：执行 `save-all`。
- `mcsm`：通过 MCSManager 控制台发送 `save-all`。

```json
{
  "backend": "msmp",
  "flush": true
}
```

`server.broadcast`

按 `backend` 发送广播：

```json
{
  "backend": "msmp",
  "message": "Server maintenance in 5 minutes",
  "overlay": false
}
```

## 12. 推荐操作流程

健康检查：

1. 读取 `minecraft-ops://config`。
2. 调 `server.list_daemons` 和 `server.list_instances`。
3. 调 `server.get_instance`、`server.get_logs`。
4. 如果有 MSMP，调 `msmp.server.status`、`msmp.players.list`。

安全重启：

1. `msmp.players.list` 或 `rcon.command {"command":"list"}` 查看在线玩家。
2. `server.broadcast` 提前通知。
3. `server.save_world` 保存。
4. `server.restart {"dry_run":true}` 预览。
5. 用户确认后 `server.restart {"confirm":true}`。

创建 1.21.9+ MSMP 测试实例：

1. `instance.create` 创建空实例。
2. `file.upload_local` 上传官方 server jar。
3. `file.touch` 和 `file.write` 写 `eula.txt`、`server.properties`、`run.sh`。
4. `server.start` 启动。
5. `msmp.discover` 验证 MSMP。
