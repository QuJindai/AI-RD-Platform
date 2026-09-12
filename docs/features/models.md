# 模型生命周期与模型服务

`ard.features.models.install(app)` 可重复调用且只安装一次路由。`ard/static/model-tools.js` 使用共享 ARD 界面接口，在“模型实验”页提供所有下列操作。模型包和评估报告通过同源鉴权请求下载；页面不执行模型包内容或服务返回的 HTML。身份、项目或选择发生变化时，旧响应不能填入新选择。

## JSON 模型包与版本

平台包协议 `ard.model-package` v1 仅支持当前 CPU 引擎的数值线性回归和逻辑回归参数，不支持 pickle、任意 Python、神经网络权重转换或外部文件路径。

最小可运行回归包如下；其预测规则为 `y = 2*x + 1`：

```json
{
  "format": "ard.model-package",
  "version": 1,
  "name": "合成回归模型",
  "target": "y",
  "artifact": {
    "format": "ard.linear-model",
    "version": 1,
    "task": "regression",
    "features": ["x"],
    "preprocessing": {
      "imputer": {"strategy": "median", "statistics": [0.0]},
      "scaler": {"mean": [0.0], "scale": [1.0]}
    },
    "estimator": {
      "type": "linear_regression",
      "coefficients": [[2.0]],
      "intercepts": [1.0]
    }
  }
}
```

分类包改用 `task: "classification"`、`estimator.type: "logistic_regression"`，并添加至少两个同类型、不重复的 JSON 标量类别 `classes`。二分类允许一行或两行系数，多分类系数行数必须等于类别数。回归只能有一行系数且没有 `classes`。

导入前验证完整嵌套结构、未知字段、协议版本类型、特征唯一性、目标列排除、参数维度、有限数值、正缩放比例、类别类型与数量；额外执行一次仅含缺失特征的安全预测以检查派生数值。模型参数必须是 JSON 数值，不能以字符串或布尔值替代。上限为 1,024 个特征、256 个类别/系数行、包名称 400 字符（兼容原引擎生成的长名称）、特征名 200 字符、类别字符串 2,000 字符以及规范编码后 8 MiB 的包。

导入模型保存为空训练指标、未知训练来源和独立的新版本。不会相信外部包声称的精度。导出包只携带可执行的 JSON 参数、名称和目标列；训练来源和本平台评估记录不随包迁移。原有 `/api/models/{id}/artifact` 仍导出裸参数，不能直接当作包导入，须按上述协议包装。

| 方法与路径 | 精确请求体或参数 | 返回 |
| --- | --- | --- |
| `POST /api/projects/{pid}/models/import` | `{"package": <上述包>, "parent_model_id": null}`；父模型字段可省略 | 201，新 `model` 记录 |
| `GET /api/models/{model_id}/package` | 无 | JSON 附件 `model-package-{id}.json` |
| `POST /api/models/{model_id}/versions` | `{"name":"封存的新名称"}` | 201，同一参数内容的新 `model` 记录 |
| `GET /api/models/{model_id}/versions` | 无 | 相同 `version_root_id` 的版本列表，含根版本 |

父版本必须在当前项目中。版本记录有 `parent_id`、`version_root_id`、`version_number`。版本号为父版本号加一；分支可以具有相同版本号，唯一标识始终是记录 ID。新版本不覆盖原模型、指标或历史。已有训练模型的命名新版本复制可验证的训练配置。

## 可复用实验基线

基线固定不可变数据版本的 ID 与摘要，并保存完整训练配置。基线版本使用新记录，禁止更新原记录。读取和运行基线不会把当前编辑中的表单覆盖到已保存配置。

```json
{
  "name": "合成回归基线",
  "dataset_id": "<dataset-id>",
  "config": {
    "target": "y",
    "task": "regression",
    "features": ["x"],
    "test_fraction": 0.25,
    "seed": 42
  },
  "parent_id": null
}
```

`features` 可省略或为 `null`，创建时展开为除目标列之外的所有列并固定下来。`task` 默认 `classification`，测试比例默认 0.25、范围 0.1–0.5，种子默认 42、范围 0–4,294,967,295。至少需要 12 条样本和有效目标/特征列。运行仍使用原有训练审批和项目任务配额，未批准数据返回 409；训练时执行完整数值及类别可训练性校验。

| 方法与路径 | 精确请求体或参数 | 返回 |
| --- | --- | --- |
| `POST /api/projects/{pid}/model-baselines` | 上述基线对象；传 `parent_id` 创建新版本 | 201，`model_baseline` 记录 |
| `GET /api/projects/{pid}/model-baselines` | 无 | 项目基线列表 |
| `GET /api/model-baselines/{baseline_id}` | 无 | 单个基线记录 |
| `POST /api/model-baselines/{baseline_id}/run` | 无请求体 | 202，持久化训练任务；`payload.baseline_id` 指向基线 |
| `GET /api/model-baselines/{baseline_id}/runs` | 无 | 该基线的实际任务列表 |

