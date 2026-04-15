# Minecraft Ops MCP 开发者框架说明

本文面向维护者和二次开发者，说明项目结构、核心抽象、后端适配方式和扩展流程。

## 1. 设计目标

项目目标是把 Minecraft 运维能力封装为稳定、可审计、可被 agent 安全调用的 MCP tools。设计取向是：

- 统一语义层：agent 面向 `server.*`、`file.*`、`msmp.*` 等工具，不直接记忆各后端 API 细节。
- 多后端适配：MCSManager 管实例和文件，RCON 做命令兜底，MSMP 提供结构化 Minecraft 管理能力。
- 规范优先：MCP stdio 层使用官方 MCP Python SDK，避免项目自行维护 JSON-RPC 协议细节。
- 明确依赖：运行时依赖 `mcp`、`httpx` 和 `websocket-client`，其余后端适配仍尽量保持轻量。
- 防误操作：高风险工具必须 `confirm=true`，并提供 `dry_run=true`。
- 可观测：工具调用写审计日志，配置 resource 会脱敏。

## 2. 目录结构

```text
.
├── README.md
├── docs/
│   ├── USER_MANUAL.md
│   ├── DEVELOPER_GUIDE.md
│   └── LIMITATIONS.md
├── pyproject.toml
└── src/
    └── minecraft_ops_mcp/
        ├── __init__.py
        ├── __main__.py
        ├── adapters/
        │   ├── mcsm.py
        │   ├── msmp.py
        │   └── rcon.py
        ├── audit.py
        ├── config.py
        ├── errors.py
        ├── models.py
        ├── modpack.py
        ├── policy.py
        ├── server.py
        └── tools.py
```

## 3. 模块职责

`models.py`

- `Tool`：工具名、标题、描述、输入 JSON Schema、输出 JSON Schema、annotations、handler。
- `Resource`：资源 URI、标题、描述、MIME、读取函数。
- `Prompt`：提示词名、标题、参数和生成函数。

这些数据类是项目内部的工具目录模型；真正的 MCP 协议对象在 `server.py` 中转换为 SDK 的 `mcp.types.Tool`、`Resource`、`Prompt` 和 `CallToolResult`。

`server.py`

组装入口。读取环境变量生成 `AppConfig`，调用 `make_tools` 注册工具，注册 resources 和 prompts，然后通过官方 MCP Python SDK 启动 stdio server。

主要职责：

- 创建 `mcp.server.Server`。
- 注册 `list_tools`、`call_tool`、`list_resources`、`read_resource`、`list_resource_templates`、`list_prompts`、`get_prompt` handler。
- 使用 SDK 的 JSON Schema 校验能力处理工具入参；校验失败作为工具结果 `isError=true` 返回。
- 使用 `anyio.to_thread.run_sync(...)` 在线程池中执行同步工具 handler，避免阻塞 MCP stdio 事件循环。
- 工具调用成功时同时返回 text content 和 `structuredContent`；dict 结果原样作为结构化结果，list/标量包装为 `{ "result": ... }`。
- 将项目内部的 `Tool.annotations` 转换为 SDK `ToolAnnotations`。

Resources：

- `minecraft-ops://config`
- `minecraft-ops://safety`
- `minecraft-ops://tools`

Prompts：

- `minecraft_health_check`
- `minecraft_safe_restart`

`config.py`

定义配置数据类：

- `McsmConfig`
- `RconConfig`
- `MsmpConfig`
- `AppConfig`

配置来源只有环境变量。`AppConfig.redacted()` 用于向 MCP resource 暴露脱敏配置。

全局文件护栏：

- `MINECRAFT_OPS_MAX_BYTES`：文件上传/下载最大字节数，默认 256 MiB。
- `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS`：MCP 宿主机本地路径白名单，限制 `file.upload_local` 来源和 `file.download_local` 输出。
- `MINECRAFT_OPS_FILE_OPERATION_WHITELIST`：实例内远程路径前缀白名单，限制 `file.write*`、上传目录和下载源文件路径。
- `MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS`：`file.upload_url` 可拉取域名白名单，支持子域名。
- `MINECRAFT_OPS_MODPACK_WORKSPACE`：整合包快照与后续测试记录的本地工作区，默认 `/tmp/minecraft-ops-mcp-modpacks`。

`tools.py`

统一工具层。这里创建后端 client 与高层服务：

```python
mcsm = McsmClient(config)
rcon = RconClient(config)
msmp = MsmpClient(config)
modpack = ModpackManager(config, mcsm)
```

每个工具用 `Tool(...)` 注册。高风险操作通过 `action(...)` 调用 `guard_high_risk(...)`。所有 handler 由 `wrap(...)` 包裹，用于记录审计日志和统一错误流。

`modpack.py`

