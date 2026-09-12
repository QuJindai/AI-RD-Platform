# 工作流、技能与受限智能体

此模块使用 FastAPI、SQLite 检查点和现有数据/知识/模型引擎。可视化编辑器操作实际节点和连线；JSON、保存的版本和执行器使用同一结构。聊天规划和 LLM 节点只调用服务器配置的聊天连接器。未配置模型时明确返回错误。

## 工作流定义和互换

原有接口保留：

- `GET /api/projects/{pid}/workflows`：项目内版本列表。
- `POST /api/projects/{pid}/workflows`：`{"name":"名称","nodes":[...],"edges":[...],"parent_id":null}`，创建不可变版本。
- `POST /api/workflows/{id}/run`：`{"input":{"asset_id":"已保存数据版本ID"}}` 或 `{"input":{"rows":[{"x":1}]}}`。不能同时提供 `asset_id` 和 `rows`。返回持久任务，HTTP 202。
- `GET /api/jobs/{id}` 和 `GET /api/projects/{pid}/jobs`：实际状态、进度、完整日志、检查点和结果。
- `POST /api/jobs/{id}/cancel`：`{"expected_revision":当前修订号}`。

新增接口：

| 方法与路径 | 请求或返回 |
| --- | --- |
| `GET /api/workflow-node-types` | 实际16种节点、各类型允许的参数键、运行限制 |
| `POST /api/projects/{pid}/workflows/validate` | `{name,nodes,edges}`；返回 `{valid,order,incoming}`，并验证显式资产引用 |
| `GET /api/workflows/{id}/export` | 下载 `ard-workflow-v1` JSON 包 |
| `POST /api/projects/{pid}/workflows/import` | 下方JSON包，可附加 `parent_id`；导入为新版本，HTTP 201 |
| `GET /api/workflows/{id}/versions` | 同一祖先的全部版本，包括分支版本 |
| `POST /api/workflows/{id}/versions` | `{name,nodes,edges}`；创建所选版本的子版本，HTTP 201 |
| `POST /api/jobs/{id}/pause` | `{expected_revision}`；请求协作暂停 |
| `POST /api/jobs/{id}/resume` | `{expected_revision}`；恢复已经处于 `PAUSED` 的任务，HTTP 202 |
| `POST /api/jobs/{id}/retry` | `{expected_revision}`；从检查点重试 `FAILED` 或 `INTERRUPTED` 工作流，HTTP 202 |

可导入的最小流程包：

```json
{
  "format": "ard-workflow-v1",
  "workflow": {
    "name": "合成输入透传",
    "nodes": [{"id":"in","type":"input"},{"id":"out","type":"output"}],
    "edges": [{"source":"in","target":"out"}]
  }
}
```

节点只接受 `id`、`type`、`params`；边只接受 `source`、`target`、条件边的布尔 `when`。导出保留节点顺序和显式数据/模型ID，不复制底层资产；跨项目导入必须先将这些ID替换为目标项目内的资产。所有引用在执行时再次进行项目权限检查，已归档数据不能作为新的执行输入。定义最多40节点、100边、2MiB；仅允许有向无环图，一个输入、至少一个输出。普通节点只接受单条输入，`human`、`output` 可汇合多条输入。

旧创建接口、导入和子版本保存统一调用 `save_workflow(s, pid, body, user, parent=None)`。图结构、固定数据/模型引用的项目权限、固定数据版本未归档状态、父工作流版本和新记录创建在同一事务内完成；并发归档不能在引用检查与创建之间插入。`validate` 接口复用相同引用检查。当前固定数据引用由 `train.params.asset_id` 提供，固定模型引用由 `predict.params.model_id` 提供；`input.params` 仍为空，输入数据ID由运行请求的 `input.asset_id` 提供并在执行时检查。

原有11种节点保留：`input`、`clean`、`filter`、`select`、`derive`、`train`、`predict`、`retrieve`、`llm`、`human`、`output`。训练仍要求已审批的精确数据版本；经过变换的行必须先保存并审批。

## 五种新增节点

| 类型 | 参数示例 | 实际行为 |
| --- | --- | --- |
| `condition` | `{"path":"enabled","op":"eq","value":true}` | 比较当前输入的JSON字段，仅执行匹配分支；必须连接 `when:true` 与 `when:false` 两种边 |
| `variable` | `{"values":{"batch":{"$path":"batch"},"prefix":"样本"}}` | 将字面量或输入字段复制到 `variables`；同一节点中的引用均读取节点的原始输入 |
| `template` | `{"template":"批次 {{variables.batch}}","target":"text"}` | 安全替换JSON字段；结果写入当前对象的 `text` 字段，保留其他字段 |
| `iterate` | `{"items_path":"rows","max_items":100,"operations":[{"type":"strip"}],"template":"第 {{index}} 项 {{item.x}}","target":"summary"}` | 逐个处理对象列表，调用真实清洗引擎并可生成逐行文本；输出 `rows` 和 `iteration_count` |
| `dataset_output` | `{"name":"实际处理结果","tags":["合成验证"]}` | 将 `rows` 注册为不可变数据版本；输入 `asset_id` 作为父版本；返回新 `asset_id` 与 `dataset` 元数据 |

