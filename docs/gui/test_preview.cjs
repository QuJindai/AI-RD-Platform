"use strict";
// DOM unit checks only: jsdom does not render desktop/mobile layouts.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM, VirtualConsole } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "gui-preview.html"), "utf8");
const fragment = fs.readFileSync(path.join(__dirname, "preview-fragment.html"), "utf8");
const results = [];

function check(name, run) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", error => errors.push(error.message));
  const dom = new JSDOM(html, { runScripts: "dangerously", virtualConsole });
  const document = dom.window.document;
  const query = selector => {
    const element = document.querySelector(selector);
    assert.ok(element, "Missing element: " + selector);
    return element;
  };
  try {
    run({ document, query, window: dom.window, click: selector => query(selector).click() });
    assert.deepEqual(errors, [], "Script or CSS parse errors");
    results.push({ name, status: "PASS" });
  } catch (error) {
    results.push({ name, status: "FAIL", error: error.stack });
  } finally {
    dom.window.close();
  }
}

check("Standalone build contains exact source and has no external resources", ({ document }) => {
  assert.ok(html.includes(fragment));
  assert.ok(Buffer.byteLength(fragment) < 1_000_000);
  assert.equal(document.querySelectorAll("script[src],link[href],iframe,img,video,audio").length, 0);
  assert.doesNotMatch(fragment, /\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|localStorage|sessionStorage/);
  const ids = [...document.querySelectorAll("[id]")].map(el => el.id);
  assert.equal(new Set(ids).size, ids.length, "Duplicate element IDs");
});

check("Five page switches show exactly one matching view", ({ document, click, query }) => {
  for (const page of ["overview", "data", "models", "agents", "ops"]) {
    click('[data-page="' + page + '"]');
    const visible = [...document.querySelectorAll("[data-view]")].filter(el => !el.hidden);
    assert.deepEqual(visible.map(el => el.dataset.view), [page]);
    assert.equal(document.querySelectorAll("[aria-current=page]").length, 1);
    assert.equal(query('[data-page="' + page + '"]').getAttribute("aria-current"), "page");
  }
});

check("Empty project hides all sample assets and can restore selected page", ({ document, window, query, click }) => {
  click('[data-page="models"]');
  query("#ag-project").value = "empty";
  query("#ag-project").dispatchEvent(new window.Event("change"));
  assert.equal(query("#ag-empty").hidden, false);
  assert.ok([...document.querySelectorAll("[data-view]")].every(el => el.hidden));
  click('[data-page="data"]');
  assert.ok([...document.querySelectorAll("[data-view]")].every(el => el.hidden));
  click("#ag-load-demo");
  assert.equal(query("#ag-empty").hidden, true);
  assert.equal(query('[data-view="data"]').hidden, false);
});

