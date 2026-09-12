# 工具包、HTTP MCP 与受管 Docker

本模块在 `ard/features/integrations.py` 注册路由，通过 `install(app)` 幂等安装；`ard/static/integration-tools.js` 使用 ARD 宿主，在模型、智能体、运维页面提供三个中文面板。全部项目读取复用 Service 权限检查，写入复用开发者或管理员权限。依赖仅用 Python 标准库及平台已有 FastAPI/Pydantic，没有新增第三方依赖。

## 工具与依赖包

| 方法与路径 | 输入 | 输出/权限 |
| --- | --- | --- |
| `GET /api/projects/{pid}/packages` | 无 | 当前项目全部版本，含 `archived`、`state_revision`；项目读者 |
| `POST /api/projects/{pid}/packages` | multipart：`file`，可选 `name`、`version`、`category`（tool/dependency，默认 tool）、`parent_id` | 201 新的不可变版本；开发者/管理员 |
| `GET /api/packages/{id}/manifest` | 无 | JSON 清单；项目读者 |
| `GET /api/packages/{id}/download` | 无 | 原始文件，逐字节保留；项目读者 |
| `GET /api/packages/{id}/requirements` | 无 | `application/x-ndjson` 依赖发现报告下载；项目读者 |
| `POST /api/packages/{id}/archive` | `{expected_revision, archived, confirmed:true}` | 更新独立状态，保留原始版本；开发者/管理员 |
| `POST /api/projects/{pid}/packages/docker-context` | `{package_ids:[...]}`，1–20 个、不重复 | 包含 Dockerfile、原包及 JSON 清单的 ZIP；项目读者 |

名称和版本为 1–100 位英文、数字、点、减号或下划线，以英文或数字开头。可从元数据读取，未发现时必须填写。项目内同类别、同名称、同版本唯一，归档后也不能覆写。父版本必须来自当前项目、同名称、同类别；新版本无需覆盖旧版本。原始文件和生成清单的合计字节数纳入项目配额，同一事务中检查与保存。

`integration_package` 记录包含 `name,version,category,filename,format,sha256,manifest_sha256,size_bytes,artifact_size_bytes,file_count,parent_id,creator,sealed`。`sha256` 和 `manifest_sha256` 均引用实际内容寻址对象。`integration_package_state` 保存 `package_id,archived`；归档操作使用状态记录的 `state_revision`，原始版本的 `revision` 保持不变。活动、创建中和状态不明的运行实例引用会阻止归档；停止、退出或移除后可归档。历史父版本与构建追溯保留，归档版本仍可读取和下载，不自动删除任何 blob。

支持 `.whl`、`.zip`、`.tar.gz`、`.tgz`，以及 `.py,.js,.ts,.sh,.r,.jl,.c,.cpp,.h,.go,.rs,.json,.toml,.yaml,.yml,.md,.txt,.sql` 源码/配置文件。上传文件名限安全 ASCII 单个文件名，最多 150 字符，允许英文、数字、点、加号、减号、下划线。压缩包不解压到磁盘，上传内容不被导入或执行。

限制为：原文件 20MiB；单个普通成员或源码文件 10MiB；累计普通成员 60MiB；最多 1000 个成员（含目录）；路径最长 240 字符且最多 16 层；单个元数据文件 256KiB；最多 1000 条依赖声明。拒绝绝对路径、点路径、反斜杠、控制字符、盘符、重复/大小写冲突、文件目录冲突、加密 ZIP、符号链接、硬链接、稀疏文件、设备及其他特殊成员。展开压缩比最多 100 倍（小文件允许最多 1MiB）；TAR 额外包含至多 2MiB 的格式开销。损坏、CRC 不匹配或不支持的压缩格式拒绝。

清单包含 `format,files[{path,size_bytes,sha256}],expanded_bytes,metadata_sources,requirements[{source,requirement}],warnings,execution`。其中 `files[].sha256` 只是归档成员校验值，**不是独立 blob 引用**。从 `*.dist-info/METADATA`、`PKG-INFO`、`pyproject.toml` 的静态 project 表、`ard-package.json` 的 name/version/requirements 及 `requirements*.txt` 发现元数据。不会执行 setup.py、动态构建后端、导入源码或访问依赖地址；`-r`、URL、pip 选项等原样作为发现报告记录，不展开、不安装。wheel 至少要求带名称/版本的 dist-info/METADATA；这不替代 wheel 发布合规检查、包签名验证或兼容性安装测试。

构建上下文总原包不超过 60MiB。Dockerfile 使用 `python:3.12-slim`；wheel 使用 JSON argv 的 `pip install --no-index --no-deps`，源码和其他归档仅复制。最终用户为 `65534:65534`，默认命令只打印上下文就绪。下载行为没有构建、执行或拉取镜像。构建者需审核来源、固定基础镜像摘要、使用隔离构建器，并把实际工具及运行依赖构建入镜像。导出包包含原始文件，不宣称依赖已解析或包可以成功安装。

## HTTP MCP