`condition.op` 为 `eq`、`ne`、`gt`、`ge`、`lt`、`le`、`contains`、`exists`、`truthy`。大小比较只接受有限数值；`contains` 只接受字符串。相等比较区分布尔值与数值类型。不存在的路径仅允许由 `exists` 检查，否则失败。路径只允许对象字段与点分隔数组索引，如 `rows.0.x`；不允许属性查找、表达式、函数调用或宿主代码。

条件结果持久保存在 `state.branches`，未执行节点在 `skipped_nodes` 列表中。汇合节点接收实际执行分支的值：一个有效前驱直接透传，多个有效前驱返回 `{"inputs":[...]}`。结果只包含实际执行的输出节点。

`iterate.max_items` 为1到100，超过上限直接失败，不截断输入。迭代元素必须为对象，必须至少有 `operations` 或 `template`；模板可读取 `item`、从0开始的 `index` 和继承的 `variables`。每个元素单独执行清洗操作，因此跨行去重应放在 `clean` 节点完成。迭代不接受子工作流、递归或任意代码。`variable.values` 最多30个变量；模板最多20000字符、输出最多1MiB，替换过程在超过输出预算时立即终止。

## 暂停、审批、取消与重试

| 当前状态 | 允许操作 | 结果 |
| --- | --- | --- |
| `QUEUED` | 暂停 | 立即持久化为 `PAUSED` |
| `RUNNING` | 请求暂停 | 先保留 `RUNNING` 和 `pause_requested:true`，到下个检查点转 `PAUSED` |
| `PAUSED` | 继续或取消 | 同一任务ID、同一版本、已提交节点保留 |
| `WAITING_APPROVAL` | 既有人工审批或取消 | 保持原审批原子性；无需额外恢复调用 |
| `FAILED`、`INTERRUPTED` | 重试 | `retry_count` 加一，仅执行未提交节点；待审批或已驳回申请会阻止重试 |
| `CANCELLED`、`SUCCEEDED` | 查看记录 | 不允许恢复或重试 |

CPU训练和网络调用不能在任意指令处强制抢占。暂停或取消在节点边界、迭代元素边界以及副作用提交前检查；未提交的当前计算可能在继续时重算。数据/模型注册与该节点检查点使用同一 SQLite 事务；中途失败会回滚元数据和审计，因此重试不会重复注册已提交的数据或模型。检查点失败可能留下未被引用的内容寻址blob，但不会形成可见资产或重复审计；不自动擦除blob。

人工节点先原子提交审批与检查点，再让出执行线程。原有审批接口仍为 `POST /api/approvals/{id}/decide`，请求 `{decision:"approve"|"reject",expected_revision,comment}`。取消后的审批不能启动任务，驳回不能通过重试绕过，已通过审批不会重复生成。

项目活动配额包含 `QUEUED`、`RUNNING`、`WAITING_APPROVAL`、`PAUSED`，提交/重试在同一事务读取项目当前限制。重启保留 `PAUSED` 与 `WAITING_APPROVAL`；已经请求暂停的在途工作流恢复为 `PAUSED`，其他在途任务标为 `INTERRUPTED`。单节点输出限制8MiB，累计输出检查点16MiB，日志保留最近200条。

## 八种内置技能

`GET /api/skills/builtins` 返回每个操作的中文名称、说明、真实输入 JSON Schema 和示例。所有操作只接受白名单参数；不接受脚本包、任意HTTP地址或远程执行命令。

| 操作 | 调用参数 | 实际结果 |
| --- | --- | --- |
| `data.profile` | `{asset_id}` | 读取真实数据并计算行列数、列类型、缺失数和唯一值数 |
| `data.transform` | `{asset_id,operations,name?}` | 调用数据引擎，封存有父版本关系的新数据集；`{dataset}` |
| `data.split` | `{asset_id,ratio?:0.8,seed?:42}` | 可复现拆分并原子保存两份非空数据；`{datasets:[...]}` |
| `data.aggregate` | `{asset_id,group_by,value_column?}` | 分组计数；选择数值列时增加有效数量、合计和均值，忽略该列空值，拒绝非数值 |
| `knowledge.search` | `{query,top_k?:5,threshold?:0,mode?:"keyword"}` | 实际检索的原文、文档ID、位置和分数；无命中返回空结果 |
| `knowledge.chunk` | `{document_id,strategy?:"paragraph",chunk_size?:600,overlap?:60,delimiter?:"\n\n"}` | 从已封存原文重新切片，返回精确偏移，不改变文档版本 |
| `model.predict` | `{model_id,rows:[...]}` | 读取实际模型参数并返回预测；最多10000行 |
| `text.template` | `{template,values:{...}}` | 安全JSON字段替换后的 `{text}`，不调用生成模型 |

技能接口：