整合包元数据层。它不负责联网查询 Modrinth / CurseForge / GitHub Releases；这些外部资料由 agent 完成。当前职责是把可重复、可审计的本地事实结构化：

- `inspect_jar_file()`：解析 `fabric.mod.json`、`quilt.mod.json`、`META-INF/mods.toml`、`META-INF/neoforge.mods.toml`、`mcmod.info`，计算 sha256。
- `ModpackManager.inspect_jar()`：支持本地 jar 或通过 MCSManager 下载实例内 jar 到临时文件后解析。
- `ModpackManager.snapshot_modlist()`：扫描本地或实例内 `mods` 目录，生成 snapshot JSON，并保存到 `MINECRAFT_OPS_MODPACK_WORKSPACE/snapshots`；默认把 jar 缓存到 `blobs/`，供应用和回滚使用。
- `ModpackManager.diff_snapshots()`：按 jar 文件和解析出的 `modId` / 版本 / hash 比较两个快照。
- `ModpackManager.apply_modlist()`：根据目标 manifest/snapshot 生成 before 快照、上传缺失/变化 jar、删除额外 jar，再生成 after 快照。
- `ModpackManager.rollback_snapshot()`：把目标快照作为 desired manifest 复用 apply 流程恢复 `mods` 目录。
- `ModpackManager.classify_startup_result()`：从 inline 文本或 MCSManager 远程日志读取启动/崩溃文本，按签名分类常见兼容性问题。
- `ModpackManager.record_test_run()`、`list_test_runs()`、`get_test_run()`：在 `MINECRAFT_OPS_MODPACK_WORKSPACE/runs` 下保存和读取测试运行记录。
- `remote_paths` / `current_paths`：用于 MCSManager 目录 listing 不可靠时显式指定需要读取或比较的实例内 jar。

`policy.py`

集中维护安全策略：

- `HIGH_RISK_TOOLS`：需要确认的工具名集合。
- `guard_high_risk(...)`：处理 `dry_run` 和 `confirm`。
- `ensure_plain_command(...)`：限制原始命令只能单行。
- `ensure_raw_command_allowed(...)`：按 `MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST` 和 `MINECRAFT_OPS_RAW_COMMAND_DENYLIST` 做原始命令前缀过滤。
- `_is_read_only_msmp_call(...)`：只允许部分 MSMP 只读方法跳过确认。

`audit.py`

写 JSONL 审计日志。敏感字段名会被脱敏，包括 `apiKey`、`apikey`、`api_key`、`password`、`secret`、`token`。

`errors.py`

定义用户可预期错误：

- `OpsError`
- `ConfigError`
- `SafetyError`

这些错误在工具调用中会转成 `isError=true` 的 MCP 工具结果；在 resource/prompt 等非工具 handler 中由 SDK 转成 JSON-RPC 错误。

## 4. 后端适配器

### 4.1 MCSManager Adapter

文件：[mcsm.py](/home/damoc/codes/minecraft-ops-mcp/src/minecraft_ops_mcp/adapters/mcsm.py)

职责：

- 给所有请求追加 `apikey` query 参数。
- 设置 MCSManager 需要的请求头：
  - `Accept: application/json`
  - `X-Requested-With: XMLHttpRequest`
  - `Content-Type: application/json; charset=utf-8`
- 把 HTTP 错误和 MCSManager 非 200 状态封装成 `OpsError`。
- 暴露节点、实例、文件、上传下载和控制台命令 API。

主要方法：

- `list_daemons()` -> `/api/service/remote_services_list`
- `get_daemon_system()` -> `/api/service/remote_services_system`
- `list_instances()` -> `/api/service/remote_service_instances`
- `get_instance()` -> `/api/instance`
- `create_instance()` -> `POST /api/instance`
- `update_instance_config()` -> `PUT /api/instance`
- `instance.update_config_patch` 的工具层会先读取 `get_instance()`，提取配置并深度合并 patch，再调用 `update_instance_config()`。
- `instance.clone_from_template` 的工具层会从 `get_instance()` 提取配置，去掉部分运行态字段后调用 `create_instance()`。
- `delete_instances()` -> `DELETE /api/instance`
- `instance_action()` -> `/api/protected_instance/open|stop|restart|kill`
- `send_command()` -> `/api/protected_instance/command`
- `get_logs()` -> `/api/protected_instance/outputlog`
- `list_files()` -> `/api/files/list`
- `read_file()` / `write_file()` -> `PUT /api/files/`
- `prepare_upload()` -> `POST /api/files/upload`
- `upload_local_file()` -> daemon `/upload/{password}`，用 `httpx` multipart 流式上传，先用文件大小检查 `max_bytes`
- `upload_url_file()` -> 用 `httpx` 流式下载 URL 到临时文件，再复用 `upload_local_file()`
- `prepare_download()` -> `POST /api/files/download`
- `download_local_file()` -> daemon `/download/{password}/{filename}`，失败时回退 `/download/{password}`，用 `httpx` 流式写入 MCP 本机路径
- `write_new_file()` -> `read_file()` 检查存在性，再 `touch()` 和 `write_file()`
- `copy_files()` / `move_files()` / `delete_files()` / `compress()` / `uncompress()`

