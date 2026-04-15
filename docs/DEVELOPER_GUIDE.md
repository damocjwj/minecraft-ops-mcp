# 开发者手册

本文说明项目结构和新增工具时需要遵守的约定。

## 1. 架构

`minecraft-ops-mcp` 是基于官方 MCP Python SDK 的 MCP 服务。默认 transport 是 stdio，同时提供旧版 HTTP+SSE 和当前 Streamable HTTP。

主要模块：

- `server.py`：注册 MCP tools、resources、prompts，并挂载 stdio、SSE、Streamable HTTP transport。
- `tools.py`：工具目录、schema、输出 schema、安全包装和后端路由。
- `config.py`：环境变量解析和脱敏配置视图。
- `policy.py`：高风险工具 gating 和原始命令 allow/deny 策略。
- `audit.py`：JSONL 审计记录和敏感字段脱敏。
- `managed_backends.py`：从 MCSManager 动态推导实例级 RCON/MSMP 运行时配置。
- `adapters/`：MCSManager、RCON、MSMP 协议客户端。
- `modpack.py`：jar 元数据、快照、diff、应用/回滚、测试记录。

`models.py` 中的内部 `Tool` 模型会在 `server.py` 中转换成 MCP SDK 对象。

Transport 实现：

- stdio：SDK `stdio_server()`。
- SSE：SDK `SseServerTransport`，默认 `GET /sse` 和 `POST /messages/?session_id=...`。
- Streamable HTTP：SDK `StreamableHTTPSessionManager`，默认 `/mcp`。
- HTTP 模式的 `/health` 返回版本和端点信息，不包含密钥。
- `BearerAuthMiddleware` 在配置 `MINECRAFT_OPS_MCP_BEARER_TOKEN` 后保护 MCP HTTP 端点；`/` 和 `/health` 保持公开。

HTTP transport 默认启用 SDK `TransportSecuritySettings` 的 Host/Origin 校验。绑定 `0.0.0.0`、反向代理或域名访问时，必须配置允许的 Host/Origin。非本机绑定默认要求 bearer token，除非显式启用 `MINECRAFT_OPS_MCP_ALLOW_UNAUTHENTICATED_HTTP`。

## 2. 配置模型

MCP 客户端只配置全局 MCSManager：

- `MCSM_BASE_URL`
- `MCSM_API_KEY`
- 可选 `MCSM_DEFAULT_DAEMON_ID`
- 可选 `MCSM_DEFAULT_INSTANCE_UUID`

RCON 和 MSMP 每次调用动态解析：

- `rcon_runtime_config()` 读取实例配置 `enableRcon`、`rconIp`、`rconPort`、`rconPassword`。
- `msmp_runtime_config()` 解析 `server.properties` 中的 `management-server-*`。
- `derive_connection_host()` 会把 `0.0.0.0`、`127.0.0.1`、`localhost` 或空 host 转换为 MCSManager host；调用方也可传 `connection_host` 覆盖。

因此一个 MCP 进程可以管理多个实例，不需要固定 RCON 或 MSMP 客户端环境变量。

## 3. Adapters

MCSManager adapter：

- 封装面板 API 和 daemon 文件传输；
- 本地上传、URL staging、下载均使用流式处理；
- 执行本地路径、实例路径、URL 域名和大小限制；
- 将 HTTP 或 MCSManager 非 200 返回转换成 `OpsError`。

RCON adapter：

- 实现 Source RCON TCP 协议；
- 每次调用接收显式 `RconConnection`；
- 不从环境变量读取 host、port、password。

MSMP adapter：

- 使用 `websocket-client`；
- 每次调用接收显式 `MsmpConnection`；
- 实现 JSON-RPC 请求/响应匹配；
- 不从环境变量读取 URL 或 secret。

## 4. 工具目录

工具分组：

- `server.*`：生命周期、日志、保存、广播、控制台命令。
- `instance.*`：MCSManager 实例配置和实例任务。
- `file.*`：MCSManager 文件操作。
- `rcon.*`：实例级 RCON 配置和命令。
- `msmp.*`：实例级 MSMP 配置和结构化操作。
- `modpack.*`：兼容性元数据、快照、应用/回滚、测试记录。

当前工具数：84。

新增工具流程：