check("Project form rejects blank names and out-of-range quotas", ({ query, click, window }) => {
  click("#ag-new-project");
  query("#ag-project-name").value = "   ";
  query("#ag-project-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  assert.equal(query("#ag-project-error").hidden, false);
  assert.equal(query("#ag-modal-layer").hidden, false);
  query("#ag-project-name").value = "合成项目";
  query("#ag-project-form input[type=number]").value = "0";
  query("#ag-project-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  assert.equal(query("#ag-modal-layer").hidden, false);
  assert.equal(query("#ag-project-error").hidden, true, "Name error should clear before quota validation");
  assert.equal(query("#ag-project-name").hasAttribute("aria-invalid"), false);
  query("#ag-project-name").value = "";
  query("#ag-project-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  click("#ag-project-modal [data-close]");
  click("#ag-new-project");
  assert.equal(query("#ag-project-error").hidden, true);
  assert.equal(query("#ag-project-name").hasAttribute("aria-invalid"), false);
});

check("Project feedback safely renders user text and does not create assets", ({ document, query, click, window }) => {
  click("#ag-new-project");
  query("#ag-project-name").value = '<img src=x onerror="alert(1)">';
  query("#ag-project-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  assert.equal(query("#ag-modal-layer").hidden, true);
  assert.match(query("#ag-feedback").textContent, /<img src=x/);
  assert.equal(document.querySelectorAll("img").length, 0);
  assert.equal(query("#ag-project").options.length, 2);
});

check("Modal focus cycles, Escape closes and returns focus", ({ document, query, click, window }) => {
  query("#ag-new-project").focus();
  click("#ag-new-project");
  assert.equal(document.activeElement.id, "ag-project-name");
  assert.equal(query(".ag-shell").inert, true);
  const first = query("#ag-project-modal [data-close]");
  const last = query("#ag-project-modal button[type=submit]");
  last.focus();
  last.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
  assert.equal(document.activeElement, first);
  first.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true }));
  assert.equal(document.activeElement, last);
  last.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(query("#ag-modal-layer").hidden, true);
  assert.equal(query(".ag-shell").inert, false);
  assert.equal(document.activeElement.id, "ag-new-project");
});

check("Credential preview cannot accept real tokens", ({ query, click }) => {
  click("#ag-credentials");
  assert.equal(query("#ag-auth-modal").hidden, false);
  assert.equal(query("#ag-auth-modal input").disabled, true);
  assert.equal(query("#ag-auth-modal input").type, "password");
});

check("Dataset selection updates selected title and metadata", ({ document, query, click }) => {
  for (const [key, rows] of [["raw", "63"], ["split", "15"], ["clean", "60"]]) {
    click('[data-asset="' + key + '"]');
    assert.ok(query("#ag-asset-meta").textContent.startsWith(rows + " 行"));
    assert.equal(document.querySelectorAll("[data-asset][aria-pressed=true]").length, 1);
    assert.equal(query('[data-asset="' + key + '"]').getAttribute("aria-pressed"), "true");
  }
});

check("Training submit and cancellation show reversible demo states", ({ query, click }) => {
  click("#ag-train");
  assert.match(query("#ag-job-status").textContent, /排队/);
  assert.equal(query("#ag-progress").value, 0);
  click("#ag-cancel");
  assert.match(query("#ag-job-status").textContent, /已取消/);
  assert.equal(query("#ag-cancel").disabled, true);
  click("#ag-train");
  assert.equal(query("#ag-cancel").disabled, false);
});

check("Expired deployment disables prediction and can restore the sample", ({ query, click }) => {
  click("#ag-predict");
  assert.match(query("#ag-prediction").textContent, /未调用模型/);
  click("#ag-expire");
  assert.equal(query("#ag-predict").disabled, true);
  assert.match(query("#ag-prediction").textContent, /已过期/);
  click("#ag-predict");
  assert.match(query("#ag-prediction").textContent, /已过期/);
  click("#ag-expire");
  assert.equal(query("#ag-predict").disabled, false);
});

check("Search validates blank input and marks source quotations as synthetic", ({ query, click }) => {
  query("#ag-query").value = " ";
  click("#ag-search");
  assert.equal(query("#ag-evidence").hidden, true);
  assert.match(query("#ag-feedback").textContent, /请先输入/);
  query("#ag-query").value = "质量";
  click("#ag-search");
  assert.equal(query("#ag-evidence").hidden, false);
  assert.match(query("#ag-evidence").textContent, /合成引文/);
});

check("Approval and rejection update execution and overview consistently", ({ query, click }) => {
  click("#ag-approve");
  assert.match(query("#ag-output-state").textContent, /完成/);
  assert.equal(query("#ag-reject").disabled, true);
  assert.equal(query("#ag-approval-status").textContent, query("#ag-overview-approval").textContent);
  click("#ag-review-reset");
  assert.equal(query("#ag-reject").disabled, false);
  click("#ag-reject");
  assert.equal(query("#ag-output-state").textContent, "已终止");
  assert.match(query("#ag-overview-approval").textContent, /已拒绝/);
});

check("Connector and audit feedback do not claim actual external execution", ({ query, click }) => {
  click("#ag-probe");
  assert.match(query("#ag-connector-state").textContent, /连接不可用/);
  assert.match(query("#ag-feedback").textContent, /未发送探测请求/);
  click("#ag-audit");
  assert.equal(query("#ag-audit-result").hidden, false);
  assert.match(query("#ag-audit-result").textContent, /未读取或验证实际/);
});

const report = {
  generated_at: new Date().toISOString(),
  type: "DOM unit checks; no browser rendering or responsive layout validation",
  node: process.version,
  jsdom: require("jsdom/package.json").version,
  passed: results.filter(item => item.status === "PASS").length,
  failed: results.filter(item => item.status === "FAIL").length,
  checks: results
};
process.stdout.write(JSON.stringify(report, null, 2) + "\n");
if (report.failed) process.exitCode = 1;
