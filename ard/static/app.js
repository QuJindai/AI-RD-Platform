"use strict";

const $ = (id) => document.getElementById(id);
const appBasePath = new URL('.', document.baseURI || 'http://localhost/').pathname;
const state = {
  token: sessionStorage.getItem("ard_token") || "",
  me: null, projects: [], projectId: sessionStorage.getItem("ard_project") || "",
  overview: null, datasets: [], datasetId: "", rows: [], approvals: [],
  jobs: [], models: [], modelId: "", deployments: [], documents: [], workflows: [], audits: [],
  pollTimer: null, generation: 0
};

class StaleContext extends Error {}
function captureIdentity() { return { generation:state.generation, token:state.token }; }
function captureProject() { return { ...captureIdentity(), pid:projectRequired() }; }
function contextIsCurrent(context) {
  return context.generation === state.generation && context.token === state.token && (!("pid" in context) || context.pid === state.projectId);
}
function requireCurrent(context) { if (!contextIsCurrent(context)) throw new StaleContext("界面上下文已变更"); }
function requireOwned(record, context, label) {
  requireCurrent(context);
  if (!record || record.project_id !== context.pid) throw new Error(`${label}不属于当前项目，请刷新后重试`);
  return record;
}

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

function empty(target, message) {
  target.replaceChildren();
  target.className = target.className.replace(/\bloading\b/g, "").trim();
  target.classList.add("empty");
  target.textContent = message;
}

function toast(message, type = "") {
  const item = node("div", `toast ${type}`.trim(), message);
  $("toast-region").append(item);
  window.setTimeout(() => item.remove(), 4200);
}
function invoke(task) { Promise.resolve().then(task).catch((error)=>{ if (!(error instanceof StaleContext)) toast(detail(error),"error"); }); }

function detail(error) {
  return error instanceof Error ? error.message : String(error || "请求失败");
}

async function api(path, options = {}, context = captureIdentity()) {
  const {responseType, ...fetchOptions} = options;
  const headers = new Headers(options.headers || {});
  if (context.token) headers.set("Authorization", `Bearer ${context.token}`);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(appBasePath + path.replace(/^\/+/, ''), { ...fetchOptions, headers });
  requireCurrent(context);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
      else if (Array.isArray(payload.detail)) message = payload.detail.map((x) => x.msg).join("；");
      else if (payload.detail && typeof payload.detail.message === "string") message = payload.detail.message;
    } catch (_) { /* keep status message */ }
    requireCurrent(context);
    if (response.status === 401) {
      $("global-status").hidden = false;
      $("global-status").textContent = "服务需要身份验证。请打开右上角凭据设置并输入访问令牌。";
      $("auth-dialog").showModal();
    }
    const error = new Error(message); error.status = response.status; throw error;
  }
  requireCurrent(context);
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  const payload = responseType === "blob" ? await response.blob() : type.includes("json") ? await response.json() : await response.blob();
  requireCurrent(context);
  return payload;
}

async function action(button, busyText, fn) {
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  try { return await fn(); }
  catch (error) { if (!(error instanceof StaleContext)) toast(detail(error), "error"); throw error; }
  finally { button.disabled = false; button.textContent = previous; }
}

function body(value) { return JSON.stringify(value); }
function parseJson(id, label) {
  try { return JSON.parse($(id).value); }
  catch (error) { throw new Error(`${label}不是有效 JSON：${error.message}`); }
}
function projectRequired() {
  if (!state.projectId) throw new Error("请先创建或选择项目");
  return state.projectId;
}
function shortId(value) { return value ? value.slice(0, 8) : "—"; }
function date(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString("zh-CN", { hour12: false });
}
function bytes(value) {
  const amount = Number(value || 0);
  if (amount < 1024) return `${amount} B`;
  if (amount < 1048576) return `${(amount / 1024).toFixed(1)} KiB`;
  if (amount < 1073741824) return `${(amount / 1048576).toFixed(1)} MiB`;
  return `${(amount / 1073741824).toFixed(2)} GiB`;
}
function statusLabel(value) {
  const labels = { QUEUED:"排队中", RUNNING:"运行中", PAUSED:"已暂停", SUCCEEDED:"已完成", FAILED:"失败", CANCELLED:"已取消", INTERRUPTED:"已中断", WAITING_APPROVAL:"等待审批", PENDING:"待审批", APPROVED:"已通过", REJECTED:"已驳回", ACTIVE:"运行中", STOPPED:"已停止", not_connected:"未连接" };
  return labels[value] || value || "未知";
}
function statusClass(value) {
  if (["SUCCEEDED","APPROVED","ACTIVE"].includes(value)) return "success";
  if (["RUNNING","QUEUED"].includes(value)) return "running";
  if (["PENDING","WAITING_APPROVAL"].includes(value)) return "warning";
  if (["FAILED","CANCELLED","INTERRUPTED","REJECTED"].includes(value)) return "danger";
  return "neutral";
}
function chip(value) { return node("span", `status-chip ${statusClass(value)}`, statusLabel(value)); }
function option(value, label) { const item = node("option", "", label); item.value = value; return item; }
function projectName(id) { return state.projects.find((x) => x.id === id)?.name || shortId(id); }

function switchTab(name) {
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  document.querySelector(".sidebar").classList.remove("open");
}