## 实际评估、共同样本对比与 HTML 报告

评估对选定数据进行真实预测，使用封存的填补统计、均值、缩放比例和系数，不重新拟合。目标列不会传入预测函数，也不能作为模型特征。

对有可验证训练来源的模型，从模型版本或原训练任务读取原始种子和划分配置。选择原始训练数据的同摘要版本时，仅取原始测试行；缺少可验证划分的旧模型不能在原训练数据上评估。选择其他数据时，排除与已知训练样本具有相同特征值的行。特征检查统一数值字符串、正负零，以及按模型填补统计处理的缺失值。

对比先求所有模型可评估行的交集，随后对每个模型使用相同的行，保证分母一致。记录保留实际行数和数据来源说明。上述检查不证明不同数据来源完全独立，也不能检查特征设计中的间接目标泄漏。导入模型训练来源未知，结果明确标注 `unknown_training_provenance`，不能声称是独立留出集精度。

分类指标为准确率、按真实类别支持数加权的 precision/recall/F1，以及混淆矩阵。混淆矩阵顺序保存在结果的 `labels` 字段；允许同类型的新真实类别，但合计不得超过 256 个。回归指标为 MSE、RMSE、MAE、R²；至少两行，全部派生指标须有限。

评估上限为 50,000 行、样本数与特征数乘积 500 万、每个结果对象 20 MiB。归档数据不能参与新评估或新基线。模型与数据必须属于同一项目。新增评估结果与比较中的全部评估记录在一个配额检查/事务边界内发布；失败不留下部分评估记录。

| 方法与路径 | 精确请求体或参数 | 返回 |
| --- | --- | --- |
| `POST /api/models/{model_id}/evaluate` | `{"dataset_id":"<id>","name":null}`；名称可省略 | 201，`model_evaluation`，含 `metrics`、`row_count`、`scope`、`independence`、说明与结果摘要 |
| `GET /api/projects/{pid}/model-evaluations` | 无 | 项目评估列表 |
| `GET /api/model-evaluations/{evaluation_id}` | 无 | 单个不可变评估记录 |
| `GET /api/model-evaluations/{evaluation_id}/rows` | `limit=100`（1–1,000），`offset=0`（非负） | `{"labels":[],"rows":[{"source_row":0,"actual":1,"prediction":1.0}],"total":1}`；源行号从 0 开始 |
| `GET /api/model-evaluations/{evaluation_id}/report` | 无 | HTML 附件 `evaluation-{id}.html` |
| `POST /api/projects/{pid}/model-comparisons` | `{"dataset_id":"<id>","model_ids":["<model-a>","<model-b>"],"name":null}` | 201，`model_comparison`，含共同 `row_count` 和各模型的 `evaluation_id`、`metrics` |
| `GET /api/projects/{pid}/model-comparisons` | 无 | 项目对比记录列表 |

对比需要 2–10 个不同模型，任务和目标列必须一致。HTML 报告转义所有模型名、数据名、标签、特征与其他动态内容，禁用脚本、网络和框架嵌入，并以附件返回。报告包含参数/数据摘要、真实指标、数据范围说明和前 1,000 条逐行结果；完整结果可通过分页端点读取。网页用文本节点展示服务或记录内容，通过鉴权 Blob 下载报告，不嵌入执行报告 HTML。

## 部署统计

`GET /api/projects/{pid}/deployment-statistics` 读取现有实际预测计数，返回：

```json
{
  "source": "recorded_prediction_requests",
  "requests": 0,
  "failures": 0,
  "active": 0,
  "failure_rate": null,
  "mean_latency_ms": null,
  "entries": [],
  "notes": ["计数包含进入实际预测函数的成功和失败请求；鉴权拒绝、请求格式错误和过期服务不计入。"]
}
```

每个 `entries` 元素有 `deployment_id`、`model_id`、`name`、实际生效的 `status`（到期的 ACTIVE 显示 EXPIRED）、`expires_at`、`requests`、`failures`、`successes`、`failure_rate`、`total_latency_ms`、`mean_latency_ms`。零次请求的比率和平均耗时为 `null`，不填入示例数值。统计为累计计数，不提供未采集的吞吐率或延迟分位数。

## 已配置的 Ollama 与 OpenAI 兼容服务

环境配置由服务器管理员维护。此模块没有 HTTP 配置修改端点；密钥不会写入平台公开记录或返回给网页。服务地址只能来自环境变量，不能来自用户请求、模型响应或工作流输出。

