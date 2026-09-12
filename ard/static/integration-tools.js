"use strict";

(() => {
  const packagePanel = ARD.panel("models", "integration-packages", "工具与依赖包", `
    <p class="hint">保存不可变版本、发现依赖并导出构建上下文。上传内容不会在平台主机执行。</p>
    <div class="form-grid">
      <label>包名称<input id="ip-name" maxlength="100" placeholder="demo_tool；也可从元数据读取"></label>
      <label>版本<input id="ip-version" maxlength="100" placeholder="1.0.0；也可从元数据读取"></label>
      <label>类别<select id="ip-category"><option value="tool">工具包</option><option value="dependency">依赖包</option></select></label>
      <label>父版本<select id="ip-parent"><option value="">首个版本</option></select></label>
      <label>包文件<input id="ip-file" type="file" accept=".whl,.zip,.tar.gz,.tgz,.py,.js,.ts,.sh,.r,.jl,.c,.cpp,.h,.go,.rs,.json,.toml,.yaml,.yml,.md,.txt,.sql"></label>
    </div>
    <div class="button-row"><button id="ip-upload" class="button primary" type="button">保存新版本</button><button id="ip-refresh" class="button" type="button">刷新清单</button></div>
    <label>选择包版本<select id="ip-select"><option value="">请选择</option></select></label>
    <div class="button-row"><button id="ip-manifest" class="button" type="button">查看清单</button><button id="ip-download" class="button" type="button">下载原包</button><button id="ip-requirements" class="button" type="button">下载依赖发现报告</button><button id="ip-archive" class="button" type="button">归档／恢复</button></div>
    <p id="ip-summary" class="hint"></p><pre id="ip-detail" class="result-box" aria-live="polite"></pre>
    <label>构建上下文中的包（可多选）<select id="ip-export-packages" multiple size="5"></select></label>
    <button id="ip-export" class="button" type="button">导出 Docker 构建上下文</button>
    <p class="hint">导出包含 Dockerfile、原包与清单；wheel 离线安装，其余源码或归档仅复制。平台不会自动构建或拉取镜像。</p>
  `);
  const mcpPanel = ARD.panel("agents", "integration-mcp", "MCP 工具连接", `
    <p class="hint">由管理员配置固定 HTTP 端点与工具白名单。工具调用可能影响远端服务，请先核对工具说明与参数。</p>
    <label>连接<select id="im-select"><option value="">新建连接</option></select></label>
    <p id="im-summary" class="hint" aria-live="polite"></p>
    <div id="im-admin-fields">
      <div class="form-grid">
        <label>连接名称<input id="im-name" maxlength="100" placeholder="团队工具服务"></label>
        <label>完整端点<input id="im-url" type="url" maxlength="2000" placeholder="https://tools.example.org/mcp" autocomplete="off"></label>
        <label>Bearer 凭据<input id="im-token" type="password" maxlength="4096" autocomplete="new-password"></label>
        <label>允许调用的工具名（每行一个）<textarea id="im-allow" rows="3" placeholder="search_documents"></textarea></label>
        <label>总超时（秒）<input id="im-timeout" type="number" min="2" max="20" value="10"></label>
        <label><input id="im-private" type="checkbox">允许访问内网／回环地址</label>
        <label><input id="im-enabled" type="checkbox" checked>启用连接</label>
      </div>
      <p class="hint">更新连接时需重新输入完整端点和凭据；凭据留空会清除原凭据。查询参数、重定向、云元数据地址均不支持。</p>
      <button id="im-save" class="button primary" type="button">保存管理员配置</button>
    </div>
    <div class="button-row"><button id="im-probe" class="button" type="button">初始化并发现工具</button><button id="im-refresh" class="button" type="button">刷新连接</button></div>
    <label>本次调用的允许工具<select id="im-tool"><option value="">请先发现工具</option></select></label>
    <pre id="im-schema" class="result-box"></pre>
    <label>参数 JSON<textarea id="im-arguments" rows="4">{}</textarea></label>
    <label><input id="im-confirm" type="checkbox">我已核对工具与参数，并明确发起本次远端调用</label>
    <button id="im-call" class="button primary" type="button">调用所选工具</button>
    <pre id="im-result" class="result-box" aria-live="polite"></pre>
  `);
  const runtimePanel = ARD.panel("operations", "integration-runtime", "受管 Docker 运行实例", `
    <p id="ir-availability" class="hint" aria-live="polite">正在检查本地 Docker…</p>
    <p class="hint">管理员可启动可信的本地 Python 3 镜像；固定无网络、只读根文件系统、无主机挂载、无特权，并限制内存、CPU、进程数和时间。包引用用于追溯，内容需预先构建进所选镜像。</p>
    <div class="form-grid">
      <label>本地镜像完整 ID<input id="ir-image" placeholder="sha256:…" maxlength="71"></label>
      <label>程序与参数 JSON 数组<textarea id="ir-argv" rows="3">["python", "-c", "print('ready')"]</textarea></label>
      <label>内存 MiB<input id="ir-memory" type="number" min="64" max="1024" value="256"></label>
      <label>CPU 核数上限<input id="ir-cpus" type="number" min="0.1" max="2" step="0.1" value="0.5"></label>
      <label>最长运行秒数<input id="ir-ttl" type="number" min="10" max="3600" value="300"></label>
      <label>已构建入镜像的包引用<select id="ir-packages" multiple size="4"></select></label>
    </div>
    <label><input id="ir-confirm" type="checkbox">我已审核镜像、程序与资源限制，确认启动</label>
    <div class="button-row"><button id="ir-create" class="button primary" type="button">启动受管实例</button><button id="ir-refresh" class="button" type="button">检查可用性与清单</button></div>
    <label>当前项目的受管实例<select id="ir-select"><option value="">请选择</option></select></label>
    <div class="button-row"><button id="ir-inspect" class="button" type="button">检查真实状态</button><button id="ir-stop" class="button" type="button">停止</button><button id="ir-remove" class="button" type="button">停止并移除容器</button></div>
    <pre id="ir-result" class="result-box" aria-live="polite"></pre>
  `);

  const $ = id => document.getElementById(id);
  let packages = [], configs = [], discovered = [], runtimeItems = [], available = false;
  let packageEpoch = 0, mcpEpoch = 0, runtimeEpoch = 0;
  const selected = (id, items, title) => {
    const record = items.find(item => item.id === $(id).value);
    if (!record) throw new Error("请选择" + title);
    return record;
  };
  const chosen = id => [...$(id).selectedOptions].map(option => option.value);
  const json = id => {
    try { return JSON.parse($(id).value); } catch (_) { throw new Error("请输入有效 JSON"); }
  };
  function fill(id, items, title, label) {
    const previous = $(id).value;
    $(id).replaceChildren(ARD.option("", title));
    items.forEach(item => $(id).append(ARD.option(item.id, label(item))));
    if (items.some(item => item.id === previous)) $(id).value = previous;
  }
  function multi(id, items) {
    const previous = new Set(chosen(id));
    $(id).replaceChildren();
    items.forEach(item => {
      const option = ARD.option(item.id, item.name + " · " + item.version);
      option.selected = previous.has(item.id);
      $(id).append(option);
    });
  }
  const packageLabel = item => `${item.name} · ${item.version} · ${item.category === "tool" ? "工具" : "依赖"}${item.archived ? " · 已归档" : ""}`;
  function roleControls() {
    const role = ARD.data().me?.role;
    const admin = role === "admin", writer = admin || role === "developer";
    $("ip-upload").disabled = !writer;
    $("ip-archive").disabled = !writer;
    $("im-admin-fields").hidden = !admin;
    $("im-save").disabled = !admin;
    $("im-probe").disabled = !writer;
    $("im-call").disabled = !writer;
    ["ir-create", "ir-inspect", "ir-stop", "ir-remove"].forEach(id => { $(id).disabled = !admin || !available; });
  }
  async function refreshPackages(context) {
    const epoch = ++packageEpoch;
    const result = await ARD.request(`/api/projects/${context.pid}/packages`, {}, context);
    if (!ARD.current(context) || epoch !== packageEpoch) return;
    packages = result;
    fill("ip-select", packages, "请选择包版本", packageLabel);
    fill("ip-parent", packages, "首个版本", packageLabel);
    multi("ip-export-packages", packages.filter(item => !item.archived));
    $("ip-summary").textContent = `${packages.length} 个不可变版本；${packages.filter(item => item.archived).length} 个已归档`;
    roleControls();
  }
  function clearMcpDiscovery() {
    mcpEpoch += 1;
    discovered = [];
    $("im-tool").replaceChildren(ARD.option("", "请先发现工具"));
    $("im-schema").textContent = "";
    $("im-result").textContent = "";
    $("im-arguments").value = "{}";
    $("im-confirm").checked = false;
  }
  function selectMcp() {
    clearMcpDiscovery();
    const item = configs.find(item => item.id === $("im-select").value);
    $("im-name").value = item?.name || "";
    $("im-url").value = "";
    $("im-token").value = "";
    $("im-allow").value = (item?.allowed_tools || []).join("\n");
    $("im-timeout").value = String(item?.timeout_seconds || 10);
    $("im-private").checked = item?.allow_private || false;
    $("im-enabled").checked = item?.enabled ?? true;
    $("im-summary").textContent = item ? `${item.endpoint_origin} · ${item.enabled ? "启用" : "禁用"} · ${item.has_credentials ? "已配置凭据" : "无凭据"} · ${item.last_probe ? "最近探测" + (item.last_probe.available ? "成功" : "失败") + "：" + item.last_probe.at : "尚未探测"}` : "管理员可创建连接；配置保存后再检查服务可用性。";
  }
  async function refreshMcp(context) {
    const epoch = ++mcpEpoch;
    const items = await ARD.request(`/api/projects/${context.pid}/mcp`, {}, context);
    if (!ARD.current(context) || epoch !== mcpEpoch) return;
    configs = items;
    fill("im-select", items, "新建连接", item => item.name + (item.enabled ? "" : " · 已禁用"));
    // Remote definitions belong to a single checked configuration revision.
    selectMcp();
    roleControls();
  }
  async function refreshRuntime(context) {
    const epoch = ++runtimeEpoch;
    const [status, items, packageItems] = await Promise.all([
      ARD.request("/api/runtime/status", {}, context),
      ARD.request(`/api/projects/${context.pid}/runtimes`, {}, context),
      ARD.request(`/api/projects/${context.pid}/packages`, {}, context)
    ]);
    if (!ARD.current(context) || epoch !== runtimeEpoch) return;
    available = status.available;
    runtimeItems = items;
    $("ir-availability").textContent = available ? `Docker 可用 · 服务端 ${status.server_version} · 网络 none` : status.reason;
    fill("ir-select", items, "请选择受管实例", item => item.id.slice(0, 8) + " · " + item.status + " · 截止 " + item.expires_at);
    multi("ir-packages", packageItems.filter(item => !item.archived));
    roleControls();
  }
  function on(id, task) { $(id).addEventListener("click", () => ARD.run($(id), task)); }

  on("ip-refresh", refreshPackages);
  on("ip-upload", async context => {
    const file = $("ip-file").files[0];
    if (!file) throw new Error("请选择包文件");
    if (file.size > 20 * 1024 * 1024) throw new Error("包文件不能超过 20MiB");
    const data = new FormData();
    data.append("file", file);
    data.append("name", $("ip-name").value.trim());
    data.append("version", $("ip-version").value.trim());
    data.append("category", $("ip-category").value);
    data.append("parent_id", $("ip-parent").value);
    const created = await ARD.request(`/api/projects/${context.pid}/packages`, {method: "POST", body: data}, context);
    if (!ARD.current(context)) return;
    $("ip-file").value = "";
    await refreshPackages(context);
    if (!ARD.current(context)) return;
    $("ip-select").value = created.id;
    ARD.message("不可变包版本已保存", "success");
  });
  on("ip-manifest", async context => {
    const item = selected("ip-select", packages, "包版本"), epoch = packageEpoch;
    const result = await ARD.request(`/api/packages/${item.id}/manifest`, {}, context);
    if (ARD.current(context) && epoch === packageEpoch && $("ip-select").value === item.id) $("ip-detail").textContent = JSON.stringify(result, null, 2);
  });
  $("ip-select").addEventListener("change", () => { packageEpoch += 1; $("ip-detail").textContent = ""; });
  for (const [id, route, filename] of [["ip-download", "download", null], ["ip-requirements", "requirements", "requirements-discovery.jsonl"]]) {
    on(id, async context => {
      const item = selected("ip-select", packages, "包版本");
      const blob = await ARD.request(`/api/packages/${item.id}/${route}`, {responseType: "blob"}, context);
      if (ARD.current(context)) ARD.saveBlob(blob, filename || item.filename);
    });
  }
  on("ip-archive", async context => {
    const item = selected("ip-select", packages, "包版本");
    if (!window.confirm(`${item.archived ? "恢复" : "归档"} ${item.name} ${item.version}？原包内容会保留。`)) return;
    await ARD.request(`/api/packages/${item.id}/archive`, {method: "POST", body: {expected_revision: item.state_revision, archived: !item.archived, confirmed: true}}, context);
    await refreshPackages(context);
  });
  on("ip-export", async context => {
    const package_ids = chosen("ip-export-packages");
    if (!package_ids.length) throw new Error("请选择构建上下文中的包");
    const blob = await ARD.request(`/api/projects/${context.pid}/packages/docker-context`, {method: "POST", body: {package_ids}, responseType: "blob"}, context);
    if (ARD.current(context)) ARD.saveBlob(blob, "ard-docker-context.zip");
  });

  $("im-select").addEventListener("change", selectMcp);
  on("im-refresh", refreshMcp);
  on("im-save", async context => {
    const current = configs.find(item => item.id === $("im-select").value);
    const body = {name: $("im-name").value.trim(), url: $("im-url").value.trim(), token: $("im-token").value,
      allowed_tools: $("im-allow").value.split(/\s+/).filter(Boolean), allow_private: $("im-private").checked,
      timeout_seconds: Number($("im-timeout").value), enabled: $("im-enabled").checked};
    if (current) body.expected_revision = current.revision;
    try {
      const result = await ARD.request(current ? `/api/mcp/${current.id}` : `/api/projects/${context.pid}/mcp`, {method: current ? "PUT" : "POST", body}, context);
      if (!ARD.current(context)) return;
      await refreshMcp(context);
      if (!ARD.current(context)) return;
      $("im-select").value = result.id;
      selectMcp();
      ARD.message("MCP 配置已保存", "success");
    } finally { if (ARD.current(context)) $("im-token").value = ""; }
  });
  on("im-probe", async context => {
    const item = selected("im-select", configs, "MCP 连接"), epoch = mcpEpoch;
    $("im-result").textContent = "正在初始化并发现工具…";
    try {
      const result = await ARD.request(`/api/mcp/${item.id}/probe`, {method: "POST"}, context);
      if (!ARD.current(context) || epoch !== mcpEpoch || $("im-select").value !== item.id) return;
      discovered = result.tools;
      fill("im-tool", result.tools.filter(tool => tool.allowed).map(tool => ({...tool, id: tool.name})), "请选择允许工具", tool => tool.name);
      $("im-result").textContent = JSON.stringify(result, null, 2);
      $("im-summary").textContent = `探测成功 · ${result.protocol_version} · ${result.checked_at}`;
    } catch (error) {
      if (ARD.current(context) && epoch === mcpEpoch) { $("im-result").textContent = "连接探测失败；请核对配置与远端服务。"; discovered = []; $("im-tool").replaceChildren(ARD.option("", "请重新发现工具")); }
      throw error;
    }
  });
  $("im-tool").addEventListener("change", () => {
    const tool = discovered.find(tool => tool.name === $("im-tool").value);
    $("im-schema").textContent = tool ? tool.description + "\n" + JSON.stringify(tool.inputSchema, null, 2) : "";
    $("im-confirm").checked = false;
  });
  $("im-arguments").addEventListener("input", () => { $("im-confirm").checked = false; });
  on("im-call", async context => {
    const item = selected("im-select", configs, "MCP 连接"), epoch = mcpEpoch;
    const tool = discovered.find(tool => tool.name === $("im-tool").value && tool.allowed);
    if (!tool) throw new Error("请先发现并选择允许工具");
    if (!$("im-confirm").checked) throw new Error("请核对并确认本次工具调用");
    const args = json("im-arguments");
    if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("工具参数需为 JSON 对象");
    $("im-confirm").checked = false;
    $("im-result").textContent = "工具调用中…";
    try {
      const result = await ARD.request(`/api/mcp/${item.id}/call`, {method: "POST", body: {tool: tool.name, arguments: args, confirmed: true}}, context);
      if (ARD.current(context) && epoch === mcpEpoch && $("im-select").value === item.id) $("im-result").textContent = JSON.stringify({tool: tool.name, ...result}, null, 2);
    } catch (error) {
      if (ARD.current(context) && epoch === mcpEpoch) $("im-result").textContent = "调用失败或结果未知。平台没有自动重试；请核对远端状态后再决定是否再次调用。";
      throw error;
    }
  });

  on("ir-refresh", refreshRuntime);
  on("ir-create", async context => {
    if (!available) throw new Error("Docker 当前不可用");
    if (!$("ir-confirm").checked) throw new Error("请先审核并确认启动配置");
    const body = {image: $("ir-image").value.trim(), argv: json("ir-argv"), package_ids: chosen("ir-packages"),
      memory_mib: Number($("ir-memory").value), cpus: Number($("ir-cpus").value), ttl_seconds: Number($("ir-ttl").value), confirmed: true};
    $("ir-confirm").checked = false;
    const result = await ARD.request(`/api/projects/${context.pid}/runtimes`, {method: "POST", body}, context);
    if (!ARD.current(context)) return;
    await refreshRuntime(context);
    if (!ARD.current(context)) return;
    $("ir-select").value = result.id;
    $("ir-result").textContent = JSON.stringify(result, null, 2);
  });
  $("ir-select").addEventListener("change", () => {
    runtimeEpoch += 1;
    const item = runtimeItems.find(item => item.id === $("ir-select").value);
    $("ir-result").textContent = item ? "上次记录（点击检查真实状态更新）：\n" + JSON.stringify(item, null, 2) : "";
  });
  for (const [id, action] of [["ir-inspect", "refresh"], ["ir-stop", "stop"], ["ir-remove", "remove"]]) {
    on(id, async context => {
      const item = selected("ir-select", runtimeItems, "受管实例");
      if (action !== "refresh" && !window.confirm(action === "remove" ? "停止并移除此受管容器？运行记录会保留。" : "停止此受管容器？")) return;
      const result = await ARD.request(`/api/runtimes/${item.id}/action`, {method: "POST", body: {action, expected_revision: item.revision, confirmed: true}}, context);
      if (!ARD.current(context)) return;
      await refreshRuntime(context);
      if (ARD.current(context) && $("ir-select").value === item.id) $("ir-result").textContent = JSON.stringify(result, null, 2);
    });
  }
  runtimePanel.querySelectorAll("input,textarea,select").forEach(element => {
    if (element.id !== "ir-confirm" && element.id !== "ir-select") element.addEventListener("input", () => { $("ir-confirm").checked = false; });
  });

  function emptyPanel(panel) {
    panel.querySelectorAll("input,textarea").forEach(element => { if (element.type === "checkbox") element.checked = false; else element.value = ""; });
    panel.querySelectorAll("select").forEach(element => element.replaceChildren(ARD.option("", "请选择")));
    panel.querySelectorAll("pre").forEach(element => { element.textContent = ""; });
  }
  ARD.register("integration-packages", {refresh: refreshPackages, reset() {
    packageEpoch += 1; packages = []; emptyPanel(packagePanel); $("ip-summary").textContent = "";
    $("ip-category").replaceChildren(ARD.option("tool", "工具包"), ARD.option("dependency", "依赖包"));
  }});
  ARD.register("integration-mcp", {refresh: refreshMcp, reset() {
    configs = []; emptyPanel(mcpPanel); clearMcpDiscovery(); $("im-summary").textContent = "";
    $("im-timeout").value = "10"; $("im-enabled").checked = true;
  }});
  ARD.register("integration-runtime", {refresh: refreshRuntime, reset() {
    runtimeEpoch += 1; runtimeItems = []; available = false; emptyPanel(runtimePanel);
    $("ir-availability").textContent = "请选择项目后检查 Docker 可用性";
    $("ir-memory").value = "256"; $("ir-cpus").value = "0.5"; $("ir-ttl").value = "300";
    $("ir-argv").value = '["python", "-c", "print(\'ready\')"]';
  }});
})();