function resetSelect(id, label) { $(id).replaceChildren(option("", label)); }
function clearProjectState(message = "正在读取当前项目…") {
  window.ARD?.reset();
  Object.assign(state, { overview:null, datasets:[], datasetId:"", rows:[], approvals:[], jobs:[], models:[], modelId:"", deployments:[], documents:[], workflows:[] });
  $("dataset-count").textContent="0"; $("model-count").textContent="0";
  $("dataset-title").textContent="请选择数据集"; $("dataset-seal").textContent="未选择"; $("dataset-seal").className="status-chip neutral";
  $("dataset-meta").replaceChildren();
  ["dataset-list","dataset-preview","merge-options","job-list","workflow-jobs","model-list","model-detail","document-list","search-results","approval-list","workflow-graph"].forEach((id)=>empty($(id),message));
  resetSelect("train-dataset","请选择已审批数据"); resetSelect("deploy-model","请选择模型"); resetSelect("prediction-deployment","请选择活动部署"); resetSelect("workflow-select","请选择工作流");
  $("prediction-result").textContent="预测结果将在这里显示";
}
function resetIdentityBoundary() {
  state.generation += 1;
  Object.assign(state, { me:null, projects:[], projectId:"", audits:[] });
  clearProjectState("身份或项目尚未就绪");
  $("identity-name").textContent="未验证"; $("identity-role").textContent="请检查访问凭据"; $("auth-open").textContent="访";
  resetSelect("project-select","正在验证身份…");
  empty($("overview-counts"),"正在验证身份…"); empty($("overview-jobs"),"正在验证身份…"); empty($("overview-resources"),"正在验证身份…");
  empty($("operations-resources"),"正在验证身份…"); empty($("connector-list"),"正在验证身份…"); empty($("audit-list"),"正在验证身份…");
  $("audit-verification").textContent="尚未执行完整性验证"; $("audit-verification").className="verification-line";
  $("service-state").textContent="正在验证身份"; document.querySelector(".sidebar-foot").classList.remove("online");
  sessionStorage.removeItem("ard_project");
  ["document-name","document-text","search-query","train-target","train-features"].forEach((id)=>{$(id).value="";});
  $("prediction-rows").value='[\n  {"x": 1, "y": 2}\n]'; $("workflow-input").value="{}";
}
async function changeIdentity(token) {
  resetIdentityBoundary(); state.token=token;
  if (token) sessionStorage.setItem("ard_token",token); else sessionStorage.removeItem("ard_token");
  return loadIdentityAndProjects(captureIdentity());
}
async function changeProject(pid) {
  state.generation += 1; state.projectId=pid; clearProjectState(); renderProjects();
  if (!pid) { renderNoProject(); return; }
  await refreshProject(captureProject());
}

async function loadIdentityAndProjects(context = captureIdentity()) {
  const [me, projects] = await Promise.all([api("/api/me",{},context), api("/api/projects",{},context)]);
  requireCurrent(context);
  state.me = me; state.projects = projects;
  $("identity-name").textContent = me.user;
  $("identity-role").textContent = `${me.mode === "local" ? "本机" : "令牌"} · ${me.role}`;
  $("auth-open").textContent = String(me.user || "访").slice(0, 1).toUpperCase();
  $("service-state").textContent = "服务在线";
  document.querySelector(".sidebar-foot").classList.add("online");
  $("global-status").hidden = true;
  if (!projects.some((x) => x.id === state.projectId)) state.projectId = projects[0]?.id || "";
  renderProjects();
  if (state.projectId) await refreshProject(captureProject());
  else renderNoProject();
  await Promise.allSettled([loadOverview(captureIdentity()), loadConnectors(captureIdentity()), loadAudit(captureIdentity())]);
}

function renderProjects() {
  const select = $("project-select");
  select.replaceChildren();
  if (!state.projects.length) select.append(option("", "尚无项目"));
  state.projects.forEach((project) => select.append(option(project.id, project.name)));
  select.value = state.projectId;
  sessionStorage.setItem("ard_project", state.projectId);
}

function renderNoProject() {
  clearProjectState("创建项目后即可开始");
}

async function refreshProject(context = captureProject()) {
  requireCurrent(context);
  const projects = await api("/api/projects", {}, context);
  requireCurrent(context);
  if (!projects.some(project => project.id === context.pid)) throw new Error("当前项目已不可访问，请重新选择项目");
  state.projects = projects;
  renderProjects();
  const results = await Promise.allSettled([loadApprovals(context), loadDatasets(context), loadJobs(context), loadModels(context), loadDeployments(context), loadDocuments(context), loadWorkflows(context)]);
  requireCurrent(context);
  const failed = results.filter((x) => x.status === "rejected" && !(x.reason instanceof StaleContext));
  if (failed.length) toast(`有 ${failed.length} 个区域未能刷新`, "error");
  await loadOverview(context).catch((error) => { if (!(error instanceof StaleContext)) throw error; });
  await window.ARD?.refreshFeatures(context);
}

async function createProject(event) {
  event.preventDefault();
  const button = $("project-create"), context=captureIdentity();
  await action(button, "创建中…", async () => {
    const result = await api("/api/projects", { method:"POST", body:body({
      name:$("project-name").value.trim(), description:$("project-description").value.trim(),
      quota_bytes:Number($("project-quota").value) * 1048576, max_jobs:Number($("project-jobs").value)
    }) },context);
    state.projects.unshift(result);
    $("project-dialog").close(); $("project-form").reset();
    $("project-quota").value = "100"; $("project-jobs").value = "4";
    toast("项目已创建", "success"); await changeProject(result.id);
  }).catch(() => {});
}

async function loadOverview(context = captureIdentity()) {
  const data = await api("/api/overview",{},context); requireCurrent(context); state.overview = data; renderOverview(data); renderResources(data.resources);
}

function renderOverview(data) {
  const labels = [["projects","项目"],["datasets","数据版本"],["models","模型"],["documents","知识文档"],["workflows","工作流"]];
  const grid = $("overview-counts"); grid.replaceChildren(); grid.classList.remove("empty","loading");
  labels.forEach(([key,label]) => { const card=node("article","metric-card"); card.append(node("i"),node("span","",label),node("strong","",data.counts?.[key] ?? 0)); grid.append(card); });
  const jobs = $("overview-jobs"); jobs.replaceChildren(); jobs.classList.remove("loading","empty");
  if (!data.jobs?.length) { empty(jobs,"当前没有任务。导入数据并提交首次训练实验。"); return; }
  data.jobs.forEach((job) => {
    const row=node("div","row-item"), main=node("div","row-main");
    main.append(node("strong","",job.payload?.kind === "workflow" ? "工作流运行" : "模型训练"),node("span","",`${projectName(job.project_id)} · ${date(job.created_at)} · ${job.progress || 0}%`));
    row.append(main,chip(job.status)); jobs.append(row);
  });
}

