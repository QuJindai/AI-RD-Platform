# AI-RD-Platform Implementation Plan

**Goal:** 创建可安装、可运行、有真实数据/模型/工作流闭环的独立公开AI研发平台，并如实追踪完整技术要求。

**Architecture:** FastAPI控制面、SQLite/内容寻址存储、独立纯函数引擎、同源中文控制台。复用scikit-learn及Ollama协议；平台数据与部署密钥留在运行目录和环境中。

**Tech Stack:** Python 3.12, FastAPI, SQLite, NumPy, scikit-learn, pytest, vanilla JavaScript.

**Spec:** docs/design.md

## Global Constraints

- 公开仓库只包含本项目代码、通用文档、合成样例，不复制其他私有项目代码或实际业务数据。
- 不伪造训练、服务连接、GPU资源或测试结果；未实现能力在覆盖表明确列出。
- 版本来源、项目隔离、配额、取消/重启、API错误和中文界面均需验证。
- 测试先于对应行为实现；修改域明确；子任务不得生成子代理。

## Task 1: 平台控制面

Files: `ard/store.py`, `ard/service.py`, `ard/api.py`, `ard/security.py`, `tests/test_platform.py`。

- [x] 建立健康、项目、版本保存、审计、错误及访问控制测试，确认实现前失败。
- [x] 实现持久化、事务与审计，内容SHA文件存储和配额。
- [x] 实现后台任务队列与取消/恢复失败状态。
- [x] 用`python -m pytest tests/test_platform.py -q`验证。

## Task 2: 数据和CPU模型引擎

Files: `ard/engines/data.py`, `ard/engines/models.py`, `tests/test_data.py`, `tests/test_models.py`。

接口与限制见design.md中的两个完整模块契约。此任务可以在控制面编写期间独立执行。

- [x] 先编写实际行为测试：CSV/JSONL导入、危险ZIP、每个变换、确定性无泄漏拆分；分类/回归训练、保存恢复预测、缺值、非法数值、目标泄漏和测试集隔离。
- [x] 用`python -m pytest tests/test_data.py tests/test_models.py -q`确认实现前缺失，再实现。
- [x] 参数JSON应能通过`json.dumps(..., allow_nan=False)`且不依赖pickle。
- [x] 同一命令验证后提交引擎与测试。

## Task 3: 知识和工作流

Files: `ard/knowledge.py`, `ard/workflows.py`, `ard/connectors.py`, `tests/test_workflows.py`。

- [x] 验证文本切片、中文关键词检索与原文引用；不命中时返回空证据。
- [x] 拒绝循环、重复节点、悬空边、非法节点参数及跨项目资产。
- [x] 验证持久化的人工等待与恢复、节点日志、失败状态和取消。
- [x] Ollama真实协议适配及连接错误，不返回模拟生成答案。

## Task 4: 中文控制台与交互

Files: `ard/static/index.html`, `ard/static/app.js`, `ard/static/style.css`。

- [x] 根据OpenAPI实现五页签、项目切换与表单；空状态、加载态、错误态明确。
- [x] 数据上传/清洗、模型训练/预测、知识录入/检索、工作流运行/审批、审计与资源均有可操作入口。
- [x] UI不嵌入假指标，输出使用textContent而非不可信innerHTML。
- [ ] 用真实浏览器验证桌面/手机布局及全链路操作。当前受管浏览器阻止回环访问，待在目标机器完成。

## Task 5: 交付与核验

Files: `README.md`, `requirements*.txt`, `pyproject.toml`, `Dockerfile`, `compose.yaml`, `scripts/`, `.github/workflows/ci.yml`, `docs/coverage.md`, `docs/verification.md`。

- [x] 提供本机启动、Windows启动、Docker Compose以及合成演示脚本。
- [x] 执行完整pytest、真实HTTP闭环、清洁安装及安装中断恢复测试。
- [ ] 完成真实浏览器操作、Windows和Docker运行验证，见verification.md的环境限制。
- [x] 独立代码审查并修复阻塞缺陷。
- [x] 提交并推送公开仓库，读回固定提交及文件，核验状态。公开主分支源码树与本地已测试源码树一致；远端CI状态以Actions记录为准。