| 方法与路径 | 输入 | 输出/权限 |
| --- | --- | --- |
| `GET /api/projects/{pid}/mcp` | 无 | 脱敏配置列表与当前配置版本最近探测结果；项目读者 |
| `POST /api/projects/{pid}/mcp` | 下述配置 JSON | 201 脱敏配置；管理员 |
| `PUT /api/mcp/{id}` | 完整配置 JSON，必须带 `expected_revision` | 更新配置版本；管理员 |
| `POST /api/mcp/{id}/probe` | 无 | `available:true,protocol_version,tools,checked_at`；开发者/管理员 |
| `POST /api/mcp/{id}/call` | `{tool,arguments:{},confirmed:true}` | `{result,elapsed_ms}`；开发者/管理员 |

配置字段：`name`（1–100 字符）、`url`（至多 2000 字符）、`token`（可选 Bearer，默认空）、`allowed_tools`（0–64 个不重复名称）、`allow_private`（默认 false）、`timeout_seconds`（2–20，默认 10）、`enabled`（默认 true），更新时另带 `expected_revision`。工具名限 `[A-Za-z0-9_.:-]`，1–100 字符。更新是完整替换，凭据空字符串明确清除旧凭据；不会从浏览器回填旧凭据。配置请求体最多 64KiB，凭据最多 4096 个可打印 ASCII 字符。配置验证错误不回显任何输入值。

端点和 Bearer 凭据保存于数据目录 `private/integrations/<opaque-id>.json`（目录 0700、文件 0600）。数据库仅保存不具访问能力的 `config_key` 引用、`endpoint_origin`、白名单、超时及状态；API 去除 `config_key`，不返回完整路径、凭据或会话标识。配置正文和远端原始错误不写入审计。工具输入、输出和授权头不持久化；事件只记录连接、操作/工具名称、时间、状态、耗时及操作者。已知凭据、完整端点、会话标识及常见敏感字段在远端结果中脱敏。任意远端工具自己返回的其他敏感内容仍由该工具及调用者管理，平台不进行无限推断的秘密检测。

私有配置文件不包含在公开备份或源代码中。恢复公开备份后需管理员重配连接；历史私有配置不通过 API 提供读取。所有路径都从固定数据根和随机 ID 生成，没有接受任意文件路径的入口。

传输仅实现 MCP **2025-06-18 Streamable HTTP** 的请求/响应客户端：`initialize` → `notifications/initialized` → `tools/list` → 可选一次明确 `tools/call`。每次 probe/call 使用独立短会话，遵守服务端 `Mcp-Session-Id`，初始化后发送 `MCP-Protocol-Version`，结束时尽力 DELETE 会话（至多额外 1 秒；远端可拒绝 DELETE）。不共享浏览器或身份之间的会话，不接受模型生成的端点、任意 RPC 方法或非白名单工具，不自动重试调用。即使远端已执行但响应丢失，也只记录 `FAILED_OR_UNKNOWN`，由用户核对远端状态。

支持 `application/json` 与 POST 响应上的 `text/event-stream`，按 SSE 事件的 `data:` 多行字段解析并匹配 JSON-RPC ID；收到对应完整事件后即关闭流。初始化必须声明 tools 能力及匹配协议版本。工具分页至多 5 页、100 个唯一工具，模式定义 16KiB，描述 4000 字符。工具参数为对象，至多 64KiB，JSON 深度至多 12 层/5000 节点。单次响应最多 1MiB、1000 个 SSE 事件；不解压 HTTP 压缩响应，不跟随重定向；非成功状态、错误 ID、错误版本或未知响应格式失败。schema 作为描述展示，由远端工具验证完整 JSON Schema；平台不会解析 schema 的远程引用。

端点只允许 HTTP(S)，不含用户名、密码、查询参数或片段。DNS 解析最多 4 个并发任务且有总操作截止时间；每次请求检查全部解析地址并固定连接到已检查 IP，TLS 仍验证原主机名。默认只允许公网地址；管理员可明确允许内网/回环。链路本地（包括云元数据）、组播及未指定地址始终拒绝。没有代理继承、重定向凭据转发、资源 URI 抓取或模型输出选址。超时覆盖解析、连接、读取和分页，失败返回统一 502，不包含端点或远端错误内容。

不支持旧 HTTP+SSE GET/endpoint 传输、后台订阅/通知、服务端发起的 sampling/elicitation/roots 请求、stdio、OAuth 自动授权、客户端工具执行或重连重放。服务端主动 RPC 请求使本次操作失败。工具 annotations 不被视为授权。GUI 每次更换连接、工具或参数都会清除调用确认；身份/项目切换清空端点、密码、参数和结果，延迟响应无法写回其他上下文。