function resourceLines(container, resources) {
  container.replaceChildren(); container.classList.remove("loading","empty");
  [["CPU 逻辑核心",resources?.cpu_count ?? "未知"],["执行槽",resources?.worker_slots ?? "未知"],["运行任务",resources?.running_jobs ?? 0],["排队任务",resources?.queued_jobs ?? 0],["已存储",bytes(resources?.stored_bytes)],["GPU",statusLabel(resources?.gpu?.status)]].forEach(([label,value]) => {
    const line=node("div","resource-line"); line.append(node("span","",label),node("strong","",value)); container.append(line);
  });
}
function renderResources(resources) {
  resourceLines($("overview-resources"), resources);
  const grid=$("operations-resources"); grid.replaceChildren(); grid.classList.remove("loading","empty");
  const items=[["CPU",`${resources?.cpu_count ?? "未知"} 核心`,"由操作系统实时报告"],["任务槽",resources?.worker_slots ?? "未知",`${resources?.running_jobs ?? 0} 运行 / ${resources?.queued_jobs ?? 0} 排队`],["存储",bytes(resources?.stored_bytes),"当前有权项目记录"],["GPU","未连接","未配置 GPU 调度与遥测"]];
  items.forEach(([label,value,note])=>{const card=node("div","resource-card");card.append(node("span","",label),node("strong","",value),node("small","",note));grid.append(card);});
}

async function loadDatasets(context = captureProject()) {
  const pid=context.pid, data=await api(`/api/projects/${pid}/datasets`,{},context); requireCurrent(context); state.datasets=data;
  if (!data.some((x)=>x.id===state.datasetId)) state.datasetId=data[0]?.id||"";
  renderDatasets(); renderMergeOptions(); renderTrainDatasets();
  if (state.datasetId) await loadDatasetRows(state.datasetId,context); else renderDatasetDetail();
}
function renderDatasets() {
  const list=$("dataset-list"); list.replaceChildren(); list.classList.remove("loading","empty"); $("dataset-count").textContent=String(state.datasets.length);
  if (!state.datasets.length) { empty(list,"还没有数据。可导入文件或创建 60 行合成样例。"); return; }
  state.datasets.forEach((dataset)=>{const button=node("button",`select-item${dataset.id===state.datasetId?" active":""}`);button.type="button";button.dataset.datasetId=dataset.id;button.append(node("strong","",dataset.name),node("small","",`${dataset.row_count} 行 · ${dataset.columns?.length||0} 列 · ${shortId(dataset.id)}`));list.append(button);});
}
async function selectDataset(id) { const context=captureProject();requireOwned(state.datasets.find((x)=>x.id===id),context,"数据版本");state.datasetId=id;state.rows=[];renderDatasets();renderDatasetDetail();empty($("dataset-preview"),"正在加载所选版本…");await loadDatasetRows(id,context); }
async function loadDatasetRows(id, context = captureProject()) {
  try { const data=await api(`/api/assets/${id}/rows?limit=20`,{},context); requireCurrent(context);if(state.datasetId!==id)return;state.rows=data.rows;renderDatasetDetail(data.total); }
  catch(error){ if (contextIsCurrent(context)&&state.datasetId===id&&!(error instanceof StaleContext)) empty($("dataset-preview"),detail(error)); }
}
function renderDatasetDetail(total) {
  const dataset=state.datasets.find((x)=>x.id===state.datasetId); const preview=$("dataset-preview"), meta=$("dataset-meta"); meta.replaceChildren(); preview.replaceChildren(); preview.classList.remove("empty");
  if (!dataset){$("dataset-title").textContent="请选择数据集";$("dataset-seal").textContent="未选择";empty(preview,"选择数据集后预览实际数据");return;}
  $("dataset-title").textContent=dataset.name; $("dataset-seal").textContent=dataset.sealed?"已封存":"未封存"; $("dataset-seal").className=`status-chip ${dataset.sealed?"success":"warning"}`;
  [["版本 ID",shortId(dataset.id)],["记录数",dataset.row_count],["字段数",dataset.columns?.length||0],["大小",bytes(dataset.size_bytes)]].forEach(([label,value])=>{const item=node("div","meta-item");item.append(node("span","",label),node("strong","",value));meta.append(item);});
  if (!state.rows.length){empty(preview,"数据版本没有可预览的记录");return;}
  const columns=dataset.columns||Object.keys(state.rows[0]||{}), table=node("table"), head=node("thead"), hr=node("tr");
  columns.forEach((column)=>hr.append(node("th","",column))); head.append(hr); table.append(head); const tbody=node("tbody");
  state.rows.forEach((row)=>{const tr=node("tr");columns.forEach((column)=>{const value=row[column];tr.append(node("td","",value===null?"null":typeof value==="object"?JSON.stringify(value):value));});tbody.append(tr);});table.append(tbody);preview.append(table);
  if (total>state.rows.length){const note=node("div","verification-line",`显示前 ${state.rows.length} 行，共 ${total} 行`);preview.append(note);}
}
function renderMergeOptions(){const box=$("merge-options");box.replaceChildren();box.classList.remove("empty");if(state.datasets.length<2){empty(box,"至少创建两个数据版本后可合并");return;}state.datasets.forEach((dataset)=>{const label=node("label","check-option"),input=node("input");input.type="checkbox";input.value=dataset.id;input.className="merge-check";label.append(input,node("span","",`${dataset.name} (${dataset.row_count} 行)`));box.append(label);});}
function approvedIds(){return new Set(state.approvals.filter((x)=>x.status==="APPROVED"&&x.approved_asset_id).map((x)=>x.approved_asset_id));}
function renderTrainDatasets(){const select=$("train-dataset"),selected=select.value,approved=approvedIds();select.replaceChildren(option("","请选择已审批数据"));state.datasets.filter((x)=>approved.has(x.id)).forEach((x)=>select.append(option(x.id,`${x.name} · ${x.row_count} 行`)));if([...select.options].some((x)=>x.value===selected))select.value=selected;}

