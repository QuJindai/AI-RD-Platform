# Notebook 部署与验收 · 0.2.1

此入口用于在已经启动的 Linux Notebook 内完成安装、启动与自动验收。手机端可以直接使用魔搭自身页面上传、运行文件。脚本不登录平台、不领取额度、不创建或购买算力，也不依赖远程浏览器接管。

## 使用

1. 在自己的魔搭页面打开可用的 GPU Notebook，并上传 [AI_RD_ModelScope.ipynb](../deploy/AI_RD_ModelScope.ipynb)。
2. 打开文件，运行唯一代码单元格。首次下载依赖需要联网；无需克隆 GitHub 仓库。
3. 下载输出中的 `acceptance.zip`。这是用于回传诊断的文件，排除了访问口令、业务数据库和运行日志。
4. 从 Notebook 的端口/Port 面板打开输出中的服务端口，默认 `8000`。访问地址末尾保留 `/`。
5. 点击 Notebook 输出中的“查看本工作台访问口令”，复制到工作台右上角的凭据设置。该口令仅用于新部署的 AI-RD 工作台。

如果端口预览页面报 `Invalid host` 或 `跨来源请求被拒绝`，将端口页面的精确网站来源填入 `PUBLIC_ORIGIN`，例如 `https://notebook.example`。只填写协议、域名及必要端口，不含路径、查询参数、账号或密码。先以 `ACTION='stop'` 停止本工具的服务，再改回 `ACTION='deploy'` 运行。配置会登记这一个来源和域名；所有 API 仍要求访问令牌，其他来源仍被拒绝。

不同 Notebook 产品的端口预览、存储和可用算力存在差异，需以账号中的实际配置为准。默认目录优先使用现有 `/mnt/workspace/ai-rd-platform-notebook`，否则使用当前目录。存在该路径本身不能证明平台保证持久化；重要数据需要自行备份。

## 执行内容

| 项目 | 行为 |
|---|---|
| 程序来源 | 内置 wheel、依赖锁文件、HTTP 验收脚本；载入前验证 SHA-256 和包成员 |
| Python | 用 uv 0.12.11 建立独立 Python 3.12 环境；不替换 Notebook 的 Python/PyTorch |
| 访问控制 | 默认只监听 `127.0.0.1`；生成不同的管理员、审核员口令，保存为权限 `0600` 的私有文件 |
| 运行恢复 | 重复运行复用同一进程和口令；重跑测试使用新的合成验收项目；停止后数据保留 |
| 进程边界 | 只管理带本工具标记的目录；停止时通过已认证接口核对 PID 和每次启动的随机实例标识，兼容容器 PID 命名空间 |
| 应用验收 | 13 项基础 HTTP 验收和 61 项完整功能验收；含真实训练、审批、工作流、导出和备份验证 |
| GPU 验收 | 查询型号/驱动/显存；在 Notebook 原有 Python 中执行 CUDA FP32 矩阵计算，核对数值和计时 |
| 失败处理 | 有管理标记的部署目录会保留错误结果 ZIP；完整安装/运行日志仅保留在该目录，不自动回传 |

GPU 结果只有真实 CUDA 计算成功才是 `PASS`。缺少 PyTorch、没有 CUDA 设备或探测失败分别标为 `UNVERIFIED`、`UNAVAILABLE` 或 `FAIL`。应用通过而 GPU 未通过时，总体为 `PARTIAL`。

当前应用的 scikit-learn 训练仍使用 CPU；CUDA 矩阵测试通过不等于已经部署 GPU 模型训练、微调或推理服务。外部浏览器显示效果、云平台端口路由和外部 LLM 连接仍需单独验证。

Notebook 停止或平台回收算力后，服务也会停止；本方案不承诺常驻运行。该文件只针对已获得使用权限的环境执行，不改变平台验证要求。

## 构建与开发验收

```bash
python -m pip install -r requirements-dev.txt
python -m pip wheel . --no-deps --wheel-dir dist
python scripts/verify_wheel.py dist/ai_rd_platform-0.2.1-py3-none-any.whl
python scripts/build_notebook.py --wheel dist/ai_rd_platform-0.2.1-py3-none-any.whl
python -m pip install ipython==9.5.0
python scripts/verify_notebook.py --notebook deploy/AI_RD_ModelScope.ipynb --output test-results/notebook-validation.json
```

`verify_notebook.py` 执行实际交付的 Notebook 代码，仅替换部署目录、临时端口、测试来源和启停动作。验证新安装、复用、停止后恢复、原有项目保留、口令保留，以及导出包成员和 SHA-256。失败的本地验证目录保留以便诊断；成功则清理测试目录。

它还逐字节对比内置 wheel 的 38 个运行文件与当前源文件，并核对内置部署/验收脚本和锁文件，避免 Notebook 携带旧代码却被当作当前版本验收。

## 本次验证证据

证据位于 [`docs/evidence/v0.2.1`](evidence/v0.2.1)。本地 Linux 运行结果不等于魔搭账号实测；`notebook-validation.json` 的 `modelscope_execution` 和 `browser_rendering` 明确记录未运行的边界。

2026-09-12 本地验证结果：

| 验证 | 结果 |
|---|---|
| Python 回归 | 332 项通过；2 条上游弃用提示 |
| JavaScript 行为 | 27 项通过，包括代理前缀下的资源与实际 API 请求；使用非渲染 DOM |
| 交付 Notebook | 新安装、同进程复用、停止后重启均通过，每轮 13 + 61 项 HTTP 验收 |
| 进程与数据恢复 | 错误实例标识拒绝停止；正常停止确认进程退出；重启保留项目与口令 |
| 程序包一致性 | 内置 wheel 的 38 个运行文件与源文件逐字节一致 |
| 远端 GPU、页面显示 | 魔搭账号实测尚未执行；本地 PyTorch 不可用，GPU 如实记为 `UNVERIFIED` |

## 官方参考

- [魔搭 Notebook 部署应用示例](https://modelscope.cn/learn/2071)：平台提供的 Notebook/端口使用示例，实际账号界面可能不同。
- [uv 安装与管理 Python](https://docs.astral.sh/uv/guides/install-python/)：独立 Python 版本的安装与使用。
- [Jupyter Server Proxy 实现](https://github.com/jupyterhub/jupyter-server-proxy/blob/main/jupyter_server_proxy/handlers.py)：代理路径和请求头行为的实现依据。本项目以相对资源路径和显式配置来源适配代理。
