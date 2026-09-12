# AI-RD-Platform · AI研发工作台

独立公开的 AI 研发工作台。把数据导入与标注、模型实验、知识库、可视化工作流、工具接入和项目运维连成实际可运行的链路。

**版本：0.2.1，Notebook 部署版。** 五模块 GUI 已接入持久化后端。具体实现、可选外部适配和后续验收条件见[需求覆盖表](docs/coverage.md)与[功能验收记录](docs/verification-v0.2.md)。

魔搭等 Linux Notebook 可上传并运行 [自包含部署 Notebook](deploy/AI_RD_ModelScope.ipynb)，内置程序 wheel、锁文件和验收脚本。详见[部署说明与验证边界](docs/notebook-deployment.md)。这不代表已经在某个魔搭账号完成 GPU 部署；实际结果由运行后的验收包记录。

正式控制台含总览、数据工坊、模型实验、智能体与工作流、运维与资源，以及项目和凭据弹窗。早期 [GUI 1.0 设计规范](docs/gui/design-spec.md)和[独立交互预览](docs/gui/gui-preview.html)保留为合成演示；运行平台即可使用新增功能。真实浏览器和目标设备的视觉验收仍待完成。

## 快速运行

Python 3.12；首次启动联网安装已锁定依赖。除主动使用外部适配器外，本机运行无需外部模型服务。

~~~bash
git clone https://github.com/QuJindai/AI-RD-Platform.git
cd AI-RD-Platform
bash scripts/start.sh
~~~

Windows PowerShell：

~~~powershell
git clone https://github.com/QuJindai/AI-RD-Platform.git
cd AI-RD-Platform
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
~~~

