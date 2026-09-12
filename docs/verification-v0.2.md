# v0.2.0 功能验收记录

日期：2026-09-12。对象：独立公开的 AI-RD-Platform v0.2.0。全部业务数据与身份样例均为合成数据；外部服务协议测试与真实本机执行分开记录。

| 验证 | 实际结果 | 原始证据 |
|---|---|---|
| Python 全量行为/API 测试 | 319 通过，0 失败，0 跳过；2 项 Starlette/httpx 依赖弃用警告 | [JUnit](evidence/v0.2/pytest.xml) |
| JavaScript/DOM 行为 | 26 通过；正式 HTML 与全部脚本经非渲染 DOM 加载并连接真实 HTTP API | [TAP](evidence/v0.2/ui-tests.tap) |
| 原有 HTTP 主链 | 13 项通过；CSV → 版本 → 审批 → 实际训练/预测 → 知识/人工审批 → 审计 | [HTTP 记录](evidence/v0.2/http-smoke.json) |
| 新功能 HTTP 主链 | 61 项通过，93 次 HTTP 请求；使用独立管理员/审核者，完整覆盖数据、标注、文档、模型、工作流、技能、运维、依赖包与备份 | [功能 HTTP 记录](evidence/v0.2/functional-smoke.json) |
| 安装包 | wheel 在隔离虚拟环境安装；从仓库外工作目录启动实际 HTTP 服务；38 个运行代码/静态文件与当前源码逐字节一致 | [包与源文件哈希](evidence/v0.2/package-verification.json) |
| 恢复 | 实际恢复数据、训练模型预测、文档与有效审计链；同时验证路径穿越、符号链接、损坏/缺失对象、非空目标等拒绝场景 | JUnit 中 test_operation_features |
| 依赖 | 锁定版本安装成功；隔离环境 pip check 无冲突；Node 版本及依赖逐项记录 | [环境](evidence/v0.2/environment.json) |
| GUI 基线 | 既有独立合成预览重建无差异；新的正式功能与早期演示分别保留 | docs/gui 与本轮 DOM 证据 |
| 公开仓库 CI | 工作流对提交执行安装、全量 Python/Node 测试、包一致性和仓库外 HTTP 验收；以对应提交运行结果为准 | [Verify platform](https://github.com/QuJindai/AI-RD-Platform/actions) |

Python 回归包含实际 XLSX/YAML/XML/HTML/Parquet 解析、图片解码、ffmpeg 生成的视频与 ffprobe 检查、SQLite 只读快照、模型数学结果、文件/检查点持久化、审批/配额竞争，以及故障和权限路径。DOM 证据包含项目/身份/选中项切换、忙碌按钮复用、旧响应隔离、JSON 下载、实际 SVG 连线与 JSON 往返、键盘/拖动排序和安全文本显示。

新 HTTP 脚本逐项保存方法、路径、状态、耗时、响应摘要和产物 ID。最后下载实际平台备份并检查成员大小/摘要、SQLite 完整性、对象引用与审计；单独的恢复测试验证真正恢复后的内容，不能将只读 ZIP 检查代替恢复。

## 审查与已修复问题

- 暂停任务仍计入额度；新任务与恢复/重试在事务内读取当前项目上限，避免使用旧额度。
- 新数据引用和归档检查与创建共享事务。旧工作流创建入口已统一到导入/版本入口，保存时拒绝跨项目、不存在或已归档引用；并发归档测试通过。
- 界面 reset 恢复原按钮状态，再应用新身份权限；旧异步任务通过上下文与操作对象校验，不能重新启用审核员写按钮或覆盖新任务。
- 项目设置修改后重新读取名称与额度；刷新时保留有权限的当前项目。JSON 数据导出显式使用 Blob。
- 最初 HTTP 脚本错误排除了当前项目本体审计；改为同时验证 project_id 归属以及全局项目事件的 entity_id 归属，再全链重跑通过。后端未放宽审计范围。
- 独立只读审查确认两项 Important 问题已关闭；在其审查范围内没有未处理的 Important/Critical 项。

## 明确未验证或未实现

真实浏览器/目标触控设备的布局、媒体播放与交互视觉仍未验收。当前浏览器访问策略阻断了预览地址，本轮未绕过该限制；DOM 模型不计算真实布局。

Docker CLI 与 GPU 硬件不可用。Docker 逻辑通过参数、归属及生命周期协议夹具验证；模型与 MCP 适配使用合成协议服务。真实 Ollama/OpenAI 兼容服务、第三方 MCP、PostgreSQL/MySQL 目标服务器仍需现场验证。GPU/vGPU 集群、深度模型训练/压缩、通用格式转换和企业高可用尚未实现，详细边界见[覆盖表](coverage.md)。

## 复现

~~~bash
python -m pip install -r requirements-dev.txt
python -m pytest -q --junitxml=test-results/junit.xml
npm ci --ignore-scripts
npm test
python -m pip wheel --no-deps --wheel-dir dist .
python scripts/verify_wheel.py dist/ai_rd_platform-0.2.0-py3-none-any.whl --output test-results/package-verification.json
~~~

使用独立环境安装 wheel，从仓库外目录启动服务，再提供合成管理员与不同审核者令牌运行两个 HTTP 脚本。完整命令由 [.github/workflows/ci.yml](../.github/workflows/ci.yml) 固化。

旧版 [v0.1 验证记录](verification.md)、工程基线以及 GUI 1.0 哈希保留历史身份，不用于替代本轮安装包证据。当前安装包及被测源码以 v0.2/package-verification.json 的摘要为准。
