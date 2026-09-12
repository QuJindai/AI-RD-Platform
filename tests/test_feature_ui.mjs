// Actual application scripts + HTTP API in a non-rendering DOM model.
// This is not a browser or target-device visual verification.
import assert from "node:assert/strict";
import {readFileSync, mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawn} from "node:child_process";
import {createServer} from "node:net";
import {once} from "node:events";
import vm from "node:vm";
import test, {before, after} from "node:test";
import {JSDOM, VirtualConsole} from "jsdom";

const root = new URL("../", import.meta.url);
const admin = "synthetic-admin-ui-token";
const reader = "synthetic-auditor-ui-token";
let server, directory, base, projects;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function call(path, body) {
  const response = await fetch(base + path, {method: body ? "POST" : "GET",
    headers: {Authorization: "Bearer " + admin, "Content-Type": "application/json"},
    ...(body ? {body: JSON.stringify(body)} : {})});
  assert.ok(response.ok, await response.clone().text());
  return response.json();
}
before(async () => {
  directory = mkdtempSync(join(tmpdir(), "ard-dom-http-"));
  const socket = createServer();
  socket.listen(0, "127.0.0.1"); await once(socket, "listening");
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  base = "http://127.0.0.1:" + port;
  server = spawn(process.env.ARD_PYTHON || "python", ["-m", "ard", "--port", String(port), "--data-dir", directory], {
    cwd: root, stdio: ["ignore", "pipe", "pipe"], env: {...process.env,
      ARD_IDENTITIES: JSON.stringify({
        [admin]: {user: "ui-admin", role: "admin", projects: ["*"]},
        [reader]: {user: "ui-auditor", role: "auditor", projects: ["*"]}
      }), ARD_DATA_SOURCES: "{}", ARD_LLM_PROVIDER: "ollama", ARD_OLLAMA_URL: "", ARD_OPENAI_BASE_URL: ""
    }
  });
  let logs = "";
  server.stdout.on("data", data => { logs = (logs + data).slice(-8000); });
  server.stderr.on("data", data => { logs = (logs + data).slice(-8000); });
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if ((await fetch(base + "/health")).ok) { ready = true; break; } } catch {}
    if (server.exitCode !== null) throw new Error(logs);
    await delay(100);
  }
  assert.ok(ready, logs);
  projects = await Promise.all(["A", "B"].map(name => call("/api/projects", {name: "Synthetic UI " + name})));
});
after(async () => {
  if (server && server.exitCode === null) { server.kill("SIGTERM"); await once(server, "exit"); }
  rmSync(directory, {recursive: true, force: true});
});

async function runtime() {
  const errors = [], requests = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", error => errors.push(error.message));
  const dom = new JSDOM(readFileSync(new URL("ard/static/index.html", root), "utf8"), {
    url: base, runScripts: "outside-only", virtualConsole
  });
  // Let jsdom's initial lifecycle finish before explicitly invoking application init.
  await delay(0);
  Object.assign(dom.window, {Headers, FormData, Blob, Response, Request});
  dom.window.fetch = async (path, options) => {
    requests.push({path, options});
    return fetch(new URL(path, base), options);
  };
  dom.window.setInterval = () => 0;
  dom.window.URL.createObjectURL = blob => {
    assert.ok(blob instanceof Blob, "downloads must receive a Blob");
    return "blob:synthetic-dom-test";
  };
  dom.window.URL.revokeObjectURL = () => {};
  dom.window.sessionStorage.setItem("ard_token", admin);
  dom.window.sessionStorage.setItem("ard_project", projects[0].id);
  const context = dom.getInternalVMContext();
  for (const script of dom.window.document.querySelectorAll("script[src]")) {
    const path = script.getAttribute("src").replace("/static/", "ard/static/");
    vm.runInContext(readFileSync(new URL(path, root), "utf8"), context, {filename: path});
  }
  await vm.runInContext("init()", context);
  return {dom, context, errors, requests, window: dom.window,
    el: id => dom.window.document.getElementById(id)};
}

test("all feature panels mount and refresh against the actual HTTP API", async () => {
  const ui = await runtime();
  try {
    assert.equal(ui.window.ARD.data().projectId, projects[0].id);
    for (const id of ["data-tools", "source-tools", "knowledge-tools", "model-tools",
      "workflow-tools", "skill-tools", "operation-tools", "integration-packages", "integration-mcp", "integration-runtime"]) {
      assert.ok(ui.el(id), id + " mounted");
      assert.equal(ui.el(id).hidden, false, id + " loaded");
    }
    assert.equal(ui.window.document.querySelectorAll(".toast.error").length, 0,
      ui.window.document.body.textContent);
    assert.deepEqual(ui.errors, []);
    const ids = [...ui.window.document.querySelectorAll("[id]")].map(element => element.id);
    assert.equal(new Set(ids).size, ids.length, "no duplicate control IDs");
    assert.ok(ui.el("wf-node-type").options.length >= 16);
  } finally { ui.dom.window.close(); }
});