async function createSynthetic(){const button=$("synthetic-create"),context=captureProject();await action(button,"创建中…",async()=>{const rows=Array.from({length:60},(_,index)=>{const x=index+1,y=(index*7)%23;return{x,y,label:x+y>=42?1:0};});const created=await api(`/api/projects/${context.pid}/datasets`,{method:"POST",body:body({name:"合成训练样例 · 60 行",rows,tags:["synthetic","demo"]})},context);state.datasetId=created.id;toast("已创建 60 行 x、y、label 合成样例","success");await refreshProject(context);}).catch(()=>{});}
async function importFile(file){if(!file)return;const button=$("import-open"),context=captureProject();await action(button,"导入中…",async()=>{const form=new FormData();form.append("file",file);const created=await api(`/api/projects/${context.pid}/import`,{method:"POST",body:form},context);state.datasetId=created.id;toast(`已导入 ${created.row_count} 行数据`,"success");await refreshProject(context);}).catch(()=>{});$("import-file").value="";}
async function transformDataset(){const context=captureProject(),asset=requireOwned(state.datasets.find((x)=>x.id===state.datasetId),context,"数据版本");await action($("transform-run"),"处理中…",async()=>{const operations=parseJson("transform-operations","清洗操作");if(!Array.isArray(operations)||!operations.length)throw new Error("清洗操作必须是非空数组");const created=await api(`/api/assets/${asset.id}/transform`,{method:"POST",body:body({operations,name:$("transform-name").value.trim()||null})},context);state.datasetId=created.id;toast("清洗版本已封存","success");await refreshProject(context);}).catch(()=>{});}
async function splitDataset(){const context=captureProject(),asset=requireOwned(state.datasets.find((x)=>x.id===state.datasetId),context,"数据版本");await action($("split-run"),"拆分中…",async()=>{const created=await api(`/api/assets/${asset.id}/split`,{method:"POST",body:body({ratio:Number($("split-ratio").value),seed:Number($("split-seed").value)})},context);state.datasetId=created[0].id;toast(`已生成 ${created.length} 个拆分版本`,"success");await refreshProject(context);}).catch(()=>{});}
async function mergeDatasets(){const context=captureProject();await action($("merge-run"),"合并中…",async()=>{const asset_ids=[...document.querySelectorAll(".merge-check:checked")].map((x)=>x.value);if(asset_ids.length<2)throw new Error("请至少选择两个来源版本");asset_ids.forEach((id)=>requireOwned(state.datasets.find((x)=>x.id===id),context,"合并来源"));const name=$("merge-name").value.trim();if(!name)throw new Error("请输入合并后名称");const created=await api(`/api/projects/${context.pid}/merge`,{method:"POST",body:body({asset_ids,name})},context);state.datasetId=created.id;toast("合并版本已封存","success");await refreshProject(context);}).catch(()=>{});}
async function exportDataset(){let context,asset;try{context=captureProject();asset=requireOwned(state.datasets.find((x)=>x.id===state.datasetId),context,"数据版本");const blob=await api(`/api/assets/${asset.id}/export`,{responseType:"blob"},context);const url=URL.createObjectURL(blob),anchor=node("a");anchor.href=url;anchor.download=`dataset-${asset.id}.json`;document.body.append(anchor);anchor.click();anchor.remove();URL.revokeObjectURL(url);toast("导出已开始","success");}catch(error){if(!(error instanceof StaleContext))toast(detail(error),"error");}}
async function transferDataset(){const context=captureProject(),asset=requireOwned(state.datasets.find((x)=>x.id===state.datasetId),context,"数据版本");await action($("transfer-run"),"申请中…",async()=>{await api(`/api/assets/${asset.id}/transfer`,{method:"POST",body:body({target_project_id:null})},context);toast("转库审批已提交","success");await loadApprovals(context);}).catch(()=>{});}

async function loadApprovals(context=captureProject()){const data=await api(`/api/projects/${context.pid}/approvals`,{},context);requireCurrent(context);state.approvals=data;renderApprovals();renderTrainDatasets();}
function renderApprovals(){const list=$("approval-list");list.replaceChildren();list.classList.remove("empty");const pending=state.approvals.filter((x)=>x.status==="PENDING");if(!pending.length){empty(list,"当前没有待审批事项");return;}pending.forEach((approval)=>{const item=node("article","approval-item"),head=node("header"),title=node("div");title.append(node("strong","",approval.name||`${approval.approval_type} 审批`),node("div","row-main",`${approval.approval_type==="workflow"?"人工节点":"数据转库"} · ${date(approval.created_at)}`));head.append(title,chip(approval.status));item.append(head);if(approval.preview)item.append(node("pre","approval-preview",JSON.stringify(approval.preview,null,2)));const actions=node("div","approval-actions"),comment=node("input");comment.placeholder="审批意见（可选）";const reject=node("button","button danger-ghost compact","驳回"),approve=node("button","button secondary compact","通过");reject.type=approve.type="button";reject.addEventListener("click",()=>invoke(()=>decideApproval(approval,"reject",comment.value,reject)));approve.addEventListener("click",()=>invoke(()=>decideApproval(approval,"approve",comment.value,approve)));actions.append(comment,reject,approve);item.append(actions);list.append(item);});}
async function decideApproval(approval,decision,comment,button){const context=captureProject();requireOwned(approval,context,"审批");await action(button,"提交中…",async()=>{await api(`/api/approvals/${approval.id}/decide`,{method:"POST",body:body({decision,expected_revision:approval.revision,comment})},context);toast(decision==="approve"?"审批已通过":"审批已驳回","success");await Promise.all([loadApprovals(context),loadJobs(context),loadDatasets(context),loadOverview(context)]);}).catch(()=>{});}

