"use strict";

(() => {
  const host = ARD.panel("datasets", "data-tools", "数据目录、媒体与独立标注", `
    <details open><summary>版本目录与分布比较</summary>
      <div class="form-grid">
        <label class="field">搜索名称、标签、版本标记或 ID<input id="dt-query" maxlength="200"></label>
        <label class="field">精确标签<input id="dt-tag" maxlength="80"></label>
        <label class="field">谱系中的版本 ID（可选）<input id="dt-lineage"></label>
        <label class="check-option"><input id="dt-archived" type="checkbox">显示已归档版本</label>
      </div>
      <button id="dt-search" class="button secondary" type="button">查询目录</button>
      <div id="dt-catalog" class="table-wrap" aria-live="polite"></div>
      <label class="field">操作的数据版本<select id="dt-asset"></select></label>
      <pre id="dt-lineage-info" class="result-box"></pre>
      <div class="form-grid">
        <label class="field">新版本名称<input id="dt-name" maxlength="150"></label>
        <label class="field">标签（逗号分隔）<input id="dt-tags"></label>
        <label class="field">版本标记<input id="dt-version" maxlength="80"></label>
        <label class="field">说明<textarea id="dt-description" rows="2" maxlength="2000"></textarea></label>
      </div>
      <button id="dt-metadata" class="button secondary" type="button">保存元数据为新版本</button>
      <div class="form-grid">
        <label class="field">分类拆分列<select id="dt-category"></select></label>
        <div class="field button-field"><span>按列中不同值生成数据版本</span><button id="dt-category-run" class="button secondary" type="button">按类别拆分</button></div>
      </div>
      <p>手动拆分使用从 0 开始的原始行号，每行必须恰好分配一次。</p>
      <div id="dt-groups" class="form-stack"></div>
      <div class="action-bar"><button id="dt-group-add" class="button ghost" type="button">增加分组</button><button id="dt-manual" class="button secondary" type="button">按行号拆分</button></div>
      <div class="form-grid">
        <label class="field">比较的另一数据版本<select id="dt-compare"></select></label>
        <label class="field">比较列（逗号分隔，最多 20 列）<input id="dt-columns"></label>
      </div>
      <button id="dt-distribution" class="button secondary" type="button">比较实际分布</button>
      <div id="dt-distribution-result" class="table-wrap" aria-live="polite"></div>
      <p>归档保留版本和文件；有任务、模型、工作流、待审批事项或未完成标注引用时会拒绝归档。</p>
      <div class="form-grid"><label class="field">归档或恢复原因<input id="dt-archive-reason" maxlength="1000"></label><label class="field">输入完整版本 ID 以确认<input id="dt-confirm" autocomplete="off"></label></div>
      <div class="action-bar"><button id="dt-references" class="button ghost" type="button">检查引用</button><button id="dt-archive" class="button danger-ghost" type="button">归档所选版本</button><button id="dt-restore" class="button secondary" type="button">恢复所选版本</button></div>
      <pre id="dt-reference-result" class="result-box"></pre>
    </details>
    <details><summary>表格扩展导入与媒体</summary>
      <p>表格支持 CSV、JSON、JSONL、XLSX、YAML、XML、HTML 和 Parquet，单文件最多 20 MiB。XLSX 读取首个可见工作表，公式需先转换为值。</p>
      <div class="form-grid"><label class="field">表格文件<input id="dt-table-file" type="file" accept=".csv,.json,.jsonl,.ndjson,.xlsx,.yaml,.yml,.xml,.html,.htm,.parquet,.zip"></label><div class="field button-field"><span>导入新版本</span><button id="dt-table-import" class="button secondary" type="button">导入表格</button></div></div>
      <p>图片支持 JPG、PNG、BMP、TIF、GIF、WebP；视频支持 MP4、AVI、MKV。图片会完整解码检查并生成预览，视频检查容器和流信息。视频能否播放取决于浏览器编码支持。</p>
      <div class="form-grid"><label class="field">媒体文件（最多 20 MiB）<input id="dt-media-file" type="file" accept=".jpg,.jpeg,.png,.bmp,.tif,.tiff,.gif,.webp,.mp4,.avi,.mkv"></label><label class="field">媒体名称（可选）<input id="dt-media-name" maxlength="150"></label></div>
      <button id="dt-media-import" class="button secondary" type="button">检查并导入媒体</button>
      <label class="field">已导入媒体<select id="dt-media-select"></select></label>
      <div class="action-bar"><button id="dt-media-preview" class="button ghost" type="button">加载预览</button><button id="dt-media-download" class="button ghost" type="button">下载原文件</button></div>
      <pre id="dt-media-info" class="result-box"></pre><div id="dt-media-result"></div>
    </details>
    <details><summary>分配标注任务</summary>
      <p>每个指定账户独立提交完整标签，提交内容随后封存。至少两个标注账户才能计算分歧率；审核者需具备审核权限，且不能审核自己提交的标签。多人流程需要已配置的独立账户。</p>
      <div class="form-grid">
        <label class="field">来源数据版本<select id="dt-annotation-asset"></select></label>
        <label class="field">任务名称<input id="dt-task-name" maxlength="150"></label>
        <label class="field">标注类型<select id="dt-task-type"><option value="row_classification">行分类</option><option value="text_classification">文本分类</option><option value="span">文本片段</option><option value="qa">抽取式问答</option></select></label>
        <label class="field">文本列（文本、片段和问答必填）<input id="dt-text-column" maxlength="200"></label>
        <label class="field">标签（逗号分隔，问答可留空）<input id="dt-task-labels"></label>
        <label class="field">问答共同问题<input id="dt-question" maxlength="1000"></label>
        <label class="field">标注账户（逗号分隔）<input id="dt-annotators"></label>
        <label class="field">行号（可选，留空使用全部，最多 5000 行）<input id="dt-task-indexes"></label>
      </div>
      <p id="dt-available-users"></p><button id="dt-task-create" class="button secondary" type="button">分配任务</button>
    </details>
    <details open><summary>执行、复核与导出标注</summary>
      <label class="field">标注任务<select id="dt-task-select"></select></label>
      <button id="dt-task-load" class="button ghost" type="button">加载或刷新任务（清除未提交草稿）</button>
      <pre id="dt-task-info" class="result-box"></pre>
      <div class="action-bar"><button id="dt-row-prev" class="button ghost" type="button">上一行</button><span id="dt-row-position"></span><button id="dt-row-next" class="button ghost" type="button">下一行</button></div>
      <pre id="dt-row-data" class="result-box"></pre>
      <label class="field">原始文本（可选中文字自动填入位置）<textarea id="dt-row-text" rows="5" readonly></textarea></label>
      <p id="dt-row-question"></p>
      <div class="form-grid">
        <label class="field">分类或片段标签<select id="dt-row-label"></select></label>
        <label class="field">起始位置（Unicode 码点，从 0 开始）<input id="dt-span-start" type="number" min="0"></label>
        <label class="field">结束位置（不包含此位置）<input id="dt-span-end" type="number" min="0"></label>
        <label class="check-option"><input id="dt-unanswerable" type="checkbox">问答：文本中无答案</label>
      </div>
      <div class="action-bar"><button id="dt-span-add" class="button ghost" type="button">添加片段</button><button id="dt-span-clear" class="button ghost" type="button">清空当前行片段</button><button id="dt-row-save" class="button secondary" type="button">保存当前行草稿</button></div>
      <pre id="dt-row-labels" class="result-box"></pre>
      <p id="dt-draft-progress" role="status"></p>
      <div id="dt-row-candidates" class="form-stack"></div>
      <button id="dt-submit" class="button primary" type="button">封存我的完整提交</button>
      <label class="field">审核意见<textarea id="dt-review-comment" rows="2" maxlength="2000"></textarea></label>
      <div class="action-bar"><button id="dt-review-accept" class="button secondary" type="button">通过并封存最终标签</button><button id="dt-review-reject" class="button danger-ghost" type="button">驳回任务</button></div>
      <div class="form-grid"><label class="field">输出数据名称（可选）<input id="dt-export-name" maxlength="150"></label><label class="field">输出标签列<input id="dt-export-column" maxlength="100" value="_label"></label></div>
      <div class="action-bar"><button id="dt-export" class="button secondary" type="button">生成已审核数据版本</button><button id="dt-labelled-download" class="button ghost" type="button">下载已导出标签数据</button></div>
    </details>
  `);
  const $ = id => document.getElementById(id);
  const split = value => value.split(/[,，]/).map(x => x.trim()).filter(Boolean);
  const json = value => JSON.stringify(value, null, 2);
  const clone = value => JSON.parse(JSON.stringify(value));
  let catalogue = [], media = [], tasks = [], task = null, position = 0, currentRow = null;
  let drafts = new Map(), spans = [], previewURL = null;
  let refreshSequence = 0, taskSequence = 0, rowSequence = 0, mediaSequence = 0;
  const writeButtons = ["dt-metadata", "dt-category-run", "dt-manual", "dt-archive", "dt-restore", "dt-table-import", "dt-media-import", "dt-task-create", "dt-export"];
  const taskButtons = ["dt-row-save", "dt-span-add", "dt-span-clear", "dt-submit", "dt-review-accept", "dt-review-reject", "dt-export", "dt-labelled-download"];
  const canWrite = () => ["admin", "developer"].includes(ARD.data().me?.role);
  const canEditLabels = active => Boolean(active && ((active.can_review && ["admin", "reviewer"].includes(ARD.data().me?.role)) || (canWrite() && active.status === "OPEN" && active.own_submission === null && active.annotators.includes(ARD.data().me?.user))));
  function assetPermissions() {
    const asset = catalogue.find(x => x.id === $("dt-asset").value), write = canWrite();
    ["dt-metadata", "dt-category-run", "dt-manual", "dt-archive"].forEach(id => $(id).disabled = !write || !asset || asset.archive.archived);
    $("dt-restore").disabled = !write || !asset || !asset.archive.archived;
  }
  function permissions() {
    writeButtons.forEach(id => $(id).disabled = !canWrite());
    assetPermissions();
    if (task) taskInfo();
    else taskButtons.forEach(id => $(id).disabled = true);
  }
  const still = (context, taskId) => ARD.current(context) && (!taskId || task?.id === taskId);
  const button = (id, fn) => $(id).addEventListener("click", () => {
    let captured;
    ARD.run($(id), context => { captured = context; return fn(context); }).finally(() => {
      if (captured && ARD.current(captured)) permissions();
    });
  });
  function selected() {
    const record = catalogue.find(x => x.id === $("dt-asset").value);
    if (!record) throw new Error("请先在目录中选择数据版本");
    return record;
  }
  function selectedTask() {
    if (!task || task.id !== $("dt-task-select").value) throw new Error("请先加载标注任务");
    return task;
  }
  function fillSelect(id, items, placeholder, label = x => x.name) {
    const select = $(id), old = select.value;
    select.replaceChildren(ARD.option("", placeholder));
    items.forEach(x => select.append(ARD.option(x.id, label(x))));
    if (items.some(x => x.id === old)) select.value = old;
  }
  function indexes(value) {
    const parts = split(value);
    if (parts.some(x => !/^\d+$/.test(x))) throw new Error("行号只能使用非负整数，以逗号分隔");
    return parts.map(Number);
  }
  function makeTable(headers, rows) {
    const table = ARD.el("table"), head = ARD.el("thead"), tr = ARD.el("tr"), body = ARD.el("tbody");
    headers.forEach(text => tr.append(ARD.el("th", "", text))); head.append(tr); table.append(head);
    rows.forEach(values => { const row = ARD.el("tr"); values.forEach(value => {
      const td = ARD.el("td"); td.append(value instanceof Node ? value : ARD.el("span", "", String(value))); row.append(td);
    }); body.append(row); }); table.append(body); return table;
  }
  function chooseAsset() {
    const record = catalogue.find(x => x.id === $("dt-asset").value);
    $("dt-name").value = record?.name || "";
    $("dt-tags").value = (record?.tags || []).join(",");
    $("dt-description").value = record?.description || "";
    $("dt-version").value = record?.version_label || "";
    $("dt-confirm").value = "";
    $("dt-reference-result").textContent = "";
    $("dt-lineage-info").textContent = record ? `完整 ID：${record.id}\n谱系深度：${record.lineage.depth}；标记：${record.version_label || "未设置"}\n父版本：${record.parents.join(", ") || "无"}\n子版本：${record.lineage.children.join(", ") || "无"}\n归档状态：${record.archive.archived ? "已归档" : "活动"}` : "请选择数据版本";
    $("dt-category").replaceChildren(ARD.option("", "请选择分类列"));
    (record?.columns || []).forEach(x => $("dt-category").append(ARD.option(x, x)));
    assetPermissions();
  }
  function renderCatalogue() {
    const prior = $("dt-asset").value;
    fillSelect("dt-asset", catalogue, "请选择目录版本", x => `${x.name} · v${x.lineage.depth} · ${x.id.slice(0, 8)}${x.archive.archived ? " · 已归档" : ""}`);
    if (!$("dt-asset").value) $("dt-asset").value = catalogue.some(x => x.id === ARD.data().datasetId) ? ARD.data().datasetId : catalogue[0]?.id || "";
    fillSelect("dt-compare", catalogue, "请选择比较版本");
    fillSelect("dt-annotation-asset", catalogue.filter(x => !x.archive.archived), "请选择标注来源");
    $("dt-catalog").replaceChildren(makeTable(["版本", "行数", "标签", "谱系", "状态"], catalogue.map(record => {
      const select = ARD.el("button", "text-button", `${record.name} · ${record.id.slice(0, 8)}`);
      select.type = "button"; select.addEventListener("click", () => { $("dt-asset").value = record.id; chooseAsset(); });
      return [select, record.row_count, record.tags.join(", "), `v${record.lineage.depth}${record.version_label ? " · " + record.version_label : ""}`, record.archive.archived ? "已归档" : "活动"];
    })));
    if (!catalogue.length) $("dt-catalog").textContent = "没有符合筛选条件的数据版本";
    if (prior !== $("dt-asset").value || !$("dt-name").value) chooseAsset();
  }
  function clearMedia() {
    mediaSequence++;
    $("dt-media-result").querySelectorAll("video").forEach(player => { player.pause(); player.removeAttribute("src"); player.load(); });
    $("dt-media-result").replaceChildren();
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
  }
  function mediaInfo() {
    clearMedia();
    const record = media.find(x => x.id === $("dt-media-select").value);
    $("dt-media-info").textContent = record ? `${record.name}\n${record.width} × ${record.height} · ${record.encoding}\n${record.media_type === "video" ? "视频容器和流信息已检查；时长 " + record.duration_seconds + " 秒" : "所有图片帧已解码；帧数 " + record.frames}\n文件 SHA-256：${record.sha256}` : "请选择媒体";
  }
  async function refresh(context) {
    const sequence = ++refreshSequence;
    const params = new URLSearchParams({q: $("dt-query").value, tag: $("dt-tag").value, include_archived: String($("dt-archived").checked)});
    if ($("dt-lineage").value.trim()) params.set("lineage_id", $("dt-lineage").value.trim());
    const values = await Promise.all([
      ARD.request(`/api/projects/${context.pid}/data-catalog?${params}`, {}, context),
      ARD.request(`/api/projects/${context.pid}/media`, {}, context),
      ARD.request(`/api/projects/${context.pid}/annotation-tasks`, {}, context),
      ARD.request(`/api/projects/${context.pid}/annotation-annotators`, {}, context)
    ]);
    if (!still(context) || sequence !== refreshSequence) return;
    [catalogue, media, tasks] = values;
    renderCatalogue();
    const oldMedia = $("dt-media-select").value;
    fillSelect("dt-media-select", media, "请选择媒体");
    if (oldMedia !== $("dt-media-select").value) mediaInfo();
    fillSelect("dt-task-select", tasks, "请选择任务", x => `${x.name} · ${x.status} · ${x.submitted_users.length}/${x.annotators.length}`);
    $("dt-available-users").textContent = "可分配账户：" + (values[3].map(x => x.user).join("、") || "无");
    permissions();
  }
  function addGroup() {
    const row = ARD.el("div", "form-grid"), nameLabel = ARD.el("label", "field", "分组名称"), indexLabel = ARD.el("label", "field", "行号（逗号分隔）");
    const name = ARD.el("input"), rows = ARD.el("input"), remove = ARD.el("button", "button ghost", "移除此分组");
    name.className = "dt-group-name"; name.maxLength = 100; rows.className = "dt-group-indexes"; remove.type = "button";
    nameLabel.append(name); indexLabel.append(rows); row.append(nameLabel, indexLabel, remove); remove.addEventListener("click", () => row.remove());
    $("dt-groups").append(row);
  }
  async function changed(context, message) {
    if (!still(context)) return;
    ARD.message(message, "success");
    await ARD.refresh(context);
  }
  button("dt-search", refresh);
  $("dt-asset").addEventListener("change", chooseAsset);
  $("dt-group-add").addEventListener("click", addGroup);
  button("dt-metadata", async context => {
    const asset = selected(), payload = {name: $("dt-name").value.trim(), tags: split($("dt-tags").value), description: $("dt-description").value, version_label: $("dt-version").value, expected_revision: asset.revision};
    await ARD.request(`/api/assets/${asset.id}/metadata-version`, {method: "POST", body: payload}, context);
    await changed(context, "新元数据版本已封存");
  });
  button("dt-category-run", async context => {
    const asset = selected(), column = $("dt-category").value;
    if (!column) throw new Error("请选择分类列");
    const result = await ARD.request(`/api/assets/${asset.id}/partition`, {method: "POST", body: {column}}, context);
    await changed(context, `已按类别生成 ${result.length} 个数据版本`);
  });
  button("dt-manual", async context => {
    const asset = selected(), groups = [...$("dt-groups").children].map(row => ({name: row.querySelector(".dt-group-name").value.trim(), row_indexes: indexes(row.querySelector(".dt-group-indexes").value)}));
    const result = await ARD.request(`/api/assets/${asset.id}/partition`, {method: "POST", body: {groups}}, context);
    await changed(context, `已生成 ${result.length} 个手动拆分版本`);
  });
  button("dt-distribution", async context => {
    const asset = selected(), right = $("dt-compare").value, columns = split($("dt-columns").value);
    if (!right || !columns.length) throw new Error("请选择比较版本和列");
    const result = await ARD.request(`/api/projects/${context.pid}/data-distributions`, {method: "POST", body: {left_id: asset.id, right_id: right, columns}}, context);
    if (!still(context) || $("dt-asset").value !== asset.id || $("dt-compare").value !== right) return;
    const summary = stats => `${stats.rows} 行；缺失 ${stats.missing}；不同值 ${stats.distinct}${stats.numeric ? "；数值均值 " + stats.numeric.mean.toFixed(4) : ""}\n高频值：${stats.top_values.map(x => `${JSON.stringify(x.value)}=${x.count}`).join("，")}`;
    $("dt-distribution-result").replaceChildren(makeTable(["列", "所选版本", "比较版本", "离散总变差（0–1）"], result.columns.map(x => [x.column, summary(x.left), summary(x.right), x.total_variation.toFixed(6)])));
  });
  button("dt-references", async context => {
    const asset = selected(), result = await ARD.request(`/api/assets/${asset.id}/references`, {}, context);
    if (!still(context) || $("dt-asset").value !== asset.id) return;
    $("dt-reference-result").textContent = result.references.length ? json(result.references) : "没有阻止归档的引用";
  });
  for (const [id, archived] of [["dt-archive", true], ["dt-restore", false]]) button(id, async context => {
    const asset = selected(), payload = {archived, confirm: $("dt-confirm").value.trim(), expected_revision: asset.archive.revision, reason: $("dt-archive-reason").value};
    await ARD.request(`/api/assets/${asset.id}/archive`, {method: "POST", body: payload}, context);
    if (!still(context)) return;
    $("dt-confirm").value = "";
    await changed(context, archived ? "数据版本已归档" : "数据版本已恢复");
    if (still(context)) chooseAsset();
  });
  for (const [id, fileId, endpoint, isMedia] of [["dt-table-import", "dt-table-file", "import", false], ["dt-media-import", "dt-media-file", "media-import", true]]) button(id, async context => {
    const file = $(fileId).files[0];
    if (!file) throw new Error("请选择文件");
    if (file.size > 20 * 1024 * 1024) throw new Error("文件超过 20 MiB");
    const form = new FormData(); form.append("file", file); if (isMedia) form.append("name", $("dt-media-name").value);
    await ARD.request(`/api/projects/${context.pid}/${endpoint}`, {method: "POST", body: form}, context);
    if (!still(context)) return;
    $(fileId).value = "";
    await changed(context, isMedia ? "媒体及数据清单已导入" : "表格已导入为新数据版本");
  });
  $("dt-media-select").addEventListener("change", mediaInfo);
  button("dt-media-preview", async context => {
    const item = media.find(x => x.id === $("dt-media-select").value);
    if (!item) throw new Error("请选择媒体");
    clearMedia(); const sequence = mediaSequence;
    const blob = await ARD.request(`/api/media/${item.id}/${item.media_type === "image" ? "preview" : "content"}`, {responseType: "blob"}, context);
    if (!still(context) || sequence !== mediaSequence || $("dt-media-select").value !== item.id) return;
    previewURL = URL.createObjectURL(blob);
    const element = ARD.el(item.media_type === "image" ? "img" : "video");
    element.src = previewURL; element.style.maxWidth = "100%"; element.style.maxHeight = "480px";
    if (item.media_type === "image") element.alt = item.name; else { element.controls = true; element.preload = "metadata"; }
    $("dt-media-result").append(element);
  });
  button("dt-media-download", async context => {
    const item = media.find(x => x.id === $("dt-media-select").value);
    if (!item) throw new Error("请选择媒体");
    const blob = await ARD.request(`/api/media/${item.id}/content`, {responseType: "blob"}, context);
    if (still(context)) ARD.saveBlob(blob, item.filename);
  });
  button("dt-task-create", async context => {
    const rawIndexes = $("dt-task-indexes").value.trim(), body = {dataset_id: $("dt-annotation-asset").value, name: $("dt-task-name").value.trim(), task_type: $("dt-task-type").value,
      text_column: $("dt-text-column").value.trim() || null, question: $("dt-question").value, labels: split($("dt-task-labels").value), annotators: split($("dt-annotators").value), row_indexes: rawIndexes ? indexes(rawIndexes) : null};
    const created = await ARD.request(`/api/projects/${context.pid}/annotation-tasks`, {method: "POST", body}, context);
    if (!still(context)) return;
    await changed(context, "标注任务已分配");
    if (!still(context)) return;
    $("dt-task-select").value = created.id;
    await loadTask(context, created.id);
  });
  function clearTask() {
    taskSequence++; rowSequence++; task = null; currentRow = null; drafts = new Map(); spans = []; position = 0;
    ["dt-task-info", "dt-row-data", "dt-row-labels", "dt-draft-progress", "dt-row-position", "dt-row-question"].forEach(id => $(id).textContent = "");
    ["dt-row-text", "dt-span-start", "dt-span-end", "dt-review-comment"].forEach(id => $(id).value = "");
    $("dt-row-label").replaceChildren(); $("dt-row-candidates").replaceChildren(); $("dt-unanswerable").checked = false;
    taskButtons.forEach(id => $(id).disabled = true);
    ["dt-row-label", "dt-span-start", "dt-span-end", "dt-unanswerable"].forEach(id => $(id).disabled = true);
  }
  function progress() {
    $("dt-draft-progress").textContent = task ? `当前草稿：${drafts.size} / ${task.row_indexes.length} 行。翻页前请保存当前行。` : "";
  }
  function taskInfo() {
    if (!task) return;
    const agreement = task.agreement;
    $("dt-task-info").textContent = `${task.name}\n状态：${task.status}；版本：${task.revision}；提交 ${task.submitted_count}/${task.annotators.length}\n指定账户：${task.annotators.join("、")}\n${agreement ? "分歧行：" + agreement.disagreement_rows.join(", ") + "；两两分歧率：" + (agreement.pairwise_disagreement_rate === null ? "至少两人提交才能计算" : (agreement.pairwise_disagreement_rate * 100).toFixed(2) + "%") : "其他账户的标签在全部提交前不可见"}${task.can_review ? "\n审核模式：一致行已预填；请逐行裁决分歧后保存草稿。" : ""}`;
    const me = ARD.data().me;
    $("dt-submit").disabled = !canWrite() || task.status !== "OPEN" || !task.annotators.includes(me?.user) || task.own_submission !== null;
    $("dt-review-accept").disabled = $("dt-review-reject").disabled = !["admin", "reviewer"].includes(me?.role) || !task.can_review;
    $("dt-export").disabled = !canWrite() || task.status !== "ACCEPTED";
    $("dt-labelled-download").disabled = !task.exported_dataset_id;
    const editable = canEditLabels(task);
    $("dt-row-save").disabled = !editable;
    $("dt-span-add").disabled = $("dt-span-clear").disabled = !editable || task.task_type !== "span";
    ["dt-row-label", "dt-span-start", "dt-span-end", "dt-unanswerable"].forEach(id => $(id).disabled = !editable);
    progress();
  }
  async function loadTask(context, id = $("dt-task-select").value) {
    if (!id) throw new Error("请选择标注任务");
    clearTask(); const sequence = taskSequence;
    const detail = await ARD.request(`/api/annotation-tasks/${id}`, {}, context);
    if (!still(context) || sequence !== taskSequence || $("dt-task-select").value !== id) return;
    task = detail;
    let items = task.own_submission || task.review?.items || [];
    if (task.can_review && task.submissions?.length) {
      const disagreed = new Set(task.agreement.disagreement_rows);
      items = task.submissions[0].items.filter(item => !disagreed.has(item.row_index));
    }
    drafts = new Map(items.map(item => [item.row_index, clone(item.value)]));
    $("dt-row-label").replaceChildren(ARD.option("", "请选择标签"));
    task.labels.forEach(x => $("dt-row-label").append(ARD.option(x, x)));
    taskInfo(); await loadRow(context);
  }
  function paintValue(value) {
    spans = task?.task_type === "span" && Array.isArray(value) ? clone(value) : [];
    $("dt-row-label").value = typeof value === "string" ? value : "";
    $("dt-span-start").value = value?.start ?? ""; $("dt-span-end").value = value?.end ?? "";
    $("dt-unanswerable").checked = value?.unanswerable === true;
    $("dt-row-labels").textContent = value === undefined ? "当前行尚未标注" : json(value);
  }
  async function loadRow(context) {
    const active = selectedTask(), id = active.id, offset = position, sequence = ++rowSequence;
    currentRow = null; $("dt-row-text").value = ""; $("dt-row-data").textContent = "正在读取行…"; $("dt-row-candidates").replaceChildren();
    const result = await ARD.request(`/api/annotation-tasks/${id}/rows?offset=${offset}&limit=1`, {}, context);
    if (!still(context, id) || sequence !== rowSequence || position !== offset) return;
    currentRow = result.rows[0];
    if (!currentRow) return;
    $("dt-row-position").textContent = `第 ${offset + 1}/${result.total} 项；源行号 ${currentRow.row_index}`;
    $("dt-row-data").textContent = json(currentRow.row);
    $("dt-row-text").value = active.text_column ? currentRow.row[active.text_column] || "" : "";
    $("dt-row-question").textContent = active.task_type === "qa" ? "共同问题：" + active.question : "";
    paintValue(drafts.get(currentRow.row_index));
    $("dt-span-add").disabled = $("dt-span-clear").disabled = !canEditLabels(active) || active.task_type !== "span";
    $("dt-row-prev").disabled = offset === 0; $("dt-row-next").disabled = offset + 1 >= result.total;
    if (active.can_review) (active.submissions || []).forEach(submission => {
      const item = submission.items.find(x => x.row_index === currentRow.row_index), row = ARD.el("div", "form-stack");
      row.append(ARD.el("strong", "", submission.annotator), ARD.el("pre", "result-box", json(item.value)));
      const choose = ARD.el("button", "button ghost", "采用此标签作为裁决草稿"); choose.type = "button";
      const rowIndex = currentRow.row_index;
      choose.addEventListener("click", () => { if (task?.id !== id || currentRow?.row_index !== rowIndex) return; drafts.set(rowIndex, clone(item.value)); paintValue(item.value); progress(); });
      row.append(choose); $("dt-row-candidates").append(row);
    });
  }
  button("dt-task-load", loadTask);
  $("dt-task-select").addEventListener("change", clearTask);
  for (const [id, delta] of [["dt-row-prev", -1], ["dt-row-next", 1]]) button(id, async context => {
    const active = selectedTask(); position = Math.max(0, Math.min(active.row_indexes.length - 1, position + delta)); await loadRow(context);
  });
  $("dt-row-text").addEventListener("select", () => {
    const textarea = $("dt-row-text");
    if (textarea.selectionStart === textarea.selectionEnd) return;
    $("dt-span-start").value = Array.from(textarea.value.slice(0, textarea.selectionStart)).length;
    $("dt-span-end").value = Array.from(textarea.value.slice(0, textarea.selectionEnd)).length;
  });
  function offsets() {
    const startValue = $("dt-span-start").value, endValue = $("dt-span-end").value;
    const start = Number(startValue), end = Number(endValue), length = Array.from($("dt-row-text").value).length;
    if (!startValue || !endValue || !Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > length) throw new Error("请选取有效的非空文本范围");
    return {start, end};
  }
  button("dt-span-add", async () => {
    selectedTask(); if (!currentRow) throw new Error("请先加载数据行");
    const label = $("dt-row-label").value; if (!label) throw new Error("请选择片段标签");
    const value = {...offsets(), label};
    if (spans.some(x => x.start < value.end && value.start < x.end)) throw new Error("片段不能重叠");
    spans.push(value); spans.sort((a, b) => a.start - b.start); $("dt-row-labels").textContent = json(spans);
  });
  button("dt-span-clear", async () => { spans = []; $("dt-row-labels").textContent = "[]"; });
  button("dt-row-save", async () => {
    const active = selectedTask(); if (!currentRow) throw new Error("请先加载数据行");
    let value;
    if (["row_classification", "text_classification"].includes(active.task_type)) { value = $("dt-row-label").value; if (!value) throw new Error("请选择分类标签"); }
    else if (active.task_type === "span") value = clone(spans);
    else value = $("dt-unanswerable").checked ? {unanswerable: true} : offsets();
    drafts.set(currentRow.row_index, value); $("dt-row-labels").textContent = json(value); progress();
  });
  function completeItems(active) {
    if (active.row_indexes.some(index => !drafts.has(index))) throw new Error("请完成并保存所有行的标签草稿");
    return active.row_indexes.map(index => ({row_index: index, value: clone(drafts.get(index))}));
  }
  button("dt-submit", async context => {
    const active = selectedTask(), id = active.id, body = {expected_revision: active.revision, items: completeItems(active)};
    await ARD.request(`/api/annotation-tasks/${id}/submit`, {method: "POST", body}, context);
    if (!still(context, id)) return;
    ARD.message("当前账户的完整标签已封存", "success"); await loadTask(context, id);
  });
  for (const [id, decision] of [["dt-review-accept", "accept"], ["dt-review-reject", "reject"]]) button(id, async context => {
    const active = selectedTask(), taskId = active.id, body = {expected_revision: active.revision, decision, comment: $("dt-review-comment").value};
    if (decision === "accept") body.items = completeItems(active);
    await ARD.request(`/api/annotation-tasks/${taskId}/review`, {method: "POST", body}, context);
    if (!still(context, taskId)) return;
    ARD.message(decision === "accept" ? "最终标签已通过审核并封存" : "任务已驳回", "success"); await loadTask(context, taskId);
  });
  button("dt-export", async context => {
    const active = selectedTask(), taskId = active.id, body = {expected_revision: active.revision, name: $("dt-export-name").value, label_column: $("dt-export-column").value};
    await ARD.request(`/api/annotation-tasks/${taskId}/export`, {method: "POST", body}, context);
    if (!still(context, taskId)) return;
    await changed(context, "已生成带审核标签的不可变数据版本");
    if (still(context, taskId)) await loadTask(context, taskId);
  });
  button("dt-labelled-download", async context => {
    const active = selectedTask(), id = active.exported_dataset_id;
    if (!id) throw new Error("请先生成已审核的数据版本");
    const blob = await ARD.request(`/api/assets/${id}/export`, {responseType: "blob"}, context);
    if (still(context)) ARD.saveBlob(blob, `labelled-${id}.json`);
  });
  function reset() {
    host.querySelectorAll("button").forEach(item => item.disabled = false);
    writeButtons.forEach(id => $(id).disabled = true);
    refreshSequence++; clearTask(); clearMedia(); catalogue = []; media = []; tasks = [];
    host.querySelectorAll("input,textarea").forEach(input => { if (input.type === "checkbox") input.checked = false; else input.value = ""; });
    ["dt-asset", "dt-compare", "dt-category", "dt-media-select", "dt-annotation-asset", "dt-task-select"].forEach(id => $(id).replaceChildren(ARD.option("", "请选择")));
    $("dt-task-type").value = "row_classification"; $("dt-export-column").value = "_label";
    ["dt-catalog", "dt-lineage-info", "dt-distribution-result", "dt-reference-result", "dt-media-info", "dt-available-users"].forEach(id => $(id).replaceChildren());
    $("dt-groups").replaceChildren(); addGroup(); addGroup();
  }
  reset();
  ARD.register("data-tools", {refresh, reset});
})();