上传逻辑：

1. 调 MCSManager 面板 API 获取 `password` 和 daemon `addr`。
2. 由 `_daemon_url(...)` 拼出 daemon 上传 URL。
3. 校验本地路径、实例内路径、域名白名单和 `max_bytes`。
4. 用 `httpx` 流式传输，避免把上传/下载内容一次性读入内存。
5. 返回结果中只暴露 `daemonUploadUrlSet` / `daemonDownloadUrlSet`，不返回带 token 的 daemon URL。

### 4.2 RCON Adapter

文件：[rcon.py](/home/damoc/codes/minecraft-ops-mcp/src/minecraft_ops_mcp/adapters/rcon.py)

职责：

- 用 TCP socket 实现 Source RCON 协议。
- 认证后发送 `SERVERDATA_EXECCOMMAND`。
- 按 little-endian 格式编码/解码 RCON packet。

限制：

- 当前是每次命令新建连接。
- 多包响应通过额外 marker 请求减少等待 timeout，但复杂大输出仍可能需要继续压测。
- 不做 TLS，因为 RCON 协议本身没有 TLS。

工具层提供了低风险固定命令封装：

- `rcon.list_players()` -> `list`
- `rcon.time_query()` -> `time query daytime|gametime|day`
- `rcon.save_all()` -> `save-all` / `save-all flush`

### 4.3 MSMP Adapter

文件：[msmp.py](/home/damoc/codes/minecraft-ops-mcp/src/minecraft_ops_mcp/adapters/msmp.py)

职责：

- 用 `websocket-client` 建立 WebSocket 连接。
- 支持 `ws://` 和 `wss://`。
- 支持 `Authorization: Bearer <secret>`。
- 通过 WebSocket 发送 JSON-RPC 请求，等待匹配 `id` 的响应。
- WebSocket 握手、mask、ping/pong、分片等协议细节交给成熟客户端库处理。
- 工具层增加 `msmp.bans.*`、`msmp.ip_bans.*` 和 `msmp.server_settings.list`；其中 `server_settings.list` 通过递归扫描 `rpc.discover` 返回值提取 `minecraft:serversettings/*` 方法。

关键流程：

1. 解析 `MSMP_URL`。
2. 根据 `MSMP_SECRET` 设置 `Authorization: Bearer ...`。
3. 对 `wss://` 且 `MSMP_TLS_VERIFY=false` 的场景传入 TLS 校验选项。
4. 通过 `websocket.create_connection(...)` 建立连接。
5. 发送 JSON-RPC payload，并读取匹配 `id` 的响应。

## 5. 工具命名与分层

工具名分为几组：

- `server.*`：跨后端或实例生命周期工具。
- `instance.*`：MCSManager 实例配置工具。
- `file.*`：MCSManager 文件工具。
- `modpack.*`：整合包元数据、快照、应用/回滚和测试运行记录工具。
- `rcon.*`：RCON 工具。
- `msmp.*`：MSMP 结构化工具。

当前工具数为 80。新增工具应优先复用已有命名分层，只有直接映射后端协议的逃生口才保留 raw call/command。

工具命名使用 MCP 友好的点分形式，例如 `msmp.players.list`，避免直接暴露 `minecraft:players` 这种后端协议名。

## 6. 添加新工具的步骤

添加 MCSManager 工具：

1. 在 `adapters/mcsm.py` 增加 client 方法，负责 HTTP 请求。
2. 在 `tools.py` 中新增 `Tool(...)`。
3. 如果会修改状态，把工具名加入 `policy.py` 的 `HIGH_RISK_TOOLS`。
4. 为输入参数补完整 JSON Schema；SDK 会在 `tools/call` 前执行 JSON Schema 校验。
5. 在 `docs/USER_MANUAL.md` 和 README 工具列表中补说明。
6. 用 MCP stdio probe 测 `tools/list` 和 `tools/call`。

添加 MSMP 工具：

1. 先用 `msmp.discover` 确认当前服务端的方法名和 params schema。
2. 在 `tools.py` 中封装后端方法名和参数形状。
3. 只读工具不要加入 `HIGH_RISK_TOOLS`；写工具必须加入。
4. 对 `msmp.call` 的只读白名单，如有必要更新 `_is_read_only_msmp_call(...)`。

添加 RCON 工具：

通常不需要新增很多 RCON 子工具，因为 `rcon.command` 已经能发送任意命令。若要新增安全封装，比如 `rcon.list_players`：

