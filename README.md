# AI-RD-Platform · AI研发工作台

独立公开的AI研发平台工程。将数据版本、审批、真实CPU模型训练、部署预测、知识检索和人工审核工作流连成可运行的链路。

**版本：0.1.0，工程验证版。** 当前实现范围、外部适配与尚未完成条款见[需求覆盖表](docs/coverage.md)。原始完整技术要求的所有条款尚未实现，不能将本版本视为整个平台的最终验收版。

**GUI 1.0 设计已成稿：** 总览、数据工坊、模型实验、智能体与工作流、运维与资源，以及项目和凭据弹窗。见[逐页设计规范](docs/gui/design-spec.md)和[独立交互预览](docs/gui/gui-preview.html)（下载后在浏览器打开，无需后端；全部内容为合成演示）。正式控制台已改善字号、触控尺寸和手机/平板布局；真实浏览器布局验收仍待完成，检查结果见[GUI 验证记录](docs/evidence/gui-design.json)。

## 快速运行

Python 3.12；首次启动需联网安装已锁定依赖。后续运行不需要访问外部模型服务，除非主动使用LLM/向量适配器。

```bash
git clone https://github.com/QuJindai/AI-RD-Platform.git
cd AI-RD-Platform
bash scripts/start.sh
```

Windows PowerShell：

```powershell
git clone https://github.com/QuJindai/AI-RD-Platform.git
cd AI-RD-Platform
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

打开 [本机控制台](http://127.0.0.1:8000)。默认本机模式无需账号；数据保存在`runtime/`。关闭终端或按Ctrl+C停止服务。自定义端口：`python -m ard --port 8080`。

## 一条完整操作链

1. 创建项目，在数据工坊上传CSV/JSON/JSONL/ZIP，或主动创建已明确标记的合成样例。
2. 预览记录，清洗并生成新版本；可按比例拆分、合并或导出。来源版本保留。
3. 申请转入模型库并审批。本机模式允许一人演示；令牌模式由不同身份的审核者处理。
4. 选择目标列、数值特征和分类/回归任务。后台实际训练，并独立计算测试集指标。
5. 部署模型，输入记录得到预测结果。过期或停用服务拒绝调用。
6. 录入知识文本，查询并查看来源切片；建立工作流，运行至人工节点、审核后继续。
7. 在运维页核对任务、资源、审批和审计链。

OpenAPI说明位于[本机API文档](http://127.0.0.1:8000/docs)。所有列表和状态来自实际记录。

## 现有能力

- CSV、JSON数组、JSONL及安全ZIP导入；11种实际数据变换；SHA-256封存、版本来源、拆分合并、配额。
- LogisticRegression分类与LinearRegression回归；训练集拟合填补/缩放、独立评估集、可检查JSON模型参数。数值表格任务，不加载用户pickle。
- 模型部署、真实预测、请求/失败/延迟累计、过期校验及手动停用。
- 五种文本切片策略；中文关键词检索与原文偏移；可选Ollama语义/混合检索和聊天。
- 11种工作流节点、DAG校验、节点日志、人工等待/审批/恢复、取消与重启状态核对。
- 项目范围和角色控制、独立审批、不可变资产、带哈希链接的本地审计记录。
- 中文同源控制台，原生HTML/CSS/JS，不依赖CDN。

## 部署和访问

本机开发模式只接受回环客户端。绑定非回环地址需要`ARD_IDENTITIES`，不会自动开放匿名远程访问。

身份配置为令牌到身份的JSON映射：每个身份包含`user`、单一`role`（admin/developer/reviewer/auditor）和`projects`（项目ID数组或`["*"]`）。真实令牌至少16字符，通过环境配置。不同角色不能共用同一`user`来绕过独立审批。

Docker Compose：

```bash
python scripts/configure_access.py
docker compose up --build -d
```

脚本在本机生成`.env`和访问令牌；它不会覆盖已有配置。此文件已被Git和Docker构建上下文排除。控制台会在需要时询问令牌。多人使用时请在配置中添加独立审核身份；单个owner不能审批自己在令牌模式创建的请求。

默认Compose端口只绑定主机127.0.0.1。需要局域网部署时，同时调整端口绑定、`ARD_ALLOWED_HOSTS`和身份配置。单进程运行，暂不支持多个应用进程共享一个运行目录。停止容器使用`docker compose down`；不要使用`-v`，该选项会删除数据卷。

## 可选Ollama连接

在管理员环境中设置：

```text
ARD_OLLAMA_URL=http://127.0.0.1:11434
ARD_CHAT_MODEL=<已安装聊天模型名称>
ARD_EMBED_MODEL=<已安装向量模型名称>
```

模型名称必须使用该Ollama实例中已安装的真实模型。通过控制台“检测连接”获取实时模型目录；仅配置地址不代表连接已验证。未连接时LLM/语义节点返回明确错误。本地关键词检索和CPU训练不依赖Ollama。

## 验证与恢复

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --test tests/test_ui.mjs
.venv/bin/python scripts/smoke.py --base-url http://127.0.0.1:8000
```

Windows将`.venv/bin/python`改为`.venv/Scripts/python.exe`。界面回归测试使用Node.js 20及以上，应用运行本身不需要Node。smoke脚本只在显式提供的运行服务中创建带唯一名称的合成验收项目，验证真实HTTP流程；可通过`ARD_TOKEN`和`ARD_REVIEW_TOKEN`传递开发/审核身份。本轮实际执行结果见[验证记录](docs/verification.md)。

运行数据备份：正常停止服务后，完整复制`runtime/`目录。恢复时将其设为`ARD_DATA_DIR`后启动；不要只复制数据库而遗漏`objects/`。在新目录运行不会覆盖原目录。

运行中/排队任务在异常重启后标记为INTERRUPTED，等待人工审核的工作流可继续。取消是协作式的；已进入底层数值运算的CPU调用会完成当前计算，但结果不会登记为成功模型。

## 规模与边界

本机数据上限50,000行/200列；上传20MiB，ZIP展开100MiB/200成员；单节点输出8MiB，工作流检查点16MiB；项目活动任务配额默认为4，CPU并行执行槽为2。项目存储配额计量数据、文档和模型内容，工作流/日志另有上述上限；尚无历史日志自动保留/归档策略。

当前没有GPU调度器、容器化MCP生命周期管理、图像/视频标注、深度模型训练压缩、ONNX/OM互转、企业三员分立或生产高可用。外部Ollama测试、GPU硬件验收和容器实测结果按证据单列。本地哈希审计链能检测被改写的记录，但没有外部锚定，不能宣称防数据库管理员重写。

## 技术依据与许可

代码采用MIT许可。依赖保持各自许可；未复制其他私有项目代码。

- [FastAPI测试与生命周期](https://fastapi.tiangolo.com/advanced/testing-events/)
- [scikit-learn模型持久化限制](https://scikit-learn.org/stable/model_persistence.html)
- [Ollama嵌入接口](https://docs.ollama.com/api/embed)
- [Ollama API](https://docs.ollama.com/api/introduction)

架构见[设计说明](docs/design.md)，实施任务见[开发计划](docs/implementation-plan.md)。