async function loadJobs(context=captureProject()){const data=await api(`/api/projects/${context.pid}/jobs`,{},context);requireCurrent(context);const old=new Map(state.jobs.map((x)=>[x.id,x.status]));state.jobs=data;renderJobs($("job-list"),data.filter((x)=>x.payload?.kind!=="workflow"));renderJobs($("workflow-jobs"),data.filter((x)=>x.payload?.kind==="workflow"));const changed=data.length!==old.size||data.some((x)=>old.get(x.id)!==x.status);const unseenModel=data.some((x)=>x.status==="SUCCEEDED"&&x.payload?.kind==="train"&&x.result?.model_id&&!state.models.some((model)=>model.id===x.result.model_id));const newApproval=data.some((x)=>x.status==="WAITING_APPROVAL"&&old.get(x.id)!=="WAITING_APPROVAL");const related=[];if(changed)related.push(loadOverview(context));if(unseenModel)related.push(loadModels(context));if(newApproval)related.push(loadApprovals(context));if(related.length)await Promise.allSettled(related);}
function renderJobs(container,jobs){container.replaceChildren();container.classList.remove("loading","empty");if(!jobs.length){empty(container,"当前没有相关任务");return;}jobs.forEach((job)=>{const panel=node("article","job-panel"),top=node("div","job-top"),copy=node("div");const kind=job.payload?.kind==="workflow"?"工作流运行":`${job.payload?.task==="regression"?"回归":"分类"}训练`;copy.append(node("strong","",kind),node("small","",`${shortId(job.id)} · ${date(job.created_at)}`));top.append(copy,chip(job.status));const track=node("div","progress-track"),bar=node("div","progress-bar");bar.style.width=`${Math.max(0,Math.min(100,Number(job.progress||0)))}%`;track.append(bar);panel.append(top,track);const latest=job.error||job.logs?.at(-1)?.message||(job.status==="WAITING_APPROVAL"?"等待人工审批":"尚无日志");panel.append(node("p","job-log",latest));if(["QUEUED","RUNNING","WAITING_APPROVAL","PAUSED"].includes(job.status)){const actions=node("div","job-actions"),cancel=node("button","button danger-ghost compact","取消任务");cancel.type="button";cancel.addEventListener("click",()=>invoke(()=>cancelJob(job,cancel)));actions.append(cancel);panel.append(actions);}container.append(panel);});}
async function cancelJob(job,button){const context=captureProject();requireOwned(job,context,"任务");await action(button,"取消中…",async()=>{await api(`/api/jobs/${job.id}/cancel`,{method:"POST",body:body({expected_revision:job.revision})},context);toast("任务已取消","success");await loadJobs(context);}).catch(()=>{});}
async function train(){const context=captureProject();await action($("train-run"),"提交中…",async()=>{const assetId=$("train-dataset").value,asset=requireOwned(state.datasets.find((x)=>x.id===assetId),context,"训练数据");if(!approvedIds().has(asset.id))throw new Error("请选择已审批的数据版本");const target=$("train-target").value.trim();if(!target)throw new Error("请输入目标列");const raw=$("train-features").value.trim();const features=raw?raw.split(",").map((x)=>x.trim()).filter(Boolean):null;await api(`/api/assets/${asset.id}/train`,{method:"POST",body:body({target,task:$("train-task").value,features,test_fraction:Number($("train-fraction").value),seed:42})},context);toast("训练任务已进入队列","success");await Promise.all([loadJobs(context),loadOverview(context)]);}).catch(()=>{});}