1. 在 `tools.py` 增加工具并固定命令为 `list`。
2. 如果是纯查询，可以不加入 `HIGH_RISK_TOOLS`。
3. 不要让用户输入多行命令。

添加整合包元数据工具：

1. 优先把 jar 解析、快照、diff、apply、rollback 等可测试逻辑放在 `modpack.py`。
2. `tools.py` 只做 JSON Schema、参数转换和审计包装。
3. 本地路径读取必须服从 `MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS`；快照路径读取必须限制在 `MINECRAFT_OPS_MODPACK_WORKSPACE` 内。
4. 修改远程 `mods` 内容的工具必须加入 `HIGH_RISK_TOOLS` 并走 `dry_run` / `confirm`。
5. 外部资料查询不要放入 MCP，交给 agent 或 skill。

## 7. 测试方法

基础协议测试：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 - <<'PY'
from minecraft_ops_mcp.config import AppConfig
from minecraft_ops_mcp.tools import make_tools
print(len(make_tools(AppConfig.from_env())))
PY
```

语法测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src
find src -type d -name __pycache__ -prune -exec rm -r {} +
```

MCP 协议级集成探针：

```bash
python3 -B scripts/mcp_integration_probe.py > /tmp/minecraft-ops-mcp-probe-report.json
python3 -B scripts/msmp_temp_instance_probe.py > /tmp/minecraft-ops-mcp-msmp-probe-report.json
```

这两个脚本都通过 stdio JSON-RPC 调用 MCP server。第一个覆盖基础协议、MCSManager、RCON 和 MSMP dry-run；第二个会创建并删除临时 Minecraft 1.21.9+ 实例，真实回归 MSMP 读写能力。运行前需要在环境变量中配置 MCSManager，第二个脚本还会从 Mojang version manifest 获取 server jar，或使用 `MINECRAFT_SERVER_JAR_URL` 指定 jar。

MCP stdio 测试思路：

1. 按官方 Python SDK stdio transport 使用单行 JSON-RPC 消息。
2. 发送 `initialize`，包含 `protocolVersion`、`capabilities` 和 `clientInfo`。
3. 发送 `notifications/initialized`。
4. 发送 `tools/list`。
5. 对目标工具发送 `tools/call`。

真实后端测试顺序建议：

1. `server.list_daemons`
2. `server.list_instances`
3. `server.get_instance`
4. `file.read`
5. `file.touch -> file.write -> file.read -> file.delete`
6. `rcon.command {"command":"list","confirm":true}`
7. `modpack.inspect_jar -> modpack.snapshot_modlist -> modpack.diff_snapshots -> modpack.apply_modlist.dry_run -> modpack.rollback_snapshot.dry_run -> modpack.classify_startup_result -> modpack.record_test_run -> modpack.list_test_runs -> modpack.get_test_run`
8. `msmp.discover`
9. MSMP 读操作
10. MSMP 写操作，先 dry-run，再 confirm

## 8. 实测记录摘要

当前版本在真实 MCSManager 环境中验证过：

- MCSManager 版本 `4.12.2`。
- MCSManager 实例 `test`：Minecraft `1.21.1`，RCON 已启用并可用。
- RCON 命令 `list`、`time query daytime` 成功。
- `modpack.inspect_jar`、`modpack.snapshot_modlist`、`modpack.diff_snapshots`、`modpack.apply_modlist`、`modpack.rollback_snapshot`、`modpack.classify_startup_result`、`modpack.record_test_run`、`modpack.list_test_runs`、`modpack.get_test_run` 已通过 MCP stdio 探针覆盖；apply/rollback 还在真实 MCSManager 临时目录中做过 confirm 级回归。
- 临时 Minecraft `1.21.9` 实例成功启动 MSMP，`rpc.discover` 返回 84 个方法。
- MSMP 的 status、players、gamerules、settings、save、broadcast、allowlist、operators、kick fake target 均测过。
- 临时 MSMP 实例已删除，测试产物已清理。

不要把上述测试环境的密钥写入文档或提交历史。

## 9. 打包与发布

`pyproject.toml` 已定义 console script：

```toml
[project.scripts]
minecraft-ops-mcp = "minecraft_ops_mcp.server:main"
```

本地开发安装：

```bash
python3 -m pip install -e .
```

运行时依赖目前包括：

- `mcp`：官方 MCP Python SDK，负责 stdio transport、协议对象和 JSON Schema 校验。
- `httpx`：MCSManager daemon 上传/下载和 URL staging 的流式 HTTP 客户端。
- `websocket-client`：MSMP JSON-RPC over WebSocket 传输。

后续发布前建议继续补 fake MCSManager/RCON/MSMP server，用自动化测试覆盖更多边界。
