# AI-RD-Platform 设计与交付边界

此文保留 v0.1 的核心设计基线。v0.2 的新增模块、统一接口、数据源与实际完成边界见[功能规格](functional-spec.md)、[模块接口说明](features/data.md)和[当前覆盖表](coverage.md)；验证以[本轮记录](verification-v0.2.md)为准。

日期：2026-09-12。独立公开项目。需求来源为用户提供的AI研发平台技术要求；公开仓库只包含独立实现、通用需求摘要和合成样例。

## 产品流程

创建项目 → 导入数据 → 清洗/拆分并封存版本 → 审批转库 → 训练/评估 → 部署预测 → 知识检索/工作流调用 → 审计与资源观察。

控制台包含总览、数据工坊、模型实验、智能体工作流、运维资源五个页签。所有计数、状态和指标读取后端实际数据；空状态显示操作入口。中文界面，适配桌面与手机，不采用外部CDN。

## 实现架构

- Python 3.12、FastAPI REST API、原生HTML/CSS/JavaScript控制台；SQLite WAL和内容寻址文件持久化。
- 数据与模型版本不可变，来源版本显式记录。项目隔离、配额、乐观并发检查、审计链由统一存储层承担。
- 两个后台CPU执行槽；任务排队、进度、日志、取消。重启时将遗留运行中任务标记为中断，不伪报完成。
- scikit-learn负责实际数值表格训练和评估；模型导出为可检查的JSON参数，不加载外部pickle。
- 关键词检索为无需下载模型的本地路径。Ollama聊天和向量协议作为可配置适配器；未连接服务时明确报告不可用。
- 工作流为经过验证的DAG；支持input、clean、filter、select、derive、train、predict、retrieve、llm、human、output等节点。人工节点持久化阻塞，审批后继续。
- 原要求涉及的大规模GPU隔离/调度、深度模型全量训练、全格式互转及企业三员运维，需要相应引擎与目标硬件。追踪表分别标明本地实现、适配入口、未完成和环境待验证，不能以CPU测试替代。

## 模块接口

### `ard/engines/data.py`

纯函数，不读写全局状态。验证错误抛出`ValueError`。

```python
parse_table(filename: str, content: bytes) -> list[dict]
profile(rows: list[dict]) -> dict
transform(rows: list[dict], operations: list[dict]) -> list[dict]
split_rows(rows: list[dict], ratio: float, seed: int = 42) -> tuple[list[dict], list[dict]]
parse_archive(content: bytes) -> list[dict]  # 合并ZIP内可识别表格，拒绝危险/超限成员
```

支持UTF-8 CSV/JSON数组/JSONL；上限50,000行、200列、20MiB压缩包、100MiB展开、200个成员。操作catalog至少十种，每种必须有实际执行：drop_duplicates、drop_empty、strip、fill_missing、select、rename、filter、cast_numeric、lowercase、replace、clip。操作格式`{"type":"...", ...}`；具体字段见模块文档。无效操作/字段明确拒绝。变换不改变输入。`filter`使用column、op(eq/ne/gt/ge/lt/le/contains)、value；不得eval。

### `ard/engines/models.py`

```python
train_model(rows: list[dict], target: str, task: str = "classification", features: list[str] | None = None, test_fraction: float = 0.25, seed: int = 42) -> dict
predict_model(artifact: dict, rows: list[dict]) -> list
```

train_model返回`artifact`、`metrics`、`train_rows`、`test_rows`、`features`。task为classification/regression；数值特征，拒绝目标泄漏、非有限值与不可训练样本；填补/缩放只拟合训练集。训练输出为JSON可序列化参数，预测函数独立恢复结果。分类返回accuracy、precision、recall、f1、confusion_matrix；回归返回mse、rmse、mae、r2。输入行数至少12且评估集不为空。

### 存储和服务

`Store(root)`负责记录、文件与审计；统一记录`id, kind, project_id, revision, created_at, updated_at, data`。实体JSON保留版本，客户端修改可变对象须传expected_revision。项目、数据、模型、工作流、运行、审批、发布分别记录。所有生成路径使用服务端UUID或内容SHA。

`create_app(data_dir=None, identities=None)`构造FastAPI应用，供测试使用隔离目录。API前缀`/api`；错误统一`detail`。UI直接使用同源API；API文档`/docs`。

## 身份模式

默认本机开发模式绑定127.0.0.1，客户端仅允许回环连接；远程部署必须配置访问令牌。令牌映射配置通过环境传入，不写入仓库。访问者具备一个角色和项目范围；服务端执行权限判断。生产多用户审批禁止创建者自审。独立本机模式可由操作者完成整条演示，但不据此声称通过企业三员分立验收。

## 验证原则

API集成用隔离临时目录，真实CSV、真实CPU训练和真实持久化；客户端检查上传、清洗、训练、预测、检索、工作流和审计。容器构建与GPU项目必须实际执行后才能标为通过。每一发布附完整要求覆盖表及未验证项。