test("project refresh updates server-side names and quota settings", async () => {
  const ui = await runtime();
  try {
    const pid = projects[0].id;
    const settings = await call("/api/projects/" + pid + "/settings");
    const response = await fetch(base + "/api/projects/" + pid + "/settings", {
      method: "PATCH", headers: {Authorization: "Bearer " + admin, "Content-Type": "application/json"},
      body: JSON.stringify({name: "Synthetic updated name", expected_revision: settings.project.revision,
        quota_bytes: 209715200, max_jobs: 6})
    });
    assert.ok(response.ok, await response.text());
    await ui.window.ARD.refresh();
    assert.equal(ui.window.ARD.data().project.name, "Synthetic updated name");
    assert.equal(ui.window.ARD.data().project.max_jobs, 6);
    assert.match(ui.el("project-select").selectedOptions[0].textContent, /updated name/);
  } finally { ui.dom.window.close(); }
});

test("a late developer action cannot reenable auditor controls or reveal former inputs", async () => {
  const ui = await runtime();
  try {
    let resolve;
    const slow = new Promise(done => { resolve = done; });
    const button = ui.el("knowledge-index-build");
    assert.equal(button.disabled, false);
    ui.el("knowledge-question").value = "Former private question";
    const pending = ui.window.ARD.run(button, async () => slow);
    await vm.runInContext("changeIdentity(" + JSON.stringify(reader) + ")", ui.context);
    assert.equal(button.disabled, true);
    assert.equal(ui.el("knowledge-question").value, "");
    resolve("completed in previous identity");
    await pending;
    assert.equal(button.disabled, true);
    assert.equal(ui.window.ARD.data().me.role, "auditor");
    assert.doesNotMatch(button.textContent, /处理中/);
    assert.equal(ui.el("op-settings-save").disabled, true);
    assert.equal(ui.el("mf-service-chat").disabled, true);
  } finally { ui.dom.window.close(); }
});

test("busy buttons are reusable after a project transition without stale cleanup", async () => {
  const ui = await runtime();
  try {
    let oldResolve, newResolve;
    const button = ui.el("dt-table-import");
    const original = button.textContent;
    const oldRun = ui.window.ARD.run(button, () => new Promise(resolve => { oldResolve = resolve; }));
    await vm.runInContext("changeProject(" + JSON.stringify(projects[1].id) + ")", ui.context);
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, original);
    const newRun = ui.window.ARD.run(button, () => new Promise(resolve => { newResolve = resolve; }));
    oldResolve();
    await oldRun;
    assert.equal(button.disabled, true);
    assert.match(button.textContent, /处理中/);
    newResolve();
    await newRun;
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, original);
  } finally { ui.dom.window.close(); }
});

test("old project HTTP response cannot replace the new source panel state", async () => {
  const ui = await runtime();
  try {
    let resolve;
    const slow = new Promise(done => { resolve = done; });
    const original = ui.window.fetch;
    let held = false;
    ui.window.fetch = (path, options) => {
      if (!held && path === "/api/projects/" + projects[0].id + "/sources") { held = true; return slow; }
      return original(path, options);
    };
    const pending = ui.window.ARD.refresh();
    for (let attempt = 0; !held && attempt < 500; attempt++) await delay(5);
    assert.ok(held, "the first project's source request was reached within the deadline");
    await vm.runInContext("changeProject(" + JSON.stringify(projects[1].id) + ")", ui.context);
    resolve(new Response(JSON.stringify([{id: "old", name: "Private old source", driver: "sqlite", queries: []}]),
      {headers: {"content-type": "application/json"}}));
    await pending.catch(() => {});
    assert.equal(ui.window.ARD.data().projectId, projects[1].id);
    assert.doesNotMatch(ui.el("source-tools").textContent, /Private old source/);
  } finally { ui.dom.window.close(); }
});

test("JSON dataset export is downloaded as a Blob", async () => {
  const ui = await runtime();
  try {
    const data = await call("/api/projects/" + projects[0].id + "/datasets", {
      name: "Synthetic export", rows: [{x: 1}, {x: 2}]
    });
    await ui.window.ARD.refresh();
    await vm.runInContext("selectDataset(" + JSON.stringify(data.id) + ")", ui.context);
    let downloaded;
    ui.window.URL.createObjectURL = blob => { downloaded = blob; return "blob:synthetic"; };
    ui.window.HTMLAnchorElement.prototype.click = function () {};
    await vm.runInContext("exportDataset()", ui.context);
    assert.ok(downloaded instanceof Blob);
    assert.deepEqual(JSON.parse(await downloaded.text()), [{x: 1}, {x: 2}]);
  } finally { ui.dom.window.close(); }
});