| 方法与路径 | 请求或返回 |
| --- | --- |
| `GET /api/projects/{pid}/skills` | 项目内所有不可变技能版本及派生的 `publication_status` |
| `POST /api/projects/{pid}/skills` | `{name,description?:"",operation,defaults?:{},parent_id?:null}`；HTTP 201 |
| `GET /api/skills/{id}/versions` | 所选技能同一祖先的全部版本 |
| `GET /api/skills/{id}/export` | 下载 `{"format":"ard-skill-v1","skill":{name,description,operation,defaults}}` |
| `POST /api/projects/{pid}/skills/import` | 上述包；只重新创建定义，不继承发布审批；HTTP 201 |
| `POST /api/skills/{id}/publication` | 无请求体；创建 `PENDING` 发布审核，HTTP 201 |
| `GET /api/projects/{pid}/skill-reviews` | 发布审核历史 |
| `POST /api/skill-reviews/{id}/decide` | `{decision:"approve"|"reject",expected_revision,comment?:""}` |
| `POST /api/skills/{id}/invoke` | `{"arguments":{...}}`；合并该版本默认参数，返回包含实际结果的 `skill_run` |
| `GET /api/projects/{pid}/skill-runs?limit=50` | 最近调用；`limit` 为1到200；包括参数、结果、错误和关联智能体ID |

默认参数最多256KiB，调用合并参数最多2MiB，结果最多8MiB，均计入项目存储配额。成功的写入型技能及其运行结果在同一事务提交。失败调用持久记录为 `FAILED`，HTTP 返回实际失败；服务重启将未结束的技能/智能体记录标为 `INTERRUPTED`，不伪称成功。

创建、导入、调用需要开发者或管理员。草稿仅创建者或管理员可调用；发布申请仅创建者或管理员可提交。审核需要审核员或管理员；令牌模式下创建者不得审批自己的技能。本地单管理员模式遵循原有本地审批规则。发布审批只属于精确技能版本，新版本重新进入草稿状态；导入包也不携带发布权限。

## 受限智能体

`POST /api/projects/{pid}/agents/run`：

```json
{
  "goal": "对指定合成数据生成质量概览",
  "skill_ids": ["明确授权的技能版本ID"],
  "max_steps": 3,
  "input": {"asset_id":"当前项目的数据版本ID"}
}
```

授权技能数量1到8且不重复，`max_steps` 为1到8；输入资料最多256KiB。`GET /api/projects/{pid}/agent-runs?limit=50` 查看运行、模型标识、计划、已完成技能调用ID及失败信息。

运行向服务器配置的聊天模型发出一次实际规划请求，只接受不超过64KiB的严格JSON：

```json
{"steps":[{"skill_id":"已授权ID","arguments":{"asset_id":"项目内ID"}}]}
```

在执行任何技能之前，验证全部步骤的结构、白名单技能ID、参数Schema和项目资产引用；拒绝重复JSON键、额外字段、非有限数值和超过步骤上限的计划。不会执行模型生成的脚本、任意端点或新增技能。参数为JSON字面值，不支持步骤结果引用；本版本按一个已验证的固定计划顺序执行，不承诺自主循环推理。后续实际引擎操作仍可能因具体数据内容或服务状态失败；运行随即停止，历史保留先前已经成功的真实写入，用户可查看对应 `skill_run_ids`。

## 中文界面与验证范围

`workflow-tools.js` 提供实际SVG节点和连线、节点添加/删除/类型参数配置、连线添加/删除、条件分支标签、拖动排序和Alt加上下箭头键盘排序。排序影响独立节点的就绪顺序，图的连线仍决定依赖顺序。每次视觉编辑同步原有 `workflow-nodes`、`workflow-edges` 和名称字段；也可从这些JSON字段重新载入。服务器校验使用同一执行器校验函数。版本保存后可直接使用既有运行输入和人工审批控制台；新的日志区域提供暂停、继续、重试和取消。

`skill-tools.js` 提供八种操作目录、参数规范和示例、版本定义、导入导出、发布审核、实际调用、明确勾选的智能体技能授权和持久运行历史。每个请求捕获身份/项目上下文；身份或项目切换清空输入、图、选择、授权和结果，过期返回不能填入另一项目。

自动化证据位于 `tests/test_workflow_features.py`，并同时运行原有 `tests/test_workflows.py` 和 `tests/test_review_regressions.py`。测试覆盖真实分支/变量/逐行输出、迭代上限、暂停重启恢复、运行中取消、副作用检查点回滚、审批保留、重复重试、八种真实引擎结果、版本发布权限、跨项目拒绝、模板放大上限以及受限计划验证。聊天计划测试使用明确的协议fixture验证白名单逻辑，不能作为真实第三方聊天服务可用性的证据。

JavaScript语法通过 `node --check` 验证；`node --test tests/test_workflow_ui.mjs` 的5项DOM单元测试验证实际SVG连线与JSON保存、键盘/拖动事件的排序变更、安全文本渲染和上下文切换后的延迟响应隔离。这些测试不等同于浏览器渲染验收。由于浏览器URL政策阻止本地预览，实际浏览器/设备呈现、指针交互和辅助技术表现仍未现场验证。本模块不包含LangGraph IDE、GPU调度或任意代码执行环境。