打开[本机控制台](http://127.0.0.1:8000)。默认本机模式无需账号；数据保存在 runtime/。按 Ctrl+C 停止服务。自定义端口：python -m ard --port 8080。

## 一条完整操作链

1. 创建项目，上传 CSV/JSON/JSONL/XLSX/YAML/XML/HTML/Parquet/ZIP，或从预设只读数据库查询导入快照。
2. 检索目录、修改元数据为新版本、按比例/类别/指定行拆分，清洗、合并和比较分布；可创建独立标注任务并审核导出。
3. 申请转入模型库，由审核者处理。本机模式允许演示训练审批；标注仍要求独立审核人。
4. 保存实验基线，选择数值特征训练分类/回归模型；重评、比较并下载 HTML 报告或 JSON 模型包。
5. 部署模型，输入记录得到预测并查看实际调用统计。过期或停用服务拒绝调用。
6. 导入知识文档、维护版本、查看精确来源片段；配置模型后可构建持久向量索引并进行引用问答。
7. 在可视化编辑器连接节点、配置条件和有界迭代，运行至人工节点审核后继续，或暂停、恢复和重试。
8. 保存和审核发布技能，按授权执行；管理工具依赖包、MCP 连接及可用时的受管 Docker 实例。
9. 调整项目额度，采集实际资源样本、处理告警、筛选导出审计和生成可验证备份。

OpenAPI 说明位于[本机 API 文档](http://127.0.0.1:8000/docs)。

## 现有能力

| 模块 | 实际能力与详细接口 |
|---|---|
| 数据 | 多格式解析、11 种变换、版本目录、拆分/分布比较、可恢复归档、图像与视频完整性检查、独立行/文本跨度标注。[说明](docs/features/data.md) |
| 数据源 | SQLite/PostgreSQL/MySQL 预设只读查询、参数验证、缓存及不可变快照。[说明](docs/features/sources.md) |
| 模型 | LogisticRegression/LinearRegression 真正训练；严格 JSON 模型包、基线、重评/对比/HTML 报告、部署统计、OpenAI 兼容与 Ollama 适配。[说明](docs/features/models.md) |
| 知识 | TXT/MD/DOCX/PDF/HTML/XLSX 解析、五种切片、源文件保存、版本/归档、关键词检索、持久向量索引及引用问答。[说明](docs/features/knowledge.md) |
| 工作流和技能 | 16 类节点、实际 SVG 编辑器与 JSON 往返、条件/模板/有界迭代/数据输出、检查点、暂停/恢复/重试、8 个内置技能及受限智能体。[说明](docs/features/workflows.md) |
| 运维 | 角色和项目权限、原子配额调整、审计 CSV、手动资源采样、告警历史、SHA-256 验证备份与恢复。[说明](docs/features/operations.md) |
| 工具接入 | 不可变依赖包、Docker 构建上下文、HTTP/SSE MCP 调用、受管容器状态与生命周期。[说明](docs/features/integrations.md) |

中文同源控制台使用原生 HTML/CSS/JavaScript，无运行时 CDN 依赖。所有数据、任务状态及指标来自实际记录。

## 部署和访问

本机开发模式只接受回环客户端。绑定非回环地址需要 ARD_IDENTITIES，不会自动开放匿名远程访问。

身份配置为令牌到身份的 JSON 映射：每个身份包含 user、单一 role（admin/developer/reviewer/auditor）和 projects（项目 ID 数组或 ["*"]）。真实令牌至少 16 字符，通过环境配置。不同角色不能共用同一 user 绕过独立审批。

Docker Compose：

~~~bash
python scripts/configure_access.py
docker compose up --build -d
~~~

配置脚本在本机生成 .env 和访问令牌；不会覆盖已有配置，文件已从 Git 和 Docker 构建上下文排除。多人使用需添加独立审核身份。默认 Compose 端口仅绑定主机 127.0.0.1；局域网部署同时配置端口、ARD_ALLOWED_HOSTS 与身份。单进程运行，不支持多个应用进程共享同一运行目录。

停止容器用 docker compose down；加 -v 会删除数据卷。Docker 适配已实现，目标主机运行验收仍需真实 Docker 环境。

## 可选模型服务与数据源

Ollama 服务端环境示例：

~~~text
ARD_OLLAMA_URL=http://127.0.0.1:11434
ARD_CHAT_MODEL=<已安装聊天模型名称>
ARD_EMBED_MODEL=<已安装向量模型名称>
~~~

模型名称须存在于目标服务。也可配置 OpenAI 兼容端点；具体变量、超时和响应验证见[模型服务说明](docs/features/models.md)。数据库设置见[只读源配置](docs/features/sources.md)。密钥仅在服务器配置，页面不返回密钥。

语义检索需要开发者显式构建索引；文档或模型配置变化后须更新索引。未连接的外部服务会明确报错。本地关键词检索与 CPU 训练可以独立使用。

## 验证与恢复

~~~bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
npm ci --ignore-scripts
ARD_PYTHON=.venv/bin/python npm test
.venv/bin/python scripts/smoke.py --base-url http://127.0.0.1:8000
~~~

Windows 将 Python 路径改为 .venv/Scripts/python.exe，并用 PowerShell 环境变量语法设置 ARD_PYTHON。界面测试需要 Node.js 24.15 及以上；应用运行本身不需要 Node。测试使用非渲染 DOM 模型和真实 HTTP，并不等同于浏览器视觉验收。

原始 smoke.py 和新增 functional_smoke.py 只在显式提供的服务中创建合成验收项目。完整功能验收需要 ARD_TOKEN 为全局管理员、ARD_REVIEW_TOKEN 为不同用户的审核者：

~~~bash
.venv/bin/python scripts/functional_smoke.py --base-url http://127.0.0.1:8000 --output test-results/functional-smoke.json
~~~

全局管理员可在无活动任务时下载平台备份，然后恢复到新空目录：

~~~bash
python scripts/restore_backup.py ai-rd-backup.zip /srv/ard-restored
python -m ard --data-dir /srv/ard-restored
~~~

备份校验数据库、对象 SHA-256、引用和审计链；不包含服务器私有连接凭据，恢复后需重新配置。也可正常停止服务后完整复制运行目录。

重启时运行中/排队任务会校正为中断或已请求的暂停状态；等待人工审核的流程可继续。暂停与取消在下一个检查点生效；不会把取消任务的计算结果登记为成功模型。

## 规模与边界

本机表格上限 50,000 行/200 列；通常上传 20 MiB，知识文档 10 MiB；单节点输出 8 MiB，工作流检查点 16 MiB；默认项目活动任务额度为 4，CPU 执行槽为 2。暂停和等待审批仍占任务额度。归档版本仍占逻辑存储额度。各解析器、运行器和备份的独立限制见模块说明。

尚未实现 GPU/vGPU 集群调度、深度模型训练压缩、ONNX/OM 通用转换、图像框/视频时间轴标注、MCP stdio/容器绑定管理、企业三员分立或生产高可用。Docker、远程数据库和模型适配器仍需目标环境验证，不能用协议模拟测试代替硬件验收。监控目前为手动采样；本地审计链没有外部锚定。

## 技术依据与许可

代码采用 MIT 许可。依赖保持各自许可；未复制其他私有项目代码。

- [FastAPI 测试与生命周期](https://fastapi.tiangolo.com/advanced/testing-events/)
- [scikit-learn 模型持久化限制](https://scikit-learn.org/stable/model_persistence.html)
- [Ollama 嵌入接口](https://docs.ollama.com/api/embed)
- [Ollama API](https://docs.ollama.com/api/introduction)

架构基线见[设计说明](docs/design.md)，本轮边界与实施分工见[功能规格](docs/functional-spec.md)及[开发计划](docs/functional-plan.md)。