async function loadModels(context=captureProject()){const data=await api(`/api/projects/${context.pid}/models`,{},context);requireCurrent(context);state.models=data;if(!data.some((x)=>x.id===state.modelId))state.modelId=data[0]?.id||"";renderModels();}
function renderModels(){const list=$("model-list");list.replaceChildren();list.classList.remove("loading","empty");$("model-count").textContent=String(state.models.length);if(!state.models.length){empty(list,"训练成功后，封存模型将在这里出现");renderModelDetail();}else state.models.forEach((model)=>{const button=node("button",`select-item${model.id===state.modelId?" active":""}`);button.type="button";button.dataset.modelId=model.id;button.append(node("strong","",model.name),node("small","",`${model.task==="regression"?"回归":"分类"} · ${model.engine} · ${shortId(model.id)}`));list.append(button);});const deploy=$("deploy-model"),selected=deploy.value;deploy.replaceChildren(option("","请选择模型"));state.models.forEach((model)=>deploy.append(option(model.id,model.name)));if([...deploy.options].some((x)=>x.value===selected))deploy.value=selected;renderModelDetail();}
function renderModelDetail(){const box=$("model-detail"),model=state.models.find((x)=>x.id===state.modelId);box.replaceChildren();box.classList.remove("empty");if(!model){empty(box,"选择模型后查看真实评估指标");return;}const grid=node("div","metric-table");Object.entries(model.metrics||{}).forEach(([key,value])=>{const cell=node("div","metric-cell");cell.append(node("span","",key),node("strong","",typeof value==="number"?value.toFixed(4):JSON.stringify(value)));grid.append(cell);});box.append(grid,node("p","model-notes",`目标列：${model.target} · 特征：${(model.features||[]).join("、")||"—"} · 训练 ${model.train_rows} 行 / 测试 ${model.test_rows} 行 · JSON 参数 ${bytes(model.size_bytes)}`));}
function deploymentIsUsable(deployment,now=Date.now()){const expiry=Date.parse(deployment?.expires_at||"");return deployment?.status==="ACTIVE"&&Number.isFinite(expiry)&&expiry>now;}
async function loadDeployments(context=captureProject()){const data=await api(`/api/projects/${context.pid}/deployments`,{},context);requireCurrent(context);state.deployments=data;const select=$("prediction-deployment"),selected=select.value;select.replaceChildren(option("","请选择活动部署"));data.filter((x)=>deploymentIsUsable(x)).forEach((dep)=>select.append(option(dep.id,`${dep.name} · 到期 ${date(dep.expires_at)}`)));if([...select.options].some((x)=>x.value===selected))select.value=selected;}
async function deploy(){const context=captureProject();await action($("deploy-run"),"发布中…",async()=>{const modelId=$("deploy-model").value,model=requireOwned(state.models.find((x)=>x.id===modelId),context,"模型");const dep=await api(`/api/models/${model.id}/deploy`,{method:"POST",body:body({expires_minutes:Number($("deploy-minutes").value)})},context);toast("限时预测服务已发布","success");await loadDeployments(context);$("prediction-deployment").value=dep.id;}).catch(()=>{});}
async function predict(){const context=captureProject();await action($("predict-run"),"预测中…",async()=>{const id=$("prediction-deployment").value,dep=requireOwned(state.deployments.find((x)=>x.id===id),context,"部署");if(!deploymentIsUsable(dep))throw new Error("模型服务已停用或超过运行时限");const rows=parseJson("prediction-rows","预测数据");if(!Array.isArray(rows)||!rows.length)throw new Error("预测数据必须是非空数组");try{const result=await api(`/api/deployments/${dep.id}/predict`,{method:"POST",body:body({rows})},context);$("prediction-result").textContent=JSON.stringify(result,null,2);toast("预测完成","success");}catch(error){if(error.status===410&&contextIsCurrent(context))await loadDeployments(context);throw error;}await loadDeployments(context);}).catch(()=>{});}
async function stopDeployment(){const context=captureProject();await action($("deployment-stop"),"停止中…",async()=>{const id=$("prediction-deployment").value,dep=requireOwned(state.deployments.find((x)=>x.id===id),context,"部署");await api(`/api/deployments/${id}/stop`,{method:"POST",body:body({expected_revision:dep.revision})},context);toast("部署已停止","success");await loadDeployments(context);}).catch(()=>{});}

async function loadDocuments(context=captureProject()){const data=await api(`/api/projects/${context.pid}/documents`,{},context);requireCurrent(context);state.documents=data;const list=$("document-list");list.replaceChildren();if(!state.documents.length){empty(list,"知识库尚无文档");return;}list.classList.remove("empty");state.documents.slice(0,8).forEach((doc)=>{const item=node("div","micro-item");item.append(node("strong","",doc.name),node("span","",`${doc.chunk_count} 片 · ${bytes(doc.size_bytes)}`));list.append(item);});}
async function addDocument(){const context=captureProject();await action($("document-add"),"写入中…",async()=>{const name=$("document-name").value.trim(),text=$("document-text").value;if(!name||!text.trim())throw new Error("请输入文档名称和内容");await api(`/api/projects/${context.pid}/documents`,{method:"POST",body:body({name,text,strategy:$("document-strategy").value,chunk_size:600,overlap:60,delimiter:"\n\n"})},context);$("document-name").value="";$("document-text").value="";toast("文档已写入知识库","success");await refreshProject(context);}).catch(()=>{});}
async function searchKnowledge(){const context=captureProject();await action($("search-run"),"检索中…",async()=>{const query=$("search-query").value.trim();if(!query)throw new Error("请输入检索问题");const result=await api(`/api/projects/${context.pid}/search`,{method:"POST",body:body({query,top_k:5,threshold:0,mode:$("search-mode").value})},context);renderSearch(result);}).catch(()=>{});}
function renderSearch(result){const list=$("search-results");list.replaceChildren();list.classList.remove("empty");if(!result.matches?.length){empty(list,`没有匹配证据 · ${Number(result.latency_ms||0).toFixed(1)} ms`);return;}result.matches.forEach((match)=>{const card=node("article","evidence-card"),quote=node("blockquote","",`“${match.text}”`),footer=node("footer","",`${match.name} · 片段 #${Number(match.index)+1} · 字符 ${match.start}–${match.end} · 得分 ${Number(match.score).toFixed(3)}`);card.append(quote,footer);list.append(card);});const line=node("div","verification-line",`${result.mode} · 检索 ${result.searched_chunks||0} 个片段 · ${Number(result.latency_ms||0).toFixed(1)} ms`);list.prepend(line);}

