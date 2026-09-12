"use strict";

(() => {
  const panel = ARD.panel("operations", "operation-tools", "项目设置、审计与监控", `
    <p class="design-caption">项目设置和告警规则由管理员维护。指标来自服务宿主机；只在点击采样时记录，没有后台定时采样或外部告警通知。</p>
    <form id="op-settings-form">
      <h3>项目设置</h3>
      <p id="op-settings-info" class="verification-line"></p>
      <div class="form-grid">
        <label class="field">项目名称<input id="op-name" required maxlength="100"></label>
        <label class="field">存储配额（字节）<input id="op-quota" type="number" required min="1024" max="10737418240" step="1"></label>
        <label class="field">活动任务上限<input id="op-jobs" type="number" required min="1" max="20" step="1"></label>
        <label class="field">指标保留条数<input id="op-retain-count" type="number" required min="10" max="2000" step="1"></label>
        <label class="field">指标保留小时数<input id="op-retain-hours" type="number" required min="1" max="8760" step="1"></label>
      </div>
      <label class="field">项目说明<textarea id="op-description" rows="2" maxlength="2000"></textarea></label>
      <div class="button-row"><button id="op-settings-save" type="submit" class="button primary">保存项目设置</button><button id="op-settings-reload" type="button" class="button ghost">重新载入设置</button></div>
    </form>
    <hr>
    <form id="op-audit-form">
      <h3>筛选项目审计</h3>
      <div class="form-grid">
        <label class="field">操作者（精确匹配）<input id="op-audit-actor" maxlength="200"></label>
        <label class="field">动作（精确匹配）<input id="op-audit-action" maxlength="200" placeholder="例如 create:dataset"></label>
        <label class="field">开始时间（本地时区）<input id="op-audit-since" type="datetime-local" step="1"></label>
        <label class="field">结束时间（本地时区）<input id="op-audit-until" type="datetime-local" step="1"></label>
      </div>
      <div class="button-row"><button id="op-audit-query" type="submit" class="button secondary">查询审计</button><button id="op-audit-export" type="button" class="button ghost">导出最近 10000 条匹配记录</button></div>
      <p id="op-audit-info" class="verification-line"></p>
      <div id="op-audit-results" class="table-wrap"></div>
      <div class="button-row"><button id="op-audit-prev" type="button" class="button ghost">上一页</button><button id="op-audit-next" type="button" class="button ghost">下一页</button></div>
    </form>
    <hr>
    <section aria-labelledby="op-telemetry-title">
      <h3 id="op-telemetry-title">实际宿主机指标</h3>
      <p id="op-telemetry-policy" class="design-caption"></p>
      <label class="field"><span><input id="op-sample-evaluate" type="checkbox" checked>采样后同时评估启用的告警规则</span></label>
      <div class="button-row"><button id="op-sample" type="button" class="button primary">采样宿主机指标</button><button id="op-telemetry-refresh" type="button" class="button ghost">刷新已保存指标</button><button id="op-telemetry-export" type="button" class="button ghost">导出保留范围内的 CSV</button></div>
      <p id="op-sample-result" class="verification-line" aria-live="polite"></p>
      <div id="op-telemetry-results" class="table-wrap"></div>
      <div class="form-grid"><label class="field">选择已保存采样<select id="op-evaluate-sample"></select></label><button id="op-evaluate" type="button" class="button secondary">评估所选采样</button></div>
    </section>
    <hr>
    <form id="op-rule-form">
      <h3>阈值告警规则</h3>
      <p class="design-caption">每项目最多 50 条规则。修改或停用规则会结束旧告警。手动解决后，须先采样确认指标恢复，随后再次越界才会创建新告警。</p>
      <label class="field">新建或修改规则<select id="op-rule-select"></select></label>
      <div class="form-grid">
        <label class="field">规则名称<input id="op-rule-name" required maxlength="100"></label>
        <label class="field">指标<select id="op-rule-metric"><option value="cpu_percent">宿主机 CPU 使用率（%）</option><option value="memory_percent">宿主机内存使用率（%）</option><option value="disk_percent">数据目录所在磁盘使用率（%）</option><option value="project_storage_percent">项目存储配额使用率（%）</option><option value="active_jobs">项目活动任务数</option></select></label>
        <label class="field">比较条件<select id="op-rule-operator"><option value="gte">大于等于</option><option value="gt">大于</option><option value="lte">小于等于</option><option value="lt">小于</option></select></label>
        <label class="field">阈值<input id="op-rule-threshold" type="number" required min="0" max="1000000" step="any"></label>
      </div>
      <label class="field"><span><input id="op-rule-enabled" type="checkbox" checked>启用规则</span></label>
      <button id="op-rule-save" type="submit" class="button primary">保存告警规则</button>
      <p id="op-rule-info" class="verification-line"></p>
    </form>
    <section aria-labelledby="op-alert-title">
      <h3 id="op-alert-title">告警及处置历史</h3>
      <div class="form-grid"><label class="field">状态<select id="op-alert-filter"><option value="">全部状态</option><option value="OPEN">待处理</option><option value="ACKNOWLEDGED">已确认</option><option value="RESOLVED">已解决</option></select></label><button id="op-alert-refresh" type="button" class="button ghost">刷新告警</button></div>
      <p id="op-alert-info" class="verification-line"></p>
      <div id="op-alert-results" class="micro-list"></div>
    </section>
    <hr>
    <form id="op-backup-form">
      <h3>完整数据备份</h3>
      <p class="design-caption">仅限具有全局项目权限的管理员。备份包含全部项目的数据库及被引用内容对象；运行凭据与私有连接配置须在恢复后重新配置。请先结束全部活动、暂停或等待审批的任务。文件总展开大小上限 512 MiB。</p>
      <label class="field"><span><input id="op-backup-confirm" type="checkbox" required>确认下载全部项目的数据备份</span></label>
      <button id="op-backup" type="submit" class="button secondary">生成并下载完整备份</button>
      <p id="op-backup-result" class="verification-line" aria-live="polite"></p>
    </form>
  `);

  const $ = id => panel.querySelector("#" + id);
  const labels = {cpu_percent: "CPU", memory_percent: "内存", disk_percent: "磁盘", project_storage_percent: "项目存储", active_jobs: "活动任务"};
  const statuses = {OPEN: "待处理", ACKNOWLEDGED: "已确认", RESOLVED: "已解决"};
  const actions = {open: "创建", acknowledge: "确认", resolve: "手动解决", recovered: "指标恢复", rule_changed: "规则修改"};
  const outcomes = {opened: "新建告警", firing: "持续越界", normal: "正常", recovered: "指标恢复并结束告警", metric_unavailable: "指标不可用，跳过", already_evaluated_or_older: "已评估或早于最近评估", resolved_waiting_for_recovery: "已手动解决，等待指标恢复"};
  let settingsRevision = null, rules = [], samples = [], ruleRevision = null;
  let refreshSequence = 0, auditSequence = 0, alertSequence = 0, auditOffset = 0, auditTotal = 0;

  function table(target, headings, rows) {
    target.replaceChildren();
    if (!rows.length) { target.append(ARD.el("p", "empty", "当前没有匹配记录")); return; }
    const tableNode = ARD.el("table"), head = ARD.el("thead"), heading = ARD.el("tr"), body = ARD.el("tbody");
    headings.forEach(label => heading.append(ARD.el("th", "", label)));
    head.append(heading);
    rows.forEach(values => { const row = ARD.el("tr"); values.forEach(value => row.append(ARD.el("td", "", value))); body.append(row); });
    tableNode.append(head, body); target.append(tableNode);
  }
  const date = value => new Date(value).toLocaleString("zh-CN", {hour12: false});
  const metric = (value, key) => value === null || value === undefined ? "不可用" : String(value) + (key.endsWith("_percent") ? "%" : "");
  const base = context => "/api/projects/" + encodeURIComponent(context.pid);
  function rights() {
    const me = ARD.data().me, isAdmin = me?.role === "admin", writable = isAdmin || me?.role === "developer";
    const permission = (id, allowed) => {
      const button = $(id), previouslyForbidden = button.dataset.operationForbidden === "true";
      button.dataset.operationForbidden = String(!allowed);
      if (!allowed) button.disabled = true;
      else if (previouslyForbidden) button.disabled = false;
    };
    ["op-settings-save", "op-rule-save"].forEach(id => permission(id, isAdmin));
    ["op-sample", "op-evaluate"].forEach(id => permission(id, writable));
    permission("op-backup", isAdmin && me?.projects?.includes("*"));
    return {isAdmin, writable};
  }
  function fillSettings(result) {
    const p = result.project;
    settingsRevision = p.revision;
    $("op-name").value = p.name;
    $("op-description").value = p.description || "";
    $("op-quota").value = p.quota_bytes;
    $("op-jobs").value = p.max_jobs;
    $("op-retain-count").value = result.telemetry.retention_count;
    $("op-retain-hours").value = result.telemetry.retention_hours;
  }
  function auditParams() {
    const query = new URLSearchParams();
    ["actor", "action"].forEach(key => { const value = $("op-audit-" + key).value; if (value) query.set(key, value); });
    ["since", "until"].forEach(key => {
      const value = $("op-audit-" + key).value;
      if (value) { const timestamp = new Date(value); if (!Number.isFinite(timestamp.valueOf())) throw new Error("请输入有效筛选时间"); query.set(key, timestamp.toISOString()); }
    });
    return query;
  }
  async function loadAudit(context) {
    const sequence = ++auditSequence, params = auditParams();
    params.set("offset", auditOffset); params.set("limit", "100");
    const result = await ARD.request(base(context) + "/audit?" + params, {}, context);
    if (!ARD.current(context) || sequence !== auditSequence) return;
    auditTotal = result.total;
    table($("op-audit-results"), ["序号", "时间", "操作者", "动作", "详情"], result.items.map(row => [row.seq, date(row.at), row.actor, row.action, JSON.stringify(row.detail)]));
    $("op-audit-info").textContent = `匹配 ${result.total} 条，本页 ${result.items.length} 条；CSV 每次最多导出最近 10000 条匹配记录。`;
    $("op-audit-prev").disabled = auditOffset === 0; $("op-audit-next").disabled = auditOffset + 100 >= auditTotal;
  }
  function renderTelemetry(result) {
    samples = result.items;
    const p = result.policy;
    $("op-telemetry-policy").textContent = `仅手动采样，间隔至少 ${p.minimum_interval_seconds} 秒；CPU 观察窗口约 ${p.cpu_window_seconds} 秒。最多保留 ${p.retention_count} 条且限最近 ${p.retention_hours} 小时，当前 ${result.total} 条，表格显示最近 30 条。超时记录在读取时隐藏，在下一次采样或保存设置时清理。CPU/内存为宿主机整体指标，未采集 GPU。`;
    table($("op-telemetry-results"), ["采样时间", "CPU", "内存", "磁盘", "项目存储", "活动任务", "不可用说明"], samples.slice(0, 30).map(row => [date(row.created_at), ...Object.keys(labels).map(key => metric(row.metrics[key], key)), Object.values(row.unavailable).join("；") || "—"]));
    const selected = $("op-evaluate-sample").value;
    $("op-evaluate-sample").replaceChildren(ARD.option("", "请选择已保存采样"));
    samples.forEach(row => $("op-evaluate-sample").append(ARD.option(row.id, date(row.created_at))));
    $("op-evaluate-sample").value = samples.some(row => row.id === selected) ? selected : (samples[0]?.id || "");
  }
  function fillRule() {
    const rule = rules.find(row => row.id === $("op-rule-select").value);
    ruleRevision = rule?.revision ?? null;
    $("op-rule-name").value = rule?.name || "";
    $("op-rule-metric").value = rule?.metric || "cpu_percent";
    $("op-rule-operator").value = rule?.operator || "gte";
    $("op-rule-threshold").value = rule?.threshold ?? "";
    $("op-rule-enabled").checked = rule?.enabled ?? true;
    $("op-rule-info").textContent = rule ? `正在修改版本 ${rule.revision}；${rule.enabled ? "已启用" : "已停用"}` : `新建规则，当前已有 ${rules.length}/50 条。`;
  }
  function renderRules(items) {
    rules = items;
    const selected = $("op-rule-select").value;
    $("op-rule-select").replaceChildren(ARD.option("", "新建规则"));
    rules.forEach(rule => $("op-rule-select").append(ARD.option(rule.id, `${rule.name} · ${rule.enabled ? "启用" : "停用"} · v${rule.revision}`)));
    if (rules.some(rule => rule.id === selected)) $("op-rule-select").value = selected;
  }
  async function loadAlerts(context) {
    const sequence = ++alertSequence, query = new URLSearchParams({limit: "200"});
    if ($("op-alert-filter").value) query.set("status", $("op-alert-filter").value);
    const result = await ARD.request(base(context) + "/alerts?" + query, {}, context);
    if (!ARD.current(context) || sequence !== alertSequence) return;
    const target = $("op-alert-results"); target.replaceChildren();
    $("op-alert-info").textContent = `匹配 ${result.total} 条，显示最近 ${result.items.length} 条。`;
    if (!result.items.length) target.append(ARD.el("p", "empty", "当前没有告警"));
    result.items.forEach(alert => {
      const item = ARD.el("article", "job-panel");
      item.append(ARD.el("strong", "", `${alert.name} · ${statuses[alert.status]}`), ARD.el("p", "", `${labels[alert.metric]}：${metric(alert.observed_value, alert.metric)}；阈值 ${alert.operator} ${alert.threshold}；采样 ${date(alert.sampled_at)}`));
      const history = ARD.el("ol");
      alert.events.forEach(event => history.append(ARD.el("li", "", `${date(event.at)} · ${event.actor} · ${actions[event.action] || event.action}${event.comment ? " · " + event.comment : ""}`)));
      item.append(history);
      if (alert.status !== "RESOLVED" && ["admin", "developer"].includes(ARD.data().me?.role)) {
        const label = ARD.el("label", "field", "处置说明"), comment = ARD.el("input"); comment.maxLength = 1000; label.append(comment); item.append(label);
        const controls = ARD.el("div", "button-row");
        ["acknowledge", "resolve"].filter(action => action !== "acknowledge" || alert.status === "OPEN").forEach(action => {
          const button = ARD.el("button", "button secondary compact", actions[action]); button.type = "button";
          button.addEventListener("click", () => ARD.run(button, async current => {
            if (current.pid !== context.pid || !ARD.current(context)) throw new Error("项目已切换，请刷新告警");
            await ARD.request(base(current) + "/alerts/" + alert.id + "/actions", {method: "POST", body: {action, expected_revision: alert.revision, comment: comment.value}}, current);
            await loadAlerts(current); await loadAudit(current);
          }));
          controls.append(button);
        });
        item.append(controls);
      }
      target.append(item);
    });
  }
  async function refresh(context) {
    const sequence = ++refreshSequence;
    const [settings, history, ruleList] = await Promise.all([
      ARD.request(base(context) + "/settings", {}, context),
      ARD.request(base(context) + "/telemetry", {}, context),
      ARD.request(base(context) + "/alert-rules", {}, context)
    ]);
    if (!ARD.current(context) || sequence !== refreshSequence) return;
    if (settingsRevision === null) fillSettings(settings);
    $("op-settings-info").textContent = `服务端版本 ${settings.project.revision}；表单版本 ${settingsRevision}；已使用 ${settings.usage.stored_bytes} 字节；活动任务 ${settings.usage.active_jobs} 个（含暂停与等待审批）。`;
    renderTelemetry(history); renderRules(ruleList); rights();
    await Promise.all([loadAudit(context), loadAlerts(context)]);
  }
  function evaluationText(result) {
    return result.evaluation.length ? result.evaluation.map(item => outcomes[item.outcome] || item.outcome).join("；") : "没有启用的规则，或本次未请求评估。";
  }

  $("op-settings-form").addEventListener("submit", event => {
    event.preventDefault(); ARD.run($("op-settings-save"), async context => {
      if (settingsRevision === null) throw new Error("请先载入项目设置");
      const result = await ARD.request(base(context) + "/settings", {method: "PATCH", body: {
        expected_revision: settingsRevision, name: $("op-name").value, description: $("op-description").value,
        quota_bytes: Number($("op-quota").value), max_jobs: Number($("op-jobs").value),
        telemetry_retention_count: Number($("op-retain-count").value), telemetry_retention_hours: Number($("op-retain-hours").value)
      }}, context);
      fillSettings(result); ARD.message("项目设置已保存", "success"); await ARD.refresh(context);
    });
  });
  $("op-settings-reload").addEventListener("click", () => ARD.run($("op-settings-reload"), async context => { fillSettings(await ARD.request(base(context) + "/settings", {}, context)); await refresh(context); }));
  $("op-audit-form").addEventListener("submit", event => { event.preventDefault(); auditOffset = 0; ARD.run($("op-audit-query"), loadAudit); });
  function auditPage(button, change) {
    ARD.run(button, async context => {
      const previous = auditOffset; auditOffset = Math.max(0, auditOffset + change);
      try { await loadAudit(context); } catch (error) { if (ARD.current(context)) auditOffset = previous; throw error; }
    }).then(() => { $("op-audit-prev").disabled = auditOffset === 0; $("op-audit-next").disabled = auditOffset + 100 >= auditTotal; });
  }
  $("op-audit-prev").addEventListener("click", () => auditPage($("op-audit-prev"), -100));
  $("op-audit-next").addEventListener("click", () => auditPage($("op-audit-next"), 100));
  $("op-audit-export").addEventListener("click", () => ARD.run($("op-audit-export"), async context => {
    const params = auditParams(); params.set("limit", "10000");
    ARD.saveBlob(await ARD.request(base(context) + "/audit/export?" + params, {responseType: "blob"}, context), `audit-${context.pid}.csv`);
  }));
  $("op-sample").addEventListener("click", () => ARD.run($("op-sample"), async context => {
    const result = await ARD.request(base(context) + "/telemetry/sample", {method: "POST", body: {evaluate_alerts: $("op-sample-evaluate").checked}}, context);
    $("op-sample-result").textContent = `采样已保存：${date(result.sample.created_at)}；${evaluationText(result)}`; await refresh(context);
  }));
  $("op-telemetry-refresh").addEventListener("click", () => ARD.run($("op-telemetry-refresh"), refresh));
  $("op-telemetry-export").addEventListener("click", () => ARD.run($("op-telemetry-export"), async context => {
    ARD.saveBlob(await ARD.request(base(context) + "/telemetry/export", {responseType: "blob"}, context), `telemetry-${context.pid}.csv`);
  }));
  $("op-evaluate").addEventListener("click", () => ARD.run($("op-evaluate"), async context => {
    const id = $("op-evaluate-sample").value;
    if (!samples.some(sample => sample.id === id && sample.project_id === context.pid)) throw new Error("请选择当前项目的已保存采样");
    const result = await ARD.request(base(context) + "/alerts/evaluate", {method: "POST", body: {sample_id: id}}, context);
    $("op-sample-result").textContent = evaluationText(result); await loadAlerts(context); await loadAudit(context);
  }));
  $("op-rule-select").addEventListener("change", fillRule);
  $("op-rule-form").addEventListener("submit", event => { event.preventDefault(); ARD.run($("op-rule-save"), async context => {
    const id = $("op-rule-select").value;
    if (id && !rules.some(rule => rule.id === id && rule.project_id === context.pid)) throw new Error("所选规则不属于当前项目");
    const body = {name: $("op-rule-name").value, metric: $("op-rule-metric").value, operator: $("op-rule-operator").value, threshold: Number($("op-rule-threshold").value), enabled: $("op-rule-enabled").checked};
    if (id) body.expected_revision = ruleRevision;
    const result = await ARD.request(base(context) + "/alert-rules" + (id ? "/" + id : ""), {method: id ? "PATCH" : "POST", body}, context);
    await refresh(context); $("op-rule-select").value = result.id; fillRule(); ARD.message("告警规则已保存", "success");
  }); });
  $("op-alert-refresh").addEventListener("click", () => ARD.run($("op-alert-refresh"), loadAlerts));
  $("op-alert-filter").addEventListener("change", () => ARD.run($("op-alert-refresh"), loadAlerts));
  $("op-backup-form").addEventListener("submit", event => { event.preventDefault(); ARD.run($("op-backup"), async context => {
    if (!$("op-backup-confirm").checked) throw new Error("请确认备份全部项目");
    const blob = await ARD.request("/api/operations/backup", {method: "POST", responseType: "blob", body: {confirm: "BACKUP_ALL_PROJECTS"}}, context);
    ARD.saveBlob(blob, "ai-rd-backup.zip"); $("op-backup-confirm").checked = false;
    $("op-backup-result").textContent = `已生成 ${blob.size} 字节备份。恢复命令：python scripts/restore_backup.py ai-rd-backup.zip 新空目录。恢复会核对 SHA-256、对象引用及审计链。`;
  }); });

  function reset() {
    ++refreshSequence; ++auditSequence; ++alertSequence;
    settingsRevision = null; ruleRevision = null; rules = []; samples = []; auditOffset = 0; auditTotal = 0;
    panel.querySelectorAll("form").forEach(form => form.reset());
    panel.querySelectorAll("input,textarea").forEach(input => { if (["checkbox", "radio"].includes(input.type)) input.checked = input.defaultChecked; else input.value = ""; });
    ["op-rule-select", "op-evaluate-sample"].forEach(id => $(id).replaceChildren(ARD.option("", "尚未载入")));
    $("op-alert-filter").value = "";
    ["op-settings-info", "op-audit-info", "op-audit-results", "op-telemetry-policy", "op-sample-result", "op-telemetry-results", "op-rule-info", "op-alert-info", "op-alert-results", "op-backup-result"].forEach(id => $(id).replaceChildren());
    $("op-backup-confirm").checked = false;
  }
  ARD.register("operation-tools", {refresh, reset});
  reset();
})();
