"use strict";

(() => {
  const panel = ARD.panel("models", "model-tools", "模型版本、评估与服务", `
    <div class="form-stack">
      <h3>导入与封存版本</h3>
      <p class="design-caption">支持平台 JSON 模型包。导入模型的训练来源未知，评估记录会保留这一说明。</p>
      <div class="form-grid">
        <label class="field">模型版本<select id="mf-model"><option value="">请选择模型</option></select></label>
        <label class="field">新版本名称<input id="mf-version-name" maxlength="150" placeholder="填写封存版本名称"></label>
        <label class="field">平台模型包（JSON，最多 8 MiB）<input id="mf-package" type="file" accept=".json,application/json"></label>
        <label class="field"><span>导入版本关系</span><span><input id="mf-import-child" type="checkbox"> 作为所选模型的新版本</span></label>
      </div>
      <div class="action-bar">
        <button id="mf-import" class="button secondary" type="button">导入模型包</button>
        <button id="mf-export" class="button ghost" type="button">下载模型包</button>
        <button id="mf-version" class="button secondary" type="button">封存命名新版本</button>
      </div>
      <p id="mf-version-info" class="model-notes" aria-live="polite"></p>
      <div class="divider"></div>
      <h3>可复用实验基线</h3>
      <p class="design-caption">基线固定数据版本、特征与划分配置。运行基线仍须先完成数据转库审批。</p>
      <div class="form-grid">
        <label class="field">已有基线<select id="mf-baseline"><option value="">新建基线</option></select></label>
        <label class="field">基线名称<input id="mf-baseline-name" maxlength="150" placeholder="例如：回归基线"></label>
        <label class="field">数据版本<select id="mf-baseline-dataset"><option value="">请选择数据</option></select></label>
        <label class="field">目标列<input id="mf-target" maxlength="200" placeholder="例如 y"></label>
        <label class="field">任务类型<select id="mf-task"><option value="classification">分类</option><option value="regression">回归</option></select></label>
        <label class="field">特征列（逗号分隔，留空使用其他列）<input id="mf-features" placeholder="例如 x"></label>
        <label class="field">测试比例<input id="mf-fraction" type="number" min="0.1" max="0.5" step="0.05" value="0.25"></label>
        <label class="field">随机种子<input id="mf-seed" type="number" min="0" max="4294967295" step="1" value="42"></label>
      </div>
      <div class="action-bar">
        <button id="mf-baseline-save" class="button secondary" type="button">保存新基线</button>
        <button id="mf-baseline-version" class="button secondary" type="button">保存基线新版本</button>
        <button id="mf-baseline-run" class="button primary" type="button">按已保存基线训练</button>
        <button id="mf-baseline-history" class="button ghost" type="button">读取运行记录</button>
      </div>
      <pre id="mf-baseline-result" class="result-box" tabindex="0" aria-live="polite"></pre>
      <div class="divider"></div>
      <h3>数据集评估与模型对比</h3>
      <p class="design-caption">使用封存参数预测。已知训练数据会按原划分取测试行，并排除可识别的训练特征重叠；对比使用共同可评估的行。</p>
      <div class="form-grid">
        <label class="field">评估数据版本<select id="mf-evaluation-dataset"><option value="">请选择数据</option></select></label>
        <label class="field">评估或对比名称（可留空）<input id="mf-evaluation-name" maxlength="150"></label>
        <label class="field">对比模型（按住 Ctrl 或 Command 选择多个）<select id="mf-comparison-models" multiple size="4" aria-describedby="mf-compare-help"></select></label>
      </div>
      <p id="mf-compare-help" class="design-caption">选择 2 至 10 个任务类型和目标列相同的模型。</p>
      <div class="action-bar">
        <button id="mf-evaluate" class="button primary" type="button">评估所选模型版本</button>
        <button id="mf-compare" class="button secondary" type="button">运行模型对比</button>
      </div>
      <div id="mf-evaluation-result" class="result-box" tabindex="0" aria-live="polite"></div>
      <div class="form-grid">
        <label class="field">已保存评估<select id="mf-evaluation"><option value="">请选择评估</option></select></label>
        <div class="field button-field"><span>查看与下载</span><div class="action-bar">
          <button id="mf-evaluation-view" class="button ghost" type="button">查看评估与前 100 行</button>
          <button id="mf-report" class="button ghost" type="button">下载 HTML 报告</button>
        </div></div>
      </div>
      <div class="divider"></div>
      <h3>部署实测统计</h3>
      <button id="mf-stats-refresh" class="button ghost" type="button">刷新实测统计</button>
      <div id="mf-stats" class="table-wrap" aria-live="polite"></div>
      <div class="divider"></div>
      <h3>已配置的模型服务</h3>
      <p class="design-caption">由服务器环境配置 Ollama 或 OpenAI 兼容服务。聊天和向量文本将发送到已配置服务。</p>
      <p id="mf-service-config" class="model-notes"></p>
      <button id="mf-service-probe" class="button ghost" type="button">探测服务与模型目录</button>
      <pre id="mf-service-models" class="result-box" tabindex="0" aria-live="polite"></pre>
      <label class="field">试用文本（向量试用按行分隔，最多 32 行）<textarea id="mf-service-text" rows="4" maxlength="200000" placeholder="输入要发送的文本"></textarea></label>
      <div class="action-bar">
        <button id="mf-service-chat" class="button primary" type="button">使用已配置聊天模型</button>
        <button id="mf-service-embed" class="button secondary" type="button">使用已配置向量模型</button>
      </div>
      <pre id="mf-service-result" class="result-box" tabindex="0" aria-live="polite"></pre>
    </div>
  `);
  const $ = id => panel.querySelector("#" + id);
  let baselines = [], evaluations = [];
  const pending = new Map();
  let refreshEpoch = 0, modelEpoch = 0, baselineEpoch = 0, evaluationEpoch = 0, serviceEpoch = 0;
  const writeButtons = ["mf-import", "mf-version", "mf-baseline-save", "mf-baseline-version", "mf-baseline-run", "mf-evaluate", "mf-compare", "mf-service-probe", "mf-service-chat", "mf-service-embed"];
  const readModel = () => owned(ARD.data().models, $("mf-model").value, "模型");
  function owned(items, id, noun) {
    const item = items.find(item => item.id === id);
    if (!item || item.project_id !== ARD.context().pid) throw new Error("请选择当前项目的" + noun);
    return item;
  }
  function fill(id, items, placeholder) {
    const select = $(id), previous = select.value;
    select.replaceChildren(ARD.option("", placeholder));
    items.forEach(item => select.append(ARD.option(item.id, item.name)));
    if (items.some(item => item.id === previous)) select.value = previous;
  }
  function renderVersions() {
    const model = ARD.data().models.find(item => item.id === $("mf-model").value);
    if (!model) { $("mf-version-info").textContent = "选择模型后可导出、封存新版本或评估。"; return; }
    const root = model.version_root_id || model.id;
    const family = ARD.data().models.filter(item => (item.version_root_id || item.id) === root);
    $("mf-version-info").textContent = `${model.name} · 版本 ${model.version_number || 1} · 同一版本族 ${family.length} 个版本 · 目标列 ${model.target} · 特征 ${(model.features || []).join("、")} · ${model.dataset_id ? "关联已保存训练数据" : "训练来源未知"}`;
  }
  function table(headers, rows) {
    const element = ARD.el("table", "data-table"), head = ARD.el("thead"), tr = ARD.el("tr"), body = ARD.el("tbody");
    headers.forEach(label => tr.append(ARD.el("th", "", label)));
    head.append(tr);
    rows.forEach(values => { const row = ARD.el("tr"); values.forEach(value => row.append(ARD.el("td", "", value))); body.append(row); });
    element.append(head, body);
    return element;
  }
  function renderEvaluation(record, rows = null) {
    const box = $("mf-evaluation-result");
    box.replaceChildren(ARD.el("p", "", `${record.name} · 实际评估 ${record.row_count} 行`));
    if (record.entries) {
      box.append(table(["模型", "实际指标"], record.entries.map(item => [item.model_name, JSON.stringify(item.metrics)])));
    } else {
      box.append(ARD.el("pre", "", JSON.stringify(record.metrics, null, 2)));
      const scope = {selected_dataset: "所选数据", original_holdout: "原始测试集", common_dataset_rows: "各模型共同可评估的行"};
      const independence = {unknown_training_provenance: "训练来源未知", recorded_split: "使用记录的训练与测试划分", training_feature_overlap_removed: "已排除训练特征重叠", source_feature_overlap_removed: "已排除训练来源特征重叠"};
      box.append(ARD.el("p", "", `评估范围：${scope[record.scope] || record.scope} · 数据独立性检查：${independence[record.independence] || record.independence}`));
    }
    (record.notes || []).forEach(note => box.append(ARD.el("p", "model-notes", note)));
    if (rows) box.append(table(["源行号", "真实目标", "模型预测"], rows.map(row => [row.source_row + 1, String(row.actual), String(row.prediction)])));
  }
  function renderStats(stats) {
    const box = $("mf-stats");
    const milliseconds = value => value === null ? "尚无请求" : value.toFixed(3) + " ms";
    box.replaceChildren(ARD.el("p", "model-notes", `已记录预测 ${stats.requests} 次，失败 ${stats.failures} 次，当前活动部署 ${stats.active} 个。平均耗时 ${milliseconds(stats.mean_latency_ms)}。`));
    const status = {ACTIVE: "活动", STOPPED: "已停止", EXPIRED: "已过期"};
    if (stats.entries.length) box.append(table(["部署", "状态", "请求", "失败", "平均耗时"], stats.entries.map(item => [item.name, status[item.status] || item.status, item.requests, item.failures, milliseconds(item.mean_latency_ms)])));
    (stats.notes || []).forEach(note => box.append(ARD.el("p", "design-caption", note)));
  }
  function configBody() {
    const features = $("mf-features").value.split(",").map(value => value.trim()).filter(Boolean);
    return {name: $("mf-baseline-name").value.trim(), dataset_id: $("mf-baseline-dataset").value,
      config: {target: $("mf-target").value.trim(), task: $("mf-task").value, features: features.length ? features : null,
        test_fraction: Number($("mf-fraction").value), seed: Number($("mf-seed").value)}};
  }
  function on(id, task) {
    $(id).addEventListener("click", () => {
      if (pending.has(id)) return;
      const token = Symbol(id);
      pending.set(id, token);
      ARD.run($(id), task).finally(() => { if (pending.get(id) === token) pending.delete(id); });
    });
  }
  function guard(context, epoch, currentEpoch) { return ARD.current(context) && epoch === currentEpoch; }

  on("mf-import", async context => {
    const file = $("mf-package").files[0];
    if (!file || file.size > 8 * 1024 * 1024) throw new Error("请选择最多 8 MiB 的平台 JSON 模型包");
    const epoch = modelEpoch, parent = $("mf-import-child").checked ? readModel().id : null;
    const text = await file.text();
    if (!guard(context, epoch, modelEpoch) || file !== $("mf-package").files[0]) return;
    let value;
    try { value = JSON.parse(text); } catch { throw new Error("模型包不是有效 JSON"); }
    const model = await ARD.request(`/api/projects/${context.pid}/models/import`, {method: "POST", body: {package: value, parent_model_id: parent}}, context);
    if (!guard(context, epoch, modelEpoch)) return;
    $("mf-package").value = "";
    ARD.message("模型已验证并封存", "success");
    await ARD.refresh(context);
    if (guard(context, epoch, modelEpoch)) { $("mf-model").value = model.id; renderVersions(); }
  });
  on("mf-export", async context => {
    const model = readModel(), epoch = modelEpoch;
    const blob = await ARD.request(`/api/models/${model.id}/package`, {responseType: "blob"}, context);
    if (guard(context, epoch, modelEpoch)) ARD.saveBlob(blob, `model-package-${model.id}.json`);
  });
  on("mf-version", async context => {
    const model = readModel(), epoch = modelEpoch;
    const child = await ARD.request(`/api/models/${model.id}/versions`, {method: "POST", body: {name: $("mf-version-name").value.trim()}}, context);
    if (!guard(context, epoch, modelEpoch)) return;
    $("mf-version-name").value = "";
    await ARD.refresh(context);
    if (guard(context, epoch, modelEpoch)) { $("mf-model").value = child.id; renderVersions(); }
  });
  for (const [id, child] of [["mf-baseline-save", false], ["mf-baseline-version", true]]) {
    on(id, async context => {
      const epoch = baselineEpoch, body = configBody();
      owned(ARD.data().datasets, body.dataset_id, "数据版本");
      if (child) body.parent_id = owned(baselines, $("mf-baseline").value, "基线").id;
      const saved = await ARD.request(`/api/projects/${context.pid}/model-baselines`, {method: "POST", body}, context);
      if (!guard(context, epoch, baselineEpoch)) return;
      await ARD.refresh(context);
      if (guard(context, epoch, baselineEpoch)) { $("mf-baseline").value = saved.id; $("mf-baseline-result").textContent = `已封存基线：${saved.name}（版本 ${saved.version_number}）`; }
    });
  }
  on("mf-baseline-run", async context => {
    const selected = owned(baselines, $("mf-baseline").value, "基线"), epoch = baselineEpoch;
    const job = await ARD.request(`/api/model-baselines/${selected.id}/run`, {method: "POST"}, context);
    if (!guard(context, epoch, baselineEpoch)) return;
    $("mf-baseline-result").textContent = `已按封存基线提交训练。任务 ${job.id}；进度与日志见上方任务队列。`;
    await ARD.refresh(context);
  });
  on("mf-baseline-history", async context => {
    const selected = owned(baselines, $("mf-baseline").value, "基线"), epoch = baselineEpoch;
    const jobs = await ARD.request(`/api/model-baselines/${selected.id}/runs`, {}, context);
    if (guard(context, epoch, baselineEpoch)) $("mf-baseline-result").textContent = jobs.length ? JSON.stringify(jobs.map(job => ({任务: job.id, 状态: job.status, 结果: job.result, 错误: job.error})), null, 2) : "此基线尚无运行记录。";
  });
  on("mf-evaluate", async context => {
    const model = readModel(), epoch = evaluationEpoch;
    const data = owned(ARD.data().datasets, $("mf-evaluation-dataset").value, "评估数据版本");
    const saved = await ARD.request(`/api/models/${model.id}/evaluate`, {method: "POST", body: {dataset_id: data.id, name: $("mf-evaluation-name").value.trim() || null}}, context);
    if (!guard(context, epoch, evaluationEpoch)) return;
    renderEvaluation(saved);
    await ARD.refresh(context);
    if (guard(context, epoch, evaluationEpoch)) $("mf-evaluation").value = saved.id;
  });
  on("mf-compare", async context => {
    const epoch = evaluationEpoch, data = owned(ARD.data().datasets, $("mf-evaluation-dataset").value, "评估数据版本");
    const ids = [...$("mf-comparison-models").selectedOptions].map(option => owned(ARD.data().models, option.value, "模型").id);
    if (ids.length < 2 || ids.length > 10) throw new Error("请选择 2 至 10 个对比模型");
    const saved = await ARD.request(`/api/projects/${context.pid}/model-comparisons`, {method: "POST", body: {dataset_id: data.id, model_ids: ids, name: $("mf-evaluation-name").value.trim() || null}}, context);
    if (!guard(context, epoch, evaluationEpoch)) return;
    renderEvaluation(saved);
    await ARD.refresh(context);
  });
  on("mf-evaluation-view", async context => {
    const selected = owned(evaluations, $("mf-evaluation").value, "评估记录"), epoch = evaluationEpoch;
    const rows = await ARD.request(`/api/model-evaluations/${selected.id}/rows?limit=100`, {}, context);
    if (guard(context, epoch, evaluationEpoch)) renderEvaluation(selected, rows.rows);
  });
  on("mf-report", async context => {
    const selected = owned(evaluations, $("mf-evaluation").value, "评估记录"), epoch = evaluationEpoch;
    const blob = await ARD.request(`/api/model-evaluations/${selected.id}/report`, {responseType: "blob"}, context);
    if (guard(context, epoch, evaluationEpoch)) ARD.saveBlob(blob, `evaluation-${selected.id}.html`);
  });
  on("mf-stats-refresh", async context => {
    const stats = await ARD.request(`/api/projects/${context.pid}/deployment-statistics`, {}, context);
    if (ARD.current(context)) renderStats(stats);
  });
  on("mf-service-probe", async context => {
    const epoch = ++serviceEpoch;
    const result = await ARD.request("/api/model-services/probe", {method: "POST"}, context);
    if (guard(context, epoch, serviceEpoch)) $("mf-service-models").textContent = `本次探测成功（${result.provider}）。\n${result.models.length ? result.models.join("\n") : "服务返回空模型目录。"}`;
  });
  on("mf-service-chat", async context => {
    const epoch = ++serviceEpoch;
    const result = await ARD.request("/api/model-services/chat", {method: "POST", body: {prompt: $("mf-service-text").value}}, context);
    if (guard(context, epoch, serviceEpoch)) $("mf-service-result").textContent = `${result.model}\n${result.answer}\n生成用量：${result.eval_count ?? "服务未提供"}`;
  });
  on("mf-service-embed", async context => {
    const epoch = ++serviceEpoch, texts = $("mf-service-text").value.split("\n").map(value => value.trim()).filter(Boolean);
    if (!texts.length || texts.length > 32) throw new Error("请输入 1 至 32 行向量试用文本");
    const result = await ARD.request("/api/model-services/embeddings", {method: "POST", body: {texts}}, context);
    if (guard(context, epoch, serviceEpoch)) $("mf-service-result").textContent = `${result.identity.provider} / ${result.identity.model} · ${result.count} 条向量 · ${result.dimensions} 维\n${JSON.stringify(result.embeddings, null, 2)}`;
  });

  $("mf-model").addEventListener("change", () => { modelEpoch++; evaluationEpoch++; $("mf-evaluation-result").replaceChildren(); renderVersions(); });
  $("mf-package").addEventListener("change", () => { modelEpoch++; });
  $("mf-import-child").addEventListener("change", () => { modelEpoch++; });
  $("mf-baseline").addEventListener("change", () => {
    baselineEpoch++;
    $("mf-baseline-result").textContent = "";
    const selected = baselines.find(item => item.id === $("mf-baseline").value);
    if (!selected) return;
    $("mf-baseline-name").value = selected.name;
    $("mf-baseline-dataset").value = selected.dataset_id;
    $("mf-target").value = selected.config.target;
    $("mf-task").value = selected.config.task;
    $("mf-features").value = (selected.config.features || []).join(",");
    $("mf-fraction").value = String(selected.config.test_fraction);
    $("mf-seed").value = String(selected.config.seed);
  });
  for (const id of ["mf-evaluation", "mf-evaluation-dataset", "mf-comparison-models"]) {
    $(id).addEventListener("change", () => { evaluationEpoch++; $("mf-evaluation-result").replaceChildren(); });
  }
  $("mf-service-text").addEventListener("input", () => { serviceEpoch++; $("mf-service-result").textContent = ""; });
  for (const id of ["mf-baseline-name", "mf-baseline-dataset", "mf-target", "mf-task", "mf-features", "mf-fraction", "mf-seed"]) {
    $(id).addEventListener("input", () => { baselineEpoch++; $("mf-baseline-result").textContent = ""; });
  }

  function reset() {
    refreshEpoch++; modelEpoch++; baselineEpoch++; evaluationEpoch++; serviceEpoch++;
    baselines = []; evaluations = [];
    pending.clear();
    panel.querySelectorAll("button").forEach(button => { button.disabled = false; });
    panel.querySelectorAll("input,textarea").forEach(input => { if (input.type === "checkbox") input.checked = false; else input.value = ""; });
    for (const id of ["mf-model", "mf-baseline", "mf-baseline-dataset", "mf-evaluation-dataset", "mf-evaluation", "mf-comparison-models"]) $(id).replaceChildren();
    for (const id of ["mf-version-info", "mf-baseline-result", "mf-evaluation-result", "mf-stats", "mf-service-config", "mf-service-models", "mf-service-result"]) $(id).replaceChildren();
    $("mf-fraction").value = "0.25"; $("mf-seed").value = "42"; $("mf-task").value = "classification";
    writeButtons.forEach(id => { $(id).disabled = true; });
  }
  async function refresh(context) {
    const epoch = ++refreshEpoch;
    const outcomes = await Promise.allSettled([
      ARD.request(`/api/projects/${context.pid}/model-baselines`, {}, context),
      ARD.request(`/api/projects/${context.pid}/model-evaluations`, {}, context),
      ARD.request(`/api/projects/${context.pid}/deployment-statistics`, {}, context),
      ARD.request("/api/model-services/configuration", {}, context)
    ]);
    if (!guard(context, epoch, refreshEpoch)) return;
    const [savedBaselines, savedEvaluations, stats, configuration] = outcomes.map(result => result.status === "fulfilled" ? result.value : null);
    baselines = savedBaselines || []; evaluations = savedEvaluations || [];
    const data = ARD.data();
    fill("mf-model", data.models, "请选择模型");
    if (!$("mf-model").value && data.models.some(model => model.id === data.modelId)) $("mf-model").value = data.modelId;
    fill("mf-baseline", baselines, "新建基线");
    fill("mf-baseline-dataset", data.datasets, "请选择数据");
    fill("mf-evaluation-dataset", data.datasets, "请选择数据");
    fill("mf-evaluation", evaluations, "请选择评估");
    const selected = new Set([...$("mf-comparison-models").selectedOptions].map(option => option.value));
    $("mf-comparison-models").replaceChildren();
    data.models.forEach(model => { const option = ARD.option(model.id, model.name); option.selected = selected.has(model.id); $("mf-comparison-models").append(option); });
    renderVersions();
    if (stats) renderStats(stats); else $("mf-stats").textContent = "部署统计读取失败，请刷新重试。";
    $("mf-service-config").textContent = configuration ? `提供方 ${configuration.provider} · 端点${configuration.configured ? "已配置" : "未配置"} · 聊天模型 ${configuration.chat_model || "未配置"} · 向量模型 ${configuration.embedding_model || "未配置"} · 密钥${configuration.api_key_configured ? "已配置（隐藏）" : "未配置"}。配置由服务器管理员管理，尚未自动探测。` : "模型服务配置不可用，请联系服务器管理员。平台 JSON 模型仍可使用。";
    const canWrite = ["admin", "developer"].includes(data.me?.role);
    writeButtons.forEach(id => { $(id).disabled = !canWrite || pending.has(id); });
    const error = outcomes.slice(0, 3).find(result => result.status === "rejected");
    if (error) throw error.reason;
  }
  reset();
  panel.hidden = true;
  ARD.register("model-tools", {refresh, reset});
})();
