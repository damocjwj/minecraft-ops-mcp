# 当前不足与后续路线

本文记录 `minecraft-ops-mcp` 当前版本的已知不足、风险点和可补充功能。

## 1. 协议层不足

当前 MCP server 已迁移到官方 MCP Python SDK，覆盖项目当前需要的方法：

- `initialize`
- `ping`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`
- `resources/templates/list`
- `prompts/list`
- `prompts/get`

不足：

- 没有进度通知、取消请求、订阅、日志通知等扩展能力。
- 工具返回仍保留 JSON 文本包装；所有工具结果都会额外提供对象形式的 `structuredContent`，并补充通用 `outputSchema`，但还没有为每个工具做精确输出 schema。
- 工具 output schema 仍是通用对象 schema，尚未为每个工具做精确结构定义。

后续建议：

- 给长任务工具增加进度通知或任务查询机制。
- 对高价值工具补精确 output schema，并用 SDK 校验能力持续回归。

## 2. MCSManager 适配不足

已覆盖实例、文件、日志、上传下载 token、上传本地文件、生命周期等主要功能，但仍有缺口：

- 已增加 `file.download_local`，但下载 URL 的 daemon 路径仍需继续在更多 MCSManager 版本上回归。
- 已增加 `file.upload_url`，但它是在 MCP 服务侧先下载临时文件再上传，不是让 MCSManager daemon 直接从 URL 拉取。
- `file.write` 对 MCSManager 的路径规则比较敏感。实测相对路径更稳定，`/xxx` 可能报 `Illegal access path`。
- 某些实例上 `file.list` 返回正确 `absolutePath` 但 `items` 为空；其他文件 API 仍可工作。这可能是 MCSManager 版本或接口行为，需要进一步定位。
- 已增加 `file.write_new`，内部执行 `touch -> write`，但仍不能完全规避所有 MCSManager 路径策略差异。
- 已增加 `instance.update_config_patch`，可读取当前配置并深度合并 patch；字段级合法性仍主要依赖 MCSManager。
- 已增加 `instance.clone_from_template`，但模板配置是否可直接创建新实例取决于 MCSManager 返回结构和实例类型，仍必须 dry-run 后人工确认。
- `instance.create` 仍需要调用方理解 MCSManager InstanceConfig，手册只给了常见示例。
- Docker 镜像管理、用户/权限管理、计划任务、节点更多状态、实例导入导出等 MCSManager 能力还没有封装。

后续建议：

- 为 MCSManager 4.x 的返回结构写适配测试。
- 继续把 `instance.clone_from_template` 提升为更强的 `instance.create_minecraft_java`，封装常用端口、jar、cwd、启动命令、MSMP/RCON 字段。
- 为危险的 `instance.update_config` 增加字段级校验和更精细 diff。

## 3. RCON 适配不足

当前 RCON 客户端可认证和执行命令，已在真实实例上测通。但它仍然比较基础：

- 每条命令都新建连接，效率一般。
- 多包响应已用 marker 请求减少等待 timeout，但复杂大输出仍需要更多压测。
- 已增加 `rcon.list_players`、`rcon.time_query`、`rcon.save_all` 等固定命令封装。
- 原始命令已支持可选 allowlist/denylist 前缀过滤，但还不是完整权限系统。
- RCON 明文传输，不应暴露到公网。

后续建议：

- 支持连接复用和更完整的多包响应读取。
- 按命令类型继续拆分更细粒度风险级别，例如只读、保存、玩家管理、世界修改。

## 4. MSMP 适配不足

当前 MSMP 客户端使用 `websocket-client` 实现 JSON-RPC over WebSocket。已验证 Minecraft 1.21.9 的读写能力，但仍有边界：

- 每次 MSMP 调用都新建 WebSocket 连接，没有连接池或订阅事件循环。
- 没有实现 MSMP notification 订阅，例如玩家加入、白名单变化、OP 变化等。
- `msmp.call` 的只读白名单是手写的，不能自动从 `rpc.discover` 判断风险。
- 已增加 `msmp.bans.*` 和 `msmp.ip_bans.*`，但 ban 对象可选字段仍需要在更多服务端版本上回归。
- 已增加 `msmp.server_settings.list`，通过 `rpc.discover` 提取 settings；仍未动态生成工具。
- `msmp.server_settings.set` 对常见 setting 做基础类型/枚举校验，但未知 setting 仍会透传给服务端。
- `msmp.players.kick` 对离线或不存在玩家会返回空列表；这符合实测，但手册里还可以继续细化。

后续建议：

- 增加持久连接和 notification resource/event buffer。
- 根据 `rpc.discover` 动态生成部分工具或动态校验参数。

## 5. 安全与权限不足

当前有基本安全策略，但还不是完整权限系统：

- `confirm=true` 是工具参数层面的确认，依赖 MCP 客户端/agent 正确传参。
- 没有基于用户身份的授权策略。
- 没有对 MCSManager API key 做权限约束检查。
- 审计日志是本地 JSONL，没有轮转、签名或集中化。
- 原始命令只限制单行，没有语义级危险命令识别。
- 上传本地文件允许读取 MCP 进程有权限读取的路径，需要依赖运行环境隔离。

后续建议：

- 增加策略配置文件，例如允许哪些工具、哪些实例、哪些路径。
- 增加路径沙箱，限制 `file.upload_local` 只能读取指定目录。
- 增加危险命令 denylist，例如 `stop`、`op`、`deop`、`ban-ip`、`whitelist off` 等需要更强确认。
- 审计日志增加调用 ID、客户端名、耗时、结果摘要和轮转。

## 6. 测试覆盖不足

目前已有基础单元测试和真实环境集成探针，但仍缺少隔离测试资产：

- 没有 fake MCSManager server。
- 没有 fake RCON server。
- 没有 fake MSMP WebSocket JSON-RPC server。
- 没有 CI。

后续建议：

- 为 adapters 建 fake server 测 HTTP/WebSocket/RCON 边界。
- 把当前真实环境测试流程整理为可选的 `scripts/smoke_test.py`，由环境变量启用。

## 7. 运维体验不足

当前项目已经可用，但使用体验还有提升空间：

- README 和手册需要随着工具变化自动同步。
- 没有一键“创建 1.21.9 MSMP 测试实例”的高级工具。
- 没有保存/恢复实例配置的事务式流程。
- 没有世界备份的高级语义工具，例如 `backup_world`、`restore_world`。
- 没有健康检查输出模板，agent 需要自己组织结果。

后续建议：

- 增加 `server.health_check` 聚合工具。
- 增加 `server.safe_restart` 聚合工具，但内部仍要求确认关键步骤。
- 增加 `mcsm.minecraft.create_vanilla_instance` 模板工具。
- 增加 `backup.create`、`backup.list`、`backup.restore`。

## 8. 当前实测注意点

真实测试中观察到：

- Minecraft 1.21.9 的 `management-server-secret` 必须是 40 位字母数字，带连字符或长度不对会导致服务端启动失败。
- MSMP 如果关闭 TLS，`MSMP_URL` 使用 `ws://...`；如果启用 TLS，使用 `wss://...`。
- `server.properties` 中 MSMP 端口和游戏端口要避开已有实例。
- MCSManager 中切换 RCON 配置时，运行中的实例可能不允许直接更新相关字段，先停服再更新更稳。
- 对不存在的新文件，`file.touch` 后再 `file.write` 更稳。