1. 只有现有 adapter 不满足时才扩展 adapter；
2. 在 `tools.py` 添加 `Tool(...)`；
3. 补充 input schema 和 output schema；
4. 如果会改变状态或本地文件，加入 `HIGH_RISK_TOOLS`；
5. 检查 dry-run preview 和输出是否会泄露 secret；
6. 增加单元测试，并尽量补真实 probe 覆盖。

优先新增具体工具，不要把能力都推给 `server.send_command`、`rcon.command` 或 `msmp.call`。

## 5. 安全与审计

高风险操作必须经过 `guard_high_risk()`：

- `dry_run=true` 返回预览，不执行；
- `confirm=true` 执行；
- 两者都没有则返回安全错误。

原始命令策略：

- `ensure_plain_command()` 拒绝多行命令；
- `ensure_raw_command_allowed()` 做精确命令词 allow/deny 匹配。

审计记录包含工具名、脱敏参数、结果和可选错误。脱敏覆盖常见敏感字段名和包含 password/secret 的配置行。

## 6. 整合包子系统

`ModpackManager` 支持：

- `inspect_jar()`：解析 Fabric、Quilt、Forge、NeoForge 和 legacy metadata。
- `snapshot_modlist()`：扫描本地或实例内 jar，保存 snapshot JSON，并缓存 jar blob。
- `diff_snapshots()`：比较 mod id、名称、版本和文件 hash。
- `apply_modlist()`：按 dry-run 或 confirm 上传、替换、删除 jar。
- `rollback_snapshot()`：恢复已缓存的历史快照。
- `classify_startup_result()`：分类日志和崩溃报告。
- `record_test_run()`、`list_test_runs()`、`get_test_run()`：在 `MINECRAFT_OPS_MODPACK_WORKSPACE` 下持久化测试证据。

当 MCSManager 目录 listing 不可靠时，使用 `remote_paths` 或 `current_paths` 显式指定 jar。

## 7. 测试

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

覆盖范围：

- `mcp_integration_probe.py`：stdio 协议、resources、prompts、MCSManager 文件/生命周期基础、RCON/MSMP dry-run、modpack 流程。
- `msmp_temp_instance_probe.py`：创建临时 Minecraft 1.21.9 实例并做真实 MSMP 读写。
- `multi_server_backend_probe.py`：创建两个临时实例，分别配置 RCON/MSMP，并在一个 MCP 进程中交替调用。

当前真实环境只有一个 MCSManager daemon，因此多服务器探针已覆盖同 daemon 多实例。跨 daemon 使用同样的 per-call `daemonId` 路径，但仍需要多 daemon 环境实测。

## 8. 快速配置脚本

`scripts/quick_setup.py` 用于本地快速安装 MCP 与 skill：

- dry-run 默认只展示计划；
- `--write` 才会写入 `$CODEX_HOME` 并调用 `codex mcp add`；
- `--replace` 会备份并替换已有 env、launcher、skill 或 MCP server；
- `--print-json` 输出通用 MCP client JSON；
- `--mcp-transport streamable-http` 输出或注册 HTTP URL；
- 默认把 API key 写入 0600 env 文件，Codex config 只保存 launcher 路径。

脚本不依赖项目包导入，便于在依赖未安装前运行。修改脚本后至少运行：

```bash
python3 -m py_compile scripts/quick_setup.py
python3 scripts/quick_setup.py --print-json
python3 scripts/quick_setup.py --mcp-transport streamable-http --print-json
```

## 9. 发布检查

发布前：

1. 运行本地检查；
2. 在可丢弃环境运行真实后端探针；
3. 对仓库和 probe 报告做 secret scan；
4. 如果公共工具面变化，更新 `__version__`、`pyproject.toml`、`CHANGELOG.md` 和测试文档；
5. 用 `python3 -m build --wheel` 构建 wheel；
6. 确认临时 probe 实例已删除。

## 10. 依赖

- `mcp`：官方 MCP Python SDK。
- `httpx`：MCSManager daemon 流式上传/下载和 URL staging。
- `starlette`、`uvicorn`：SSE 和 Streamable HTTP ASGI 服务。
- `websocket-client`：MSMP JSON-RPC over WebSocket。

后续硬化见 [LIMITATIONS.md](LIMITATIONS.md)，重点是 fake MCSManager/RCON/MSMP 服务、CI 覆盖和持久连接池。