实现依据为 MCP 官方 [Streamable HTTP 传输](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、[生命周期](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle) 与 [工具协议](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)。

## 本地 Docker 运行实例

| 方法与路径 | 输入 | 输出/权限 |
| --- | --- | --- |
| `GET /api/runtime/status` | 无 | 实际检查 Docker CLI/本地守护进程；已登录身份 |
| `GET /api/projects/{pid}/runtimes` | 无 | 本项目保存的实例状态，`checked_at` 表示上次实际检查时间；项目读者 |
| `POST /api/projects/{pid}/runtimes` | 下述启动 JSON | 201 实例记录；管理员 |
| `POST /api/runtimes/{id}/action` | `{action:"refresh"|"stop"|"remove",expected_revision,confirmed:true}` | 更新实例状态；管理员 |

启动 JSON：`image` 为完整 `sha256:` 加 64 位小写十六进制的**本地镜像 ID**，`argv` 为 1–20 项的程序/参数数组，`package_ids` 为至多 20 个当前项目未归档的包引用，`memory_mib` 为 64–1024（默认 256），`cpus` 为 0.1–2（默认 0.5），`ttl_seconds` 为 10–3600（默认 300），`confirmed:true`。argv 每项 1–1000 字符，不含控制字符；合计 JSON 不超过 8192 字节；第一项不得以减号开头。包引用仅用于追溯和归档保护，平台不会挂载或自动安装上传文件，镜像必须预先含有所需文件。创建与配额预留同事务检查，未终止实例数受项目 `max_jobs` 约束。

支持本地 `/var/run/docker.sock`，显式指定 CLI `--host unix:///var/run/docker.sock`，不继承远端 Docker host/context。镜像通过 `docker image inspect` 验证 ID，使用 `--pull never`。启动使用固定 Python 3 超时监督程序包装显式 argv，所以镜像需可信且包含可用 `python`。创建后实际 inspect 判断 RUNNING 或 EXITED，不根据仅返回容器 ID 就宣称运行成功。

资源与隔离参数固定包括：`--network none`、只读根文件系统、`--cap-drop ALL`、`no-new-privileges`、UID/GID 65534、64 个进程上限、内存与内存+交换区同值、CPU 上限、16MiB `/tmp` tmpfs（noexec/nosuid/nodev）、不重启、不持久化日志、init 进程；不支持主机挂载、端口发布、宿主网络、GPU、特权或任意额外 Docker 参数。CLI 由参数数组直接运行，没有主机 shell。单次命令 3–10 秒上限、输出 256KiB 上限；错误文本不会返回 Docker 原始输出。

平台安装标识存放于 `private/integrations/runtime-owner`，随机生成且权限 0600。每个容器标记 `io.ard.owner`、`io.ard.project`、`io.ard.runtime`。任何停止/移除都先 inspect 同时核对三项标签、名称和完整容器 ID；移除前再次检查。不扫描、批量停止或删除无关容器。公开备份恢复后没有原所有权标识，不能接管旧容器。UNKNOWN 状态保留配额和包引用以便管理员检查，创建响应超时不会冒充成功，也不会自动操作名称不匹配的对象。

TTL 使用容器内 Python `subprocess.run(timeout=...)`，并有平台的守护计时器在 TTL 到达后再检查所有权并停止；refresh 同样停止已过期实例。**适用可信镜像和常规工具进程**：同 UID 的恶意进程可以干扰监督程序；Docker/平台故障时无法保证外部 stop 成功，因此不宣称对任意敌对镜像提供硬隔离 SLA。这里没有 GPU 调度、企业多租户沙箱、安全恶意代码执行服务、镜像拉取/构建 API、重启现有实例或通用容器终端。停止和移除保留平台运行记录；移除容器需明确确认，容器临时状态会丢失。

Docker 参数依据官方 [container run 文档](https://docs.docker.com/reference/cli/docker/container/run/)。当前开发环境实际没有 Docker CLI，`available:false` 与启动 503 是真实状态；容器生命周期通过命令/所有权夹具验证，未进行真实 Docker 服务验收。

## 验证与界面边界

`tests/test_integration_features.py` 测试不可变版本与恢复、真实 ZIP/TAR 内容/清单/导出、恶意路径与链接/炸弹、配额、角色、跨项目限制、私有文件权限、错误脱敏，以及本地合成 HTTP 服务上的 JSON/SSE、会话生命周期、明确白名单调用、协议失败与网络限制。Docker 夹具验证真实生成的命令参数、所有权检查和停止/移除顺序。夹具不是任何第三方 MCP 产品或 Docker 守护进程已通过验收的证据。

本轮运行 `/workspace/scratch/1bd60b330871/ai-rd-venv/bin/python -m pytest tests/test_integration_features.py -q`：36 项通过；另通过 `node --check ard/static/integration-tools.js`。测试含 DNS 截止时间、调用结果不明时不重试、配置在发现期间改变后不调用、移除前再次检查所有权和 TTL 停止路径。

中文界面包含包上传、版本选择、清单查看、原包/依赖报告/构建上下文下载、归档恢复，MCP 管理员配置、探测、schema 展示、参数确认调用，以及 Docker 可用性、启动、检查、停止、移除。所有动态内容使用 textContent，静态作者 HTML 由 ARD.panel 承载；每个面板独立注册 reset/refresh，身份/项目边界和局部选择代次共同阻止过期响应。浏览器 URL 策略已阻止本地浏览，不作绕过，真实浏览器布局与交互渲染仍待在允许环境验收。