function exampleWorkflow(){$("workflow-name").value="证据人工复核";$("workflow-nodes").value=JSON.stringify([{id:"input",type:"input"},{id:"review",type:"human",params:{message:"请复核输入证据"}},{id:"output",type:"output"}],null,2);$("workflow-edges").value=JSON.stringify([{source:"input",target:"review"},{source:"review",target:"output"}],null,2);previewWorkflow();}
function workflowDefinition(){const nodes=parseJson("workflow-nodes","节点定义"),edges=parseJson("workflow-edges","连线定义");if(!Array.isArray(nodes)||!nodes.length||!Array.isArray(edges))throw new Error("节点和连线必须是 JSON 数组");return{nodes,edges};}
function validateWorkflowStructure(definition){const types=new Set(["input","clean","filter","select","derive","train","predict","retrieve","llm","human","output","condition","variable","template","iterate","dataset_output"]),names=definition.nodes.map((x)=>x?.id);if(names.some((x)=>typeof x!=="string"||!x||x.length>80)||new Set(names).size!==names.length)throw new Error("节点标识不能为空或重复");if(definition.nodes.some((x)=>!types.has(x?.type)||x.params!==undefined&&(x.params===null||Array.isArray(x.params)||typeof x.params!=="object")))throw new Error("工作流含未知节点类型或无效参数");const inputs=definition.nodes.filter((x)=>x.type==="input"),outputs=definition.nodes.filter((x)=>x.type==="output");if(inputs.length!==1||!outputs.length)throw new Error("工作流需要一个输入节点和至少一个输出节点");const incoming=new Map(names.map((x)=>[x,[]])),outgoing=new Map(names.map((x)=>[x,[]])),seen=new Set();for(const edge of definition.edges){const key=`${edge?.source}\u0000${edge?.target}`;if(!incoming.has(edge?.source)||!incoming.has(edge?.target)||edge.source===edge.target||seen.has(key))throw new Error("无效或重复连线");seen.add(key);incoming.get(edge.target).push(edge.source);outgoing.get(edge.source).push(edge.target);}if(incoming.get(inputs[0].id).length||outputs.some((x)=>outgoing.get(x.id).length))throw new Error("输入节点不能有前驱，输出节点不能有后继");for(const item of definition.nodes)if(incoming.get(item.id).length>1&&!['output','human'].includes(item.type))throw new Error("此节点只能接收一条输入连线");const degree=new Map(names.map((x)=>[x,incoming.get(x).length])),ready=names.filter((x)=>degree.get(x)===0),order=[];while(ready.length){const id=ready.shift();order.push(id);for(const child of outgoing.get(id)){degree.set(child,degree.get(child)-1);if(degree.get(child)===0)ready.push(child);}}if(order.length!==names.length)throw new Error("工作流包含环");const reached=new Set([inputs[0].id]);for(const id of order)if(reached.has(id))outgoing.get(id).forEach((x)=>reached.add(x));const useful=new Set(outputs.map((x)=>x.id));for(const id of [...order].reverse())if(useful.has(id))incoming.get(id).forEach((x)=>useful.add(x));if(reached.size!==names.length||useful.size!==names.length)throw new Error("存在未连接至输入或输出的节点");return order.map((id)=>definition.nodes.find((x)=>x.id===id));}
function previewWorkflow(){try{const definition=workflowDefinition(),ordered=validateWorkflowStructure(definition),box=$("workflow-graph");box.replaceChildren();box.classList.remove("empty");const wrap=node("div","graph-nodes");ordered.forEach((item,index)=>{const targets=definition.edges.filter((x)=>x.source===item.id).map((x)=>x.target);const row=node("div","graph-node");row.append(node("span","graph-index",String(index+1).padStart(2,"0")),node("strong","",item.id),node("small","",`${item.type}${targets.length?` → ${targets.join("、")}`:""}`));wrap.append(row);});box.append(wrap);toast(`结构校验通过：${ordered.length} 个节点，${definition.edges.length} 条连线`,"success");}catch(error){toast(detail(error),"error");}}
async function loadWorkflows(context=captureProject()){const data=await api(`/api/projects/${context.pid}/workflows`,{},context);requireCurrent(context);state.workflows=data;const select=$("workflow-select"),selected=select.value;select.replaceChildren(option("","请选择工作流"));state.workflows.forEach((flow)=>select.append(option(flow.id,`${flow.name} · v${flow.revision}`)));if([...select.options].some((x)=>x.value===selected))select.value=selected;}
async function saveWorkflow(){const context=captureProject();await action($("workflow-save"),"保存中…",async()=>{const definition=workflowDefinition(),name=$("workflow-name").value.trim();validateWorkflowStructure(definition);if(!name)throw new Error("请输入工作流名称");const created=await api(`/api/projects/${context.pid}/workflows`,{method:"POST",body:body({name,...definition,parent_id:null})},context);toast("工作流版本已保存","success");await refreshProject(context);$("workflow-select").value=created.id;}).catch(()=>{});}
async function runWorkflow(){const context=captureProject();await action($("workflow-run"),"提交中…",async()=>{const id=$("workflow-select").value,flow=requireOwned(state.workflows.find((x)=>x.id===id),context,"工作流");const input=parseJson("workflow-input","运行输入");if(!input||Array.isArray(input)||typeof input!=="object")throw new Error("运行输入必须是 JSON 对象");await api(`/api/workflows/${flow.id}/run`,{method:"POST",body:body({input})},context);toast("工作流已进入任务队列","success");await Promise.all([loadJobs(context),loadOverview(context)]);}).catch(()=>{});}

async function loadConnectors(context=captureIdentity()){const data=await api("/api/connectors",{},context);requireCurrent(context);const list=$("connector-list");list.replaceChildren();list.classList.remove("loading","empty");const entries=Array.isArray(data)?data:Object.entries(data).map(([name,value])=>({name,...(typeof value==="object"?value:{status:value})}));if(!entries.length){empty(list,"服务未报告连接器能力");return;}entries.forEach((entry)=>{const item=node("div","micro-item"),name=entry.name||entry.connector||entry.id||"连接器";let value=entry.status||entry.state;if(!value&&typeof entry.configured==="boolean")value=entry.configured?"已配置":"未配置";item.append(node("strong","",name),node("span","",statusLabel(value)));list.append(item);});}
async function probeOllama(){const context=captureIdentity();await action($("ollama-probe"),"探测中…",async()=>{const result=await api("/api/connectors/ollama/probe",{method:"POST"},context);toast(`Ollama：${result.available||result.status||JSON.stringify(result)}`,result.available===false?"error":"success");await loadConnectors(context);}).catch(()=>{});}
async function loadAudit(context=captureIdentity()){const data=await api("/api/audit?limit=100",{},context);requireCurrent(context);state.audits=data;renderAudit();}
function renderAudit(){const box=$("audit-list");box.replaceChildren();box.classList.remove("loading","empty");if(!state.audits.length){empty(box,"还没有审计事件");return;}const table=node("table"),head=node("thead"),row=node("tr");["序号","时间","操作者","动作","对象","项目"].forEach((x)=>row.append(node("th","",x)));head.append(row);table.append(head);const tbody=node("tbody");state.audits.forEach((audit)=>{const tr=node("tr");[audit.seq,date(audit.at),audit.actor,audit.action,shortId(audit.entity_id),projectName(audit.project_id)].forEach((x)=>tr.append(node("td","",x)));tbody.append(tr);});table.append(tbody);box.append(table);}
async function verifyAudit(){const context=captureIdentity();await action($("audit-verify"),"验证中…",async()=>{const result=await api("/api/audit/verify",{},context),line=$("audit-verification");line.textContent=result.valid?`哈希链有效 · 已验证 ${result.checked} 条 · 链头 ${shortId(result.head_hash)}`:`哈希链无效 · 首个异常序号 ${result.first_invalid_seq}`;line.classList.toggle("valid",Boolean(result.valid));toast(result.valid?"审计哈希链验证通过":"审计哈希链验证失败",result.valid?"success":"error");}).catch(()=>{});}

