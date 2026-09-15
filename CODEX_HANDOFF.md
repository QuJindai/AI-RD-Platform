# Codex 代码交接 · AI-RD-Platform

交接日期：2026-09-15。用户要求将本项目全部代码交给下一位 Codex 继续执行。

## 1. 项目与版本

| 项目 | 已确认内容 |
|---|---|
| 仓库 | <https://github.com/QuJindai/AI-RD-Platform> |
| 所有者 / 分支 / 可见性 | QuJindai / main / public |
| 应用版本 | 0.2.1 |
| 运行代码基线 | `cc1579f986dfa02b8412d0dc51dc79828ceb1fed` |
| 基线 Git tree | `d151b0ed853a101cb56a3e599be1857f4cbb45c2` |
| 许可 | MIT；依赖使用各自许可 |
| 当前交接范围 | 全部应用源码、静态界面、测试、依赖锁文件、构建/部署脚本、设计与验收文档、自包含部署 Notebook |

本次交接提交只增加本文件及 README 入口。运行代码、依赖和部署 Notebook 保持上述基线。GitHub 克隆包含历史提交；源码 ZIP 是指定提交的完整文件快照，不含 Git 历史。仓库没有 Git 子模块或 Git LFS 指针；Notebook 已内置对应版本的应用 wheel。

用户已授权独立公开建仓、开发、测试，并在其已有魔搭 GPU 环境中部署。本次最新指令是交接代码；当前操作者已停止继续操作云浏览器。后续接手者以用户的新指令为准。未授权新购付费算力。

## 2. 先读这些文件

| 文件 | 用途 |
|---|---|
| [README.md](README.md) | 启动入口、功能概览、配置与范围 |
| [docs/coverage.md](docs/coverage.md) | 逐项区分已实现、适配待实测和后续开发 |
| [docs/functional-spec.md](docs/functional-spec.md) | 功能契约、权限边界及统一接口 |
| [docs/functional-plan.md](docs/functional-plan.md) | 已执行的功能开发清单；其中人员分工是历史实施记录 |
| [docs/design.md](docs/design.md) | 架构与数据/任务设计基线 |
| [docs/gui/design-spec.md](docs/gui/design-spec.md) | 已确定的 GUI 设计 |
| [docs/notebook-deployment.md](docs/notebook-deployment.md) | 魔搭部署、端口访问、验收与启停恢复 |
| [docs/evidence/v0.2.1](docs/evidence/v0.2.1) | 最近应用版本的原始验证证据 |

## 3. 当前完成状态与边界

已实现五页中文控制台：总览、数据工坊、模型实验、智能体与工作流、运维与资源，以及项目和凭据弹窗。运行时为同源原生 HTML/CSS/JavaScript，后端为 Python 3.12 + FastAPI，SQLite WAL 和内容寻址文件保存数据，scikit-learn 执行 CPU 表格模型训练。

真实功能链包括多格式数据导入、清洗/拆分/版本管理、独立标注与审核、模型训练/重评/预测/报告、知识解析/检索、可视化 DAG 工作流与人工检查点、技能审核发布、权限/配额/审计/备份。详细支持格式、节点和接口以模块文档及 coverage 为准。

### 已有证据

以下为 **2026-09-12 已完成的验证记录**，本次交接核对了文件与 CI 状态，未重新运行完整应用测试。