| 环境变量 | 含义 |
| --- | --- |
| `ARD_MODEL_PROVIDER` | `ollama`（默认）或 `openai` |
| `ARD_OLLAMA_URL` | Ollama 服务根地址 |
| `ARD_CHAT_MODEL` | Ollama 聊天模型名 |
| `ARD_EMBED_MODEL` | Ollama 向量模型名 |
| `ARD_OPENAI_URL` | OpenAI 兼容 API 根地址，包含服务要求的前缀，如 `https://example.invalid/v1` |
| `ARD_OPENAI_API_KEY` | 可选 Bearer 凭据；远程服务需要凭据时必须配置 |
| `ARD_OPENAI_CHAT_MODEL` | OpenAI 兼容聊天模型名 |
| `ARD_OPENAI_EMBED_MODEL` | OpenAI 兼容向量模型名 |
| `ARD_MODEL_TIMEOUT_SECONDS` | 整次操作总时限，默认 45 秒，允许 1–120 秒 |

仅接受 HTTP/HTTPS、无用户信息/查询串/片段的服务器地址。禁用环境代理和重定向。使用可取消的异步传输实现同步连接器签名，整次请求超时覆盖连接和响应读取；分批向量请求共享同一总期限。另有限定连接/读取超时、8 MiB 单次响应上限、未压缩 JSON 响应要求和完整响应对象校验。不会回显远端错误正文或访问凭据。

聊天输入最多 200,000 字符，响应最多 500,000 字符；文本向量最多 4,096 条、UTF-8 总计 2 MiB，以 32 条分批，每个向量 1–8,192 维，总计最多 8,388,608 个数值。拒绝 NaN、Infinity、溢出、非对象响应、非数值向量、维度变化、条数错配以及重复/越界/错误类型的 OpenAI 向量索引。OpenAI 返回向量按 `index` 恢复输入顺序。

使用的协议路径为 Ollama `GET /api/tags`、`POST /api/chat`、`POST /api/embed`，以及 OpenAI 兼容 `GET /models`、`POST /chat/completions`、`POST /embeddings`（接在配置根地址之后）。聊天发送单条 user 消息和 `stream:false`；向量发送 `model` 和字符串数组 `input`。不支持任意远端自定义函数调用。

| 方法与路径 | 精确请求体或参数 | 返回 |
| --- | --- | --- |
| `GET /api/model-services/configuration` | 无 | `provider`、`configured`、`endpoint_sha256`、`chat_model`、`embedding_model`、`api_key_configured`、`editable:false`、`live_verified:false` |
| `POST /api/model-services/probe` | 无 | 实际模型目录探测结果：`status`、`provider`、`models`、`live_verified:true`；失败返回明确错误 |
| `POST /api/model-services/chat` | `{"prompt":"试用文本"}` | `{"answer":"服务回答","model":"已配置模型名","eval_count":null}` |
| `POST /api/model-services/embeddings` | `{"texts":["第一条","第二条"]}`，网页试用最多 32 条 | `identity`、`count`、`dimensions` 和完整 `embeddings` 数组 |

`GET /api/connectors` 保留 `ollama`、`cpu_training`、`gpu_scheduler`、`mcp_runtime` 的原能力结构，并增加同样为对象结构的 `openai`。当前提供方由配置端点返回。配置状态不证明服务可用。原 `POST /api/connectors/ollama/probe` 始终探测 Ollama，保留原语义。

供知识索引绑定的函数：

```python
from ard.connectors import embedding_identity, embeddings, chat, ollama_probe, capabilities

identity = embedding_identity()
# {"provider": "ollama" | "openai", "model": str, "endpoint_sha256": str}
# 无端点明文或密钥；缺少有效配置时抛 ValueError。
vectors = embeddings(["文本"])  # 有限值的二维 numpy.ndarray
answer = chat("问题")          # {answer, model, eval_count}
```

## 权限、验证与已知边界

读取使用当前项目访问范围。导入、版本创建、基线创建/运行、持久化评估/对比和远端服务调用需要 admin 或 developer。审计员可读取其有权访问的包、报告和统计，不能发起上述写入。跨项目引用即使双方项目都可访问也被拒绝。

模型、`model_baseline`、`model_evaluation`、`model_comparison` 记录不可变。实际对象使用顶层 `sha256`，数据与模型来源摘要也指向已有内容对象；没有任意文件引用。新记录配额检查与写入在事务内完成。

运行验证：

```sh
python -m pytest tests/test_models.py tests/test_model_features.py -q
node --check ard/static/model-tools.js
```

测试覆盖实际导入预测、原始留出集重建、训练特征重叠排除、共同样本比较、真实分类/回归指标、HTML 转义、不可变版本、基线配置复用、权限、配额原子失败，以及 Ollama/OpenAI 响应类型/形状/数值/大小/索引/超时/重定向协议。HTTP 协议测试使用明确标记的合成 MockTransport，未连接真实第三方模型服务。浏览器 URL 策略已阻止本地页面，未尝试绕过；本模块不声称已完成实际浏览器或设备渲染验收。没有 GPU 调度、企业 vGPU 隔离或通用神经网络转换。