function bind() {
  document.querySelectorAll(".nav-item").forEach((button)=>button.addEventListener("click",()=>switchTab(button.dataset.tab)));
  document.querySelectorAll("[data-go]").forEach((button)=>button.addEventListener("click",()=>switchTab(button.dataset.go)));
  $("mobile-menu").addEventListener("click",()=>document.querySelector(".sidebar").classList.toggle("open"));
  $("project-select").addEventListener("change",(event)=>invoke(()=>changeProject(event.target.value)));
  $("create-project-open").addEventListener("click",()=>$("project-dialog").showModal()); $("project-form").addEventListener("submit",createProject);
  $("project-dialog-close").addEventListener("click",()=>$("project-dialog").close()); $("project-dialog-cancel").addEventListener("click",()=>$("project-dialog").close());
  $("auth-open").addEventListener("click",()=>{$("auth-token").value=state.token;$("auth-dialog").showModal();});
  $("auth-dialog-close").addEventListener("click",()=>$("auth-dialog").close());
  $("auth-form").addEventListener("submit",(event)=>{event.preventDefault();const token=$("auth-token").value.trim();$("auth-dialog").close();invoke(()=>changeIdentity(token));});
  $("auth-clear").addEventListener("click",()=>{$("auth-token").value="";$("auth-dialog").close();toast("会话令牌已清除");invoke(()=>changeIdentity(""));});
  $("overview-refresh").addEventListener("click",()=>invoke(()=>loadOverview(captureIdentity())));
  $("synthetic-create").addEventListener("click",()=>invoke(createSynthetic)); $("import-open").addEventListener("click",()=>$("import-file").click()); $("import-file").addEventListener("change",(event)=>invoke(()=>importFile(event.target.files[0])));
  $("dataset-list").addEventListener("click",(event)=>{const target=event.target.closest("[data-dataset-id]");if(target)invoke(()=>selectDataset(target.dataset.datasetId));});
  $("transform-run").addEventListener("click",()=>invoke(transformDataset)); $("split-run").addEventListener("click",()=>invoke(splitDataset)); $("merge-run").addEventListener("click",()=>invoke(mergeDatasets)); $("export-run").addEventListener("click",()=>invoke(exportDataset)); $("transfer-run").addEventListener("click",()=>invoke(transferDataset));
  $("models-refresh").addEventListener("click",()=>invoke(()=>refreshProject(captureProject()))); $("train-run").addEventListener("click",()=>invoke(train)); $("model-list").addEventListener("click",(event)=>{const target=event.target.closest("[data-model-id]");if(target)invoke(()=>{const context=captureProject();requireOwned(state.models.find((x)=>x.id===target.dataset.modelId),context,"模型");state.modelId=target.dataset.modelId;renderModels();});}); $("deploy-run").addEventListener("click",()=>invoke(deploy)); $("predict-run").addEventListener("click",()=>invoke(predict)); $("deployment-stop").addEventListener("click",()=>invoke(stopDeployment));
  $("document-add").addEventListener("click",()=>invoke(addDocument)); $("search-run").addEventListener("click",()=>invoke(searchKnowledge)); $("search-query").addEventListener("keydown",(event)=>{if(event.key==="Enter")invoke(searchKnowledge);});
  $("workflow-example").addEventListener("click",exampleWorkflow); $("workflow-preview-run").addEventListener("click",previewWorkflow); $("workflow-save").addEventListener("click",()=>invoke(saveWorkflow)); $("workflow-run").addEventListener("click",()=>invoke(runWorkflow)); $("agents-refresh").addEventListener("click",()=>invoke(()=>refreshProject(captureProject())));
  $("operations-refresh").addEventListener("click",()=>invoke(async()=>{const context=captureIdentity();await Promise.all([loadOverview(context),loadConnectors(context),loadAudit(context)]);})); $("ollama-probe").addEventListener("click",()=>invoke(probeOllama)); $("audit-verify").addEventListener("click",()=>invoke(verifyAudit));
}

async function init() {
  bind(); exampleWorkflow();
  const rememberedProject = state.projectId;
  try {
    await changeIdentity(state.token);
    if (rememberedProject !== state.projectId && state.projects.some(project => project.id === rememberedProject)) {
      await changeProject(rememberedProject);
    }
  }
  catch(error){$("service-state").textContent="连接失败";$("global-status").hidden=false;$("global-status").textContent=detail(error);toast(detail(error),"error");}
  state.pollTimer=window.setInterval(()=>{if(state.projectId&&document.visibilityState==="visible"){const context=captureProject();Promise.allSettled([loadJobs(context),loadDeployments(context)]);}},1500);
}

document.addEventListener("DOMContentLoaded",init);
