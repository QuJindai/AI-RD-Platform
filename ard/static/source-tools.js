"use strict";
(() => {
  const card = ARD.panel("datasets", "source-tools", "数据库只读快照", '<p class="muted">从管理员配置的预设查询导入数据，生成可复用的数据版本。</p><div class="form-grid"><label>数据源<select id="source-select"></select></label><label>预设查询<select id="source-query"></select></label></div><p id="source-schema" class="muted"></p><label>查询参数（JSON）<textarea id="source-parameters" rows="3">{}</textarea></label><label>数据版本名称（可选）<input id="source-name" maxlength="150"></label><label class="check-row"><input type="checkbox" id="source-force">重新查询并创建新版本</label><div class="button-row"><button type="button" id="source-snapshot" class="button primary">导入数据快照</button></div><pre id="source-result" class="result-box" aria-live="polite"></pre>');
  const $ = id => card.querySelector("#" + id);
  let sources = [];
  function queries() {
    const source = sources.find(item => item.id === $("source-select").value);
    $("source-query").replaceChildren(...(source?.queries || []).map(query => ARD.option(query.id, query.name)));
    parameters();
  }
  function parameters() {
    const source = sources.find(item => item.id === $("source-select").value);
    const query = source?.queries.find(item => item.id === $("source-query").value);
    const fields = query?.parameters || {};
    $("source-parameters").value = JSON.stringify(Object.fromEntries(Object.entries(fields).map(([key, kind]) => [key, kind === "text" ? "" : kind === "boolean" ? false : 0])), null, 2);
    $("source-schema").textContent = query
      ? "参数类型：" + JSON.stringify(fields) + " · 最多 " + query.max_rows + " 行 · 缓存 " + query.cache_ttl_seconds + " 秒"
      : "当前项目尚未配置数据源，请联系管理员添加预设查询。";
    $("source-snapshot").disabled = !query || !["admin", "developer"].includes(ARD.data().me?.role);
    $("source-result").textContent = "";
  }
  $("source-select").addEventListener("change", queries);
  $("source-query").addEventListener("change", parameters);
  $("source-snapshot").addEventListener("click", () => ARD.run($("source-snapshot"), async context => {
    const sourceId = $("source-select").value;
    const params = JSON.parse($("source-parameters").value);
    if (!params || Array.isArray(params) || typeof params !== "object") throw new Error("查询参数须为 JSON 对象");
    const result = await ARD.request("/api/projects/" + context.pid + "/sources/" + encodeURIComponent(sourceId) + "/snapshot", {
      method: "POST", body: {query_id: $("source-query").value, parameters: params,
        name: $("source-name").value.trim() || null, refresh: $("source-force").checked}
    }, context);
    await ARD.refresh(context);
    if (!ARD.current(context)) return;
    $("source-result").textContent = (result.cached ? "已复用有效快照" : "已创建数据快照") + "\n" + JSON.stringify({
      name: result.dataset.name, dataset_id: result.dataset.id,
      rows: result.dataset.row_count, sha256: result.dataset.sha256
    }, null, 2);
    ARD.message(result.cached ? "已复用缓存数据版本。" : "数据已导入，可在数据目录中继续处理。", "success");
  }));
  ARD.register("source-tools", {
    async refresh(context) {
      const previous = $("source-select").value, previousQuery = $("source-query").value;
      const result = await ARD.request("/api/projects/" + context.pid + "/sources", {}, context);
      if (!ARD.current(context)) return;
      sources = result;
      $("source-select").replaceChildren(...sources.map(source => ARD.option(source.id, source.name + " · " + source.driver)));
      if (sources.some(source => source.id === previous)) $("source-select").value = previous;
      queries();
      if (sources.find(source => source.id === $("source-select").value)?.queries.some(query => query.id === previousQuery)) {
        $("source-query").value = previousQuery;
        parameters();
      }
    },
    reset() {
      sources = [];
      $("source-select").replaceChildren();
      $("source-query").replaceChildren();
      $("source-schema").textContent = "";
      $("source-parameters").value = "{}";
      $("source-result").textContent = "";
      $("source-snapshot").disabled = true;
    }
  });
})();
