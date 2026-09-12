"use strict";

(() => {
  const panel = ARD.panel("agents", "knowledge-tools", "文档导入、版本与引用问答", `
    <p class="muted">导入原始文件并保留来源。字符位置以提取文本为准；PDF扫描图片需要先完成文字识别。</p>
    <div class="form-grid">
      <label class="field">导入文件（最大10 MiB）<input id="knowledge-file" type="file" accept=".txt,.md,.docx,.pdf,.html,.htm,.xlsx"></label>
      <label class="field">文档名称（留空使用文件名）<input id="knowledge-import-name" maxlength="150"></label>
      <label class="field">切片策略<select id="knowledge-import-strategy"><option value="paragraph">按段落</option><option value="heading">按Markdown标题</option><option value="sentence">按句子</option><option value="fixed">固定长度</option><option value="delimiter">自定义分隔符</option></select></label>
      <label class="field">每片字符数<input id="knowledge-import-size" type="number" min="50" max="4000" value="600"></label>
      <label class="field">重叠字符数<input id="knowledge-import-overlap" type="number" min="0" max="1000" value="60"></label>
      <label class="field">自定义分隔符（\\n表示换行）<input id="knowledge-import-delimiter" value="\\n\\n" maxlength="60"></label>
    </div>
    <div class="action-bar"><button id="knowledge-import" data-write type="button" class="button secondary">导入并切片</button></div>
    <div class="divider"></div>
    <div class="form-grid">
      <label class="field">文档与历史版本<select id="knowledge-select"><option value="">请选择文档</option></select></label>
      <label class="field">按名称查找<input id="knowledge-filter" maxlength="200" placeholder="输入名称后点击查找"></label>
    </div>
    <div class="action-bar"><button id="knowledge-find" type="button" class="button ghost">查找文档</button><button id="knowledge-load" type="button" class="button ghost">打开文档</button></div>
    <p id="knowledge-detail" class="muted" aria-live="polite">请选择文档查看原文和片段。</p>
    <div class="form-grid">
      <label class="field">新版本名称<input id="knowledge-edit-name" maxlength="150"></label>
      <label class="field">新版本切片策略<select id="knowledge-edit-strategy"><option value="paragraph">按段落</option><option value="heading">按Markdown标题</option><option value="sentence">按句子</option><option value="fixed">固定长度</option><option value="delimiter">自定义分隔符</option></select></label>
      <label class="field">每片字符数<input id="knowledge-edit-size" type="number" min="50" max="4000" value="600"></label>
      <label class="field">重叠字符数<input id="knowledge-edit-overlap" type="number" min="0" max="1000" value="60"></label>
      <label class="field">自定义分隔符（\\n表示换行）<input id="knowledge-edit-delimiter" value="\\n\\n" maxlength="60"></label>
    </div>
    <label class="field">提取原文 / 新版本内容<textarea id="knowledge-edit-text" rows="7" maxlength="2000000" placeholder="打开文档后显示提取文本"></textarea></label>
    <div class="action-bar">
      <button id="knowledge-version" data-write type="button" class="button secondary">保存为新版本</button>
      <button id="knowledge-export-source" type="button" class="button ghost">下载源文件</button>
      <button id="knowledge-export-text" type="button" class="button ghost">下载提取文本</button>
      <button id="knowledge-export-chunks" type="button" class="button ghost">下载切片与来源</button>
    </div>
    <label class="field"><span><input id="knowledge-archive-confirm" type="checkbox">我确认归档或恢复所选版本；归档后当前版本退出检索，源文件和历史仍保留。</span></label>
    <div class="action-bar"><button id="knowledge-archive" data-write type="button" class="button danger-ghost">归档所选版本</button></div>
    <div class="form-grid"><label class="field">片段起始序号（从0开始）<input id="knowledge-chunk-offset" type="number" min="0" value="0"></label></div>
    <div class="action-bar"><button id="knowledge-chunks-load" type="button" class="button ghost">读取20个片段</button></div>
    <div id="knowledge-chunks" class="evidence-list" aria-live="polite"></div>
    <div id="knowledge-preview" class="result-box" tabindex="0" aria-live="polite">点击片段查看上下文。</div>
    <div class="divider"></div>
    <p id="knowledge-index-status" class="muted" aria-live="polite">语义索引状态将在选择项目后显示。</p>
    <div class="action-bar"><button id="knowledge-index-build" data-write type="button" class="button secondary">构建或更新语义索引</button></div>
    <p class="muted">语义与混合检索需要服务器配置向量模型并建立当前索引。文档版本、归档或模型配置变化后需要更新；未变片段会复用。</p>
    <div class="form-grid">
      <label class="field">依据资料提问<input id="knowledge-question" maxlength="2000" placeholder="例如：压力超过阈值后应怎样处理？"></label>
      <label class="field">检索方式<select id="knowledge-answer-mode"><option value="keyword">关键词</option><option value="semantic">语义</option><option value="hybrid">混合</option></select></label>
      <label class="field">最多引用片段<input id="knowledge-answer-top" type="number" value="5" min="1" max="20"></label>
      <label class="field">最低匹配分数<input id="knowledge-answer-threshold" type="number" value="0.05" min="0" max="1" step="0.01"></label>
    </div>
    <div class="action-bar"><button id="knowledge-answer" type="button" class="button primary">生成带引用的回答</button></div>
    <div id="knowledge-answer-output" class="evidence-list" aria-live="polite">回答需要服务器配置聊天模型；没有匹配证据时不会调用模型。</div>
  `);
  const $ = id => document.getElementById(id);
  let documents = [], loaded = null, selectionEpoch = 0, listingEpoch = 0, chosenId = "";

  function value(id) { return $(id).value; }
  function integer(id, minimum, maximum) {
    const input = value(id);
    const number = Number(input);
    if (!input.trim() || !Number.isInteger(number) || number < minimum || number > maximum) {
      throw new Error(`请输入${minimum}至${maximum}之间的整数。`);
    }
    return number;
  }
  function config(prefix) {
    const chunk_size = integer(`${prefix}-size`, 50, 4000);
    const overlap = integer(`${prefix}-overlap`, 0, 1000);
    if (overlap >= chunk_size) throw new Error("重叠字符数必须小于每片字符数。");
    const delimiter = value(`${prefix}-delimiter`).replaceAll("\\n", "\n");
    if (!delimiter || delimiter.length > 30) throw new Error("分隔符需要1至30个字符。");
    return {strategy: value(`${prefix}-strategy`), chunk_size, overlap, delimiter};
  }
  function activeSelection() {
    if (!loaded || loaded.document.id !== value("knowledge-select")) throw new Error("请先打开所选文档。");
    return loaded.document;
  }
  function same(context, epoch, id) {
    return ARD.current(context) && epoch === selectionEpoch && value("knowledge-select") === id;
  }
  function clearDocument() {
    loaded = null;
    selectionEpoch += 1;
    ["knowledge-edit-name", "knowledge-edit-text"].forEach(id => { $(id).value = ""; });
    $("knowledge-archive-confirm").checked = false;
    $("knowledge-detail").textContent = "请选择文档查看原文和片段。";
    $("knowledge-chunks").replaceChildren();
    $("knowledge-preview").textContent = "点击片段查看上下文。";
    $("knowledge-chunk-offset").value = "0";
    $("knowledge-archive").textContent = "归档所选版本";
  }
  function renderIndex(status) {
    const names = {ready: "当前索引可用", stale: "资料已变化，需要更新索引", not_built: "当前模型尚未构建索引", unavailable: "向量服务未就绪"};
    const identity = status.identity ? ` · ${status.identity.provider} / ${status.identity.model}` : "";
    $("knowledge-index-status").textContent = `${names[status.state] || status.state} · ${status.chunk_count || 0} 个片段${identity}${status.reason ? ` · ${status.reason}` : ""}`;
  }
  async function loadListing(context) {
    const epoch = ++listingEpoch;
    const payload = await ARD.request(`/api/knowledge/projects/${context.pid}/documents?include_archived=true&include_history=true&limit=250&q=${encodeURIComponent(value("knowledge-filter"))}`, {}, context);
    if (!ARD.current(context) || epoch !== listingEpoch) return;
    const previous = chosenId || value("knowledge-select");
    documents = payload.documents;
    const select = $("knowledge-select");
    select.replaceChildren(ARD.option("", "请选择文档"));
    documents.forEach(item => select.append(ARD.option(item.id,
      `${item.name} · v${item.version}${item.archived ? " · 已归档" : ""}${item.is_latest ? "" : " · 历史版本"}`)));
    if (documents.some(item => item.id === previous)) select.value = previous;
    else if (loaded) clearDocument();
    chosenId = "";
    if (payload.total > 250) ARD.message("当前显示前250个版本，请按名称缩小范围。", "info");
  }
  async function preview(context, id, index, epoch) {
    const result = await ARD.request(`/api/knowledge/documents/${id}/chunks/${index}`, {}, context);
    if (!same(context, epoch, id)) return;
    const box = $("knowledge-preview");
    box.replaceChildren(ARD.el("p", "muted", `字符 ${result.chunk.start}–${result.chunk.end}（结束位置不包含）`));
    const text = ARD.el("p", "source-preview");
    text.style.whiteSpace = "pre-wrap";
    text.append(document.createTextNode(result.preview.before), ARD.el("mark", "", result.preview.selected), document.createTextNode(result.preview.after));
    box.append(text, ARD.el("p", "muted", `来源位置：${JSON.stringify(result.locations)}`));
  }
  async function loadChunks(context, id, epoch) {
    const offset = integer("knowledge-chunk-offset", 0, 5000);
    const result = await ARD.request(`/api/knowledge/documents/${id}/chunks?offset=${offset}&limit=20`, {}, context);
    if (!same(context, epoch, id)) return;
    const list = $("knowledge-chunks");
    list.replaceChildren(ARD.el("p", "muted", `共 ${result.total} 个片段，本页显示 ${result.chunks.length} 个。`));
    result.chunks.forEach(chunk => {
      const card = ARD.el("article", "evidence-card");
      const open = ARD.el("button", "text-button", `查看片段 #${chunk.index} 上下文`);
      open.type = "button";
      open.addEventListener("click", () => ARD.run(open, ctx => preview(ctx, id, chunk.index, epoch)));
      card.append(ARD.el("blockquote", "", chunk.text), ARD.el("p", "muted", `字符 ${chunk.start}–${chunk.end}`), open);
      list.append(card);
    });
  }
  async function loadDocument(context) {
    const id = value("knowledge-select");
    if (!id) throw new Error("请选择文档。");
    clearDocument();
    const epoch = selectionEpoch;
    let result;
    try { result = await ARD.request(`/api/knowledge/documents/${id}`, {}, context); }
    catch (error) { if (!same(context, epoch, id)) return; throw error; }
    if (!same(context, epoch, id)) return;
    loaded = result;
    const item = result.document;
    $("knowledge-edit-name").value = item.name;
    $("knowledge-edit-text").value = result.text;
    $("knowledge-edit-strategy").value = item.strategy;
    $("knowledge-edit-size").value = String(item.chunk_size);
    $("knowledge-edit-overlap").value = String(item.overlap);
    $("knowledge-edit-delimiter").value = (item.delimiter || "\n\n").replaceAll("\n", "\\n");
    $("knowledge-detail").textContent = `${item.name} · v${item.version} · ${item.chunk_count} 个片段 · 来源 ${result.source.filename || "手工录入"} · ${item.archived ? "已归档" : item.is_latest ? "当前版本" : "历史版本"} · 源文件SHA-256 ${item.source_sha256 || item.sha256}`;
    $("knowledge-archive").textContent = item.archived ? "恢复所选版本" : "归档所选版本";
    await loadChunks(context, id, epoch);
  }
  function action(id, task) { $(id).addEventListener("click", () => ARD.run($(id), task)); }

  action("knowledge-import", async context => {
    const file = $("knowledge-file").files[0];
    if (!file || !file.size || file.size > 10 * 1024 * 1024) throw new Error("请选择1字节至10 MiB的文档。");
    const form = new FormData();
    form.append("file", file);
    if (value("knowledge-import-name").trim()) form.append("name", value("knowledge-import-name").trim());
    Object.entries(config("knowledge-import")).forEach(([key, input]) => form.append(key, String(input)));
    const created = await ARD.request(`/api/knowledge/projects/${context.pid}/import`, {method: "POST", body: form}, context);
    if (!ARD.current(context)) return;
    $("knowledge-file").value = ""; $("knowledge-import-name").value = ""; $("knowledge-filter").value = "";
    chosenId = created.id;
    await ARD.refresh(context);
    await loadDocument(context);
    ARD.message("源文件与提取片段已保存。", "success");
  });
  $("knowledge-select").addEventListener("change", clearDocument);
  action("knowledge-find", loadListing);
  action("knowledge-load", loadDocument);
  action("knowledge-chunks-load", context => {
    const item = activeSelection();
    return loadChunks(context, item.id, selectionEpoch);
  });
  action("knowledge-version", async context => {
    const item = activeSelection(), epoch = selectionEpoch;
    if (item.archived || !item.is_latest) throw new Error("请打开未归档的当前版本后编辑。");
    const name = value("knowledge-edit-name").trim(), text = value("knowledge-edit-text");
    if (!name || !text.trim()) throw new Error("请输入新版本名称和内容。");
    const result = await ARD.request(`/api/knowledge/documents/${item.id}/versions`, {method: "POST", body: {
      name, text, expected_revision: item.revision, ...config("knowledge-edit")}}, context);
    if (!same(context, epoch, item.id)) return;
    chosenId = result.id; $("knowledge-filter").value = "";
    await ARD.refresh(context);
    await loadDocument(context);
    ARD.message("新版本已保存，请更新语义索引。", "success");
  });
  ["source", "text", "chunks"].forEach(format => action(`knowledge-export-${format}`, async context => {
    const item = activeSelection();
    const result = await ARD.request(`/api/knowledge/documents/${item.id}/export?format=${format}`, {responseType: "blob"}, context);
    if (!ARD.current(context)) return;
    const extension = format === "source" ? (item.source?.format || "txt") : format === "text" ? "txt" : "json";
    ARD.saveBlob(result, `document-${item.id}-${format}.${extension}`);
  }));
  action("knowledge-archive", async context => {
    const item = activeSelection(), epoch = selectionEpoch;
    if (!$("knowledge-archive-confirm").checked) throw new Error("请勾选归档或恢复确认框。");
    await ARD.request(`/api/knowledge/documents/${item.id}/archive`, {method: "POST", body: {
      archived: !item.archived, expected_revision: item.status_revision, confirmed: true}}, context);
    if (!same(context, epoch, item.id)) return;
    chosenId = item.id;
    await ARD.refresh(context);
    await loadDocument(context);
    ARD.message(item.archived ? "文档已恢复。" : "文档已归档，源文件仍可下载。", "success");
  });
  action("knowledge-index-build", async context => {
    const result = await ARD.request(`/api/knowledge/projects/${context.pid}/index/build`, {method: "POST"}, context);
    if (!ARD.current(context)) return;
    renderIndex(result);
    ARD.message(`索引就绪：新计算 ${result.embedded_chunks} 个片段，复用 ${result.reused_chunks} 个片段。`, "success");
  });
  action("knowledge-answer", async context => {
    const query = value("knowledge-question").trim();
    const thresholdText = value("knowledge-answer-threshold"), threshold = Number(thresholdText);
    if (!query) throw new Error("请输入问题。");
    if (!thresholdText.trim() || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) throw new Error("最低分数需为0至1。");
    const output = $("knowledge-answer-output");
    output.textContent = "正在检索证据并准备回答…";
    let result;
    try {
      result = await ARD.request(`/api/knowledge/projects/${context.pid}/answer`, {method: "POST", body: {
        query, mode: value("knowledge-answer-mode"), top_k: integer("knowledge-answer-top", 1, 20), threshold}}, context);
    } catch (error) {
      if (ARD.current(context)) output.textContent = "问答未完成，请根据错误提示检查资料、索引或模型连接。";
      throw error;
    }
    if (!ARD.current(context)) return;
    output.replaceChildren(ARD.el("p", "", result.answer || (result.model_called ? "模型未给出有效引用，请查看原文证据。" : "没有匹配证据，未调用生成模型。")));
    if (result.model) output.append(ARD.el("p", "muted", `生成模型：${result.model}`));
    result.citations.forEach(item => {
      const card = ARD.el("article", "evidence-card");
      card.append(ARD.el("strong", "", `[${item.citation_id}] ${item.name} · v${item.version} · 片段 #${item.index}`),
        ARD.el("blockquote", "", item.text),
        ARD.el("p", "muted", `字符 ${item.start}–${item.end} · 匹配分数 ${item.score.toFixed(3)} · ${JSON.stringify(item.locations)}`));
      output.append(card);
    });
    const limits = ARD.el("ul", "muted");
    result.limitations.forEach(text => limits.append(ARD.el("li", "", text)));
    output.append(limits);
  });

  ARD.register("knowledge-tools", {
    async refresh(context) {
      const results = await Promise.allSettled([loadListing(context),
        ARD.request(`/api/knowledge/projects/${context.pid}/index`, {}, context)]);
      if (!ARD.current(context)) return;
      if (results[0].status === "rejected") throw results[0].reason;
      if (results[1].status === "fulfilled") renderIndex(results[1].value);
      else $("knowledge-index-status").textContent = `索引状态无法读取：${results[1].reason.message}`;
      const write = ["admin", "developer"].includes(ARD.data().me?.role);
      panel.querySelectorAll("[data-write]").forEach(button => { button.disabled = !write; });
    },
    reset() {
      documents = []; chosenId = ""; listingEpoch += 1; clearDocument();
      panel.querySelectorAll("input,textarea").forEach(input => {
        if (input.type === "checkbox") input.checked = false;
        else input.value = "";
      });
      $("knowledge-select").replaceChildren(ARD.option("", "请选择文档"));
      ["knowledge-import", "knowledge-edit"].forEach(prefix => {
        $(`${prefix}-strategy`).value = "paragraph"; $(`${prefix}-size`).value = "600";
        $(`${prefix}-overlap`).value = "60"; $(`${prefix}-delimiter`).value = "\\n\\n";
      });
      $("knowledge-chunk-offset").value = "0";
      $("knowledge-answer-mode").value = "keyword"; $("knowledge-answer-top").value = "5";
      $("knowledge-answer-threshold").value = "0.05";
      $("knowledge-index-status").textContent = "语义索引状态将在选择项目后显示。";
      $("knowledge-answer-output").textContent = "回答需要服务器配置聊天模型；没有匹配证据时不会调用模型。";
    }
  });
})();