| 验证 | 结果 | 证据 |
|---|---|---|
| Python 回归 | 332 项通过，0 失败/错误/跳过 | [pytest.xml](docs/evidence/v0.2.1/pytest.xml) |
| JavaScript 行为 | 27 项通过；JSDOM 非渲染 DOM + 真实 HTTP | [ui-tests.tap](docs/evidence/v0.2.1/ui-tests.tap) |
| 发布包一致性 | wheel 中 38 个运行文件与源码逐字节一致 | [package-verification.json](docs/evidence/v0.2.1/package-verification.json) |
| 自包含 Notebook | 本地 Linux 新安装、同进程复用、停止后重启通过；每轮 13 + 61 项 HTTP 检查，共 3 轮 | [notebook-validation.json](docs/evidence/v0.2.1/notebook-validation.json) |
| 进程与数据恢复 | 错误实例身份拒绝停止；正常停止确认退出；重启保留项目和口令 | 同上 |
| GitHub Actions 基线 | `Verify platform` 的 `test` 作业成功 | [run 34689720820](https://github.com/QuJindai/AI-RD-Platform/actions/runs/34689720820) |
| 本地 GPU | `UNVERIFIED / PYTORCH_UNAVAILABLE`，应用通过而总体为 `PARTIAL` | [notebook-gpu.json](docs/evidence/v0.2.1/notebook-gpu.json)、[notebook-summary.json](docs/evidence/v0.2.1/notebook-summary.json) |
| 魔搭远端应用 / GPU 验收 | **NOT_RUN** | 下一节的会话现场交接 |
| 实际浏览器视觉、手机触控、魔搭端口路由 | **NOT_RUN** | 需目标环境实测 |

历史证据中的文件散列属于其生成时版本；交接说明和 README 的更新不回写旧证据。下一次代码变更应生成自己的新验证记录。

### 尚待验证或开发

- Docker、远程 PostgreSQL/MySQL、外部 Ollama/OpenAI 兼容模型、第三方 MCP：已有适配实现，真实目标服务验收仍待完成。当前不能把协议模拟结果写成目标服务通过。
- GPU/vGPU/NPU 集群调度、深度模型/LoRA 训练、压缩与 ONNX/OM 通用转换、专业图像框/视频时间轴标注、SSO/企业三员分立、生产高可用：未完成的后续范围，详见 coverage。
- 应用模型训练当前仍是 **CPU scikit-learn**。部署脚本的真实 CUDA 矩阵测试即便通过，也不表示已完成 GPU 训练、微调或推理业务。

## 4. 魔搭现场交接：最后停在哪里

本节来自 2026-09-15 会话中的实际页面观察，不是 GPU 验收报告；下一位执行者必须重新核实即时状态。

1. 用户手动登录魔搭并启动已有免费 CUDA GPU Notebook。平台显示“GPU环境已启动”，规格为 8 核 / 32 GB 内存 / 24 GB 显存；未执行 `nvidia-smi`，GPU 具体型号尚未取得。
2. “我的Notebook → 查看Notebook”出现版本选择弹窗。“暂不使用”旧版入口跳到另一个阿里云登录页；选择“体验新版”成功进入 ModelScope Code Editor。
3. 新编辑器显示“实例运行中”，连接到 `DSW-GPU (/mnt/workspace)`；内嵌 VS Code 可见终端入口及目录右键菜单“在集成终端中打开”。
4. 尝试通过目录菜单“上传...”上传 `deploy/AI_RD_ModelScope.ipynb`，上传调用长时间未返回。结束等待后，旧云浏览器标签页消失；恢复页面显示“登录 / 注册”。**上传是否完成未知。**
5. 尚未在远端运行安装、启动应用、HTTP 检查或 CUDA 计算。未主动关闭 GPU；它是否仍运行、工作目录是否保留，交接时未知。

因此接手后先确认会话、实例和文件；不要根据用户说“已启动”就写部署完成，也不要把未确认的上传当成文件已存在。

优先在新版编辑器终端核对 `/mnt/workspace`，从本公开仓库获取明确提交的代码或 Notebook；也可使用目标环境支持的正常上传入口。此前卡住的是云浏览器上传/会话，未取得站点拒绝或反自动化证据。

用户在手机 Codex 内操作浏览器时，左右滑动会触发宿主界面的左右切换。这个反馈发生在远程浏览器交互/登录过程中，**尚未定位为工作台源码缺陷**。避免再次给用户依赖横向滑动的登录步骤；工作台自身的手机触控仍需另行验证。

登录会话、平台密码、验证码、Cookie 和部署后的私有访问口令没有进入交接源码。需要认证时用接手环境支持的安全登录或手动接管流程；不要从历史聊天复制凭据写入终端、代码或报告。

## 5. 代码地图

| 路径 | 职责 |
|---|---|
| `ard/__main__.py`, `ard/api.py` | CLI、应用创建、API 与静态资源入口 |
| `ard/service.py`, `ard/store.py` | 业务协调、记录/对象持久化、版本、额度及审计 |
| `ard/security.py` | 身份、角色、项目边界和来源检查 |
| `ard/jobs.py`, `ard/workflows.py`, `ard/workflow_values.py` | 后台任务、DAG 执行、检查点和变量 |
| `ard/engines/` | 数据解析/变换及实际 CPU 训练/预测 |
| `ard/features/` | 数据、数据源、模型、知识、工作流、技能、集成、运维模块 |
| `ard/knowledge.py`, `ard/knowledge_parsers.py`, `ard/connectors.py` | 知识处理、文档解析、模型协议 |
| `ard/static/` | 正式运行界面和模块控制器；`features.js` 提供共用前端接口 |
| `tests/` | Python 回归和 JavaScript 行为测试 |
| `scripts/smoke.py`, `scripts/functional_smoke.py` | 13 项基础、61 项完整功能 HTTP 验收 |
| `scripts/notebook_deploy.py` | 隔离安装、访问控制、进程管理、GPU 探测及结果 ZIP |
| `scripts/build_notebook.py`, `scripts/verify_notebook.py`, `scripts/verify_wheel.py` | 交付物构建、实际执行与源码一致性验证 |
| `scripts/start.sh`, `scripts/start.ps1`, `scripts/bootstrap.py` | Linux / Windows 本机启动 |
| `Dockerfile`, `compose.yaml`, `scripts/configure_access.py` | 容器部署及本地身份配置 |
| `scripts/restore_backup.py` | 验证备份后恢复到新空目录 |
| `deploy/AI_RD_ModelScope.ipynb` | 一个可执行代码单元格，内置 wheel、锁文件、部署和 HTTP 验收脚本 |
| `docs/gui/` | 设计规范、独立合成预览及预览构建源；正式应用以 `ard/static/` 为准 |
| `.github/workflows/ci.yml` | Python、JS、GUI 预览、wheel、Notebook、安装包 HTTP 验收流水线 |

## 6. 本机启动和复验

Linux 先确保 `python3` 是 Python 3.12 或更高版本；推荐与已验证环境一致的 3.12。Node.js 24.15+ 仅用于界面测试。

```bash
git clone https://github.com/QuJindai/AI-RD-Platform.git
cd AI-RD-Platform
git rev-parse HEAD
bash scripts/start.sh
```

打开 <http://127.0.0.1:8000/>；API 文档 <http://127.0.0.1:8000/docs>。默认本机模式无需工作台账号，运行数据位于 `runtime/`。Windows 入口见 README 的 `scripts/start.ps1`。获取运行代码基线可使用 `git show cc1579f986dfa02b8412d0dc51dc79828ceb1fed:<路径>`；不要覆盖接手时已有未提交修改。

复验命令在仓库根目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q --junitxml=test-results/pytest.xml
npm ci --ignore-scripts
ARD_PYTHON=.venv/bin/python npm test
.venv/bin/python -m pip wheel . --no-deps --wheel-dir dist
.venv/bin/python scripts/verify_wheel.py dist/ai_rd_platform-0.2.1-py3-none-any.whl
.venv/bin/python -m pip install ipython==9.5.0
.venv/bin/python scripts/verify_notebook.py --notebook deploy/AI_RD_ModelScope.ipynb --output test-results/notebook-validation.json
```

最后一条会在隔离目录安装并实际执行交付 Notebook，覆盖新装、复用、停止/重启和三轮 HTTP 验收；没有 PyTorch/GPU 的本地机器会如实记录 GPU 未验证。不要用该结果代替魔搭实测。

若修改运行代码或部署脚本，按 [Notebook 构建说明](docs/notebook-deployment.md)重新构建 wheel 和 Notebook，再执行一致性及运行验证。不要只更新 Python/JS 源码而留下嵌入旧代码的 Notebook。本次纯交接文档变更没有重建发布 Notebook。

## 7. 远端部署参数与恢复要点

发布 Notebook 的 SHA-256：

```text
0229e39fbab2366eac716e827cba7a92e9ea03b6aa1b0d741cbc44e556fda975
```

内置 payload SHA-256：`49417638c4befdc834030eb798be2484be9efb97ea23f8c6d90874a05f94255f`。

内置 wheel SHA-256：`aae20cd928391c297c6f741bdcca09a3b0067aee89744d7be3098ad68fbd3d6f`。

- 默认 `DEPLOY_DIR=/mnt/workspace/ai-rd-platform-notebook`（存在 `/mnt/workspace` 时），`PORT=8000`，监听 `127.0.0.1`。只接管本工具管理的独立目录，不能把已有业务目录当成空目录覆盖。
- 应用使用 uv 0.12.11 建立的独立 Python 3.12 环境。`GPU_PYTHON` 应指向 Notebook 原有含 PyTorch/CUDA 的 Python，不能用没有 torch 的应用 venv 误判 GPU。
- `ACTION='deploy'` 部署并验收；重复执行复用自己的进程和口令。`ACTION='stop'` 验证 PID 与随机实例身份后停止服务并保留数据。
- 端口面板和公网预览尚未实测。遇到 `Invalid host` / 跨来源拒绝时，按实际预览页设置精确 `PUBLIC_ORIGIN`，先正常 stop 再 deploy；不要改为通配来源或去掉认证。
- 全功能 HTTP 验收会创建合成项目，要求全局管理员和另一个用户的审核身份。Notebook 自行生成并传递这些口令，不要公开命令行或日志里的凭据。
- Notebook 执行输出中的口令折叠区是私密输出；公开报告只用 `acceptance.zip`，不要提交执行后的原始 Notebook、私有配置、业务数据库或完整安装日志。
- `acceptance.zip` 正常包含 `smoke.json`、`functional.json`、`gpu.json`、`summary.json`、`manifest.json`。先核对清单、大小、散列和实际结果；失败包可能只有 summary 与 manifest。
- GPU 只有真实 CUDA 数值计算成功才为 PASS；应用通过但 GPU 未通过时总体 PARTIAL。Notebook 实例停止后服务会中断，不是常驻生产托管。

## 8. 建议接手顺序与交付条件

1. 克隆并记录接手 SHA，阅读本文件、coverage 和 Notebook 文档，确认工作树状态。保留已实现 GUI 和功能，按实际缺口推进。
2. 恢复目标魔搭登录并核实已有 GPU、原始 Python/CUDA、工作目录及待上传文件。先排查是否已有安装，避免重复部署与误停其他服务。
3. 运行已校验的交付 Notebook 或经同等 payload 校验的部署 CLI，完成应用安装、13 + 61 HTTP 检查、实际 CUDA 验收，保存脱敏结果。
4. 打开实际端口预览，验证认证、项目创建、数据导入、训练/预测、工作流操作和下载；完成真实浏览器及手机布局/触控检查。记录访问方式与截图，截图不能出现口令。
5. 修复现场发现的具体问题；有代码变更时回归相应功能并通过发布验证。根据 coverage 继续处理已授权范围的功能缺口；依赖真实硬件/服务的条目保留明确状态。
6. 将代码、脱敏验收证据和更新后的完成/未完成清单推送用户仓库，提供运行入口、复现步骤和具体剩余阻塞。远端硬件、外部服务和 UI 每类都只能按实际证据写 PASS。

## 9. 可直接发给 Codex 的执行指令

> 接手公开仓库 https://github.com/QuJindai/AI-RD-Platform 。先阅读根目录 CODEX_HANDOFF.md、README.md、docs/coverage.md 和 docs/notebook-deployment.md，核实实际代码和工作树，继续现有 GUI 与功能开发。优先完成已有魔搭 GPU Notebook 的实际部署、13+61 项 HTTP 验收、真实 CUDA 测试、端口访问和手机端验证。已有 332 项 Python、27 项 JS 及本地 Notebook 验证是历史基线，魔搭远端验收仍未执行；不得混淆。遇到已授权且可逆的操作直接推进，认证需要用户时再提供安全登录入口。完成后提交代码和脱敏证据，给出访问入口及明确的剩余缺口。
