// DOM-model unit tests only; these do not claim actual browser rendering.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

class Element {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase(); this.id = id; this.value = "";
    this.type = ""; this.children = []; this.listeners = {}; this.style = {};
    this.checked = false; this.files = []; this.disabled = false; this._text = "";
  }
  get textContent() { return this._text + this.children.map(child => child.textContent || "").join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ""; }
  addEventListener(event, listener) { this.listeners[event] = listener; }
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

function runtime(responder = async () => ({})) {
  const elements = new Map(), controllers = new Map();
  let current = {pid: "A", epoch: 1};
  function element(id) { return elements.get(id); }
  const document = {getElementById: element, createTextNode: text => ({textContent: text})};
  const ARD = {
    panel(tab, id, title, markup) {
      for (const match of markup.matchAll(/<(input|textarea|select|button|p|div)[^>]*\bid="([^"]+)"[^>]*>/g)) {
        const node = new Element(match[1], match[2]);
        node.value = match[0].match(/\bvalue="([^"]*)"/)?.[1] || "";
        node.type = match[0].match(/\btype="([^"]*)"/)?.[1] || "";
        elements.set(node.id, node);
      }
      const panel = new Element("article", id);
      panel.querySelectorAll = selector => selector === "input,textarea"
        ? [...elements.values()].filter(item => ["INPUT", "TEXTAREA"].includes(item.tagName)) : [];
      return panel;
    },
    register(id, controller) { controllers.set(id, controller); },
    current(context) { return context.pid === current.pid && context.epoch === current.epoch; },
    context() { return {...current}; },
    request(path, options, context) { return responder(path, options, context); },
    run(button, task) { return task({...current}); },
    el(tag, className, text = "") { const node = new Element(tag); node.textContent = text; return node; },
    option(value, label) { const option = new Element("option"); option.value = value; option.textContent = label; return option; },
    message() {}, saveBlob() {}, data() { return {me: {role: "admin"}}; }
  };
  const context = {ARD, document, FormData, Blob, console};
  vm.runInNewContext(readFileSync(new URL("../ard/static/knowledge-tools.js", import.meta.url), "utf8"), context);
  const controller = controllers.get("knowledge-tools");
  ARD.refresh = value => controller.refresh(value);
  return {element, controller, context: () => ({...current}),
    transition() { current = {pid: "B", epoch: current.epoch + 1}; controller.reset(); },
    click(id) { return element(id).listeners.click(); },
    change(id) { return element(id).listeners.change(); }};
}

function detail(id, text) {
  return {document: {id, name: text, version: 1, revision: 1, strategy: "paragraph", chunk_size: 600,
    overlap: 60, delimiter: "\n\n", is_latest: true, archived: false, chunk_count: 1, status_revision: 0},
    text, source: {filename: "source.txt"}};
}

test("knowledge reset clears source text, question, results, files and selection", async () => {
  const ui = runtime(async path => path.endsWith("/chunks?offset=0&limit=20")
    ? {total: 0, chunks: []} : detail("doc-a", "A private evidence"));
  ui.element("knowledge-select").value = "doc-a";
  await ui.click("knowledge-load");
  ui.element("knowledge-file").value = "selected.txt";
  ui.element("knowledge-question").value = "A private question";
  ui.element("knowledge-answer-output").textContent = "A private answer";
  ui.element("knowledge-archive-confirm").checked = true;
  ui.transition();
  assert.equal(ui.element("knowledge-edit-text").value, "");
  assert.equal(ui.element("knowledge-edit-name").value, "");
  assert.equal(ui.element("knowledge-file").value, "");
  assert.equal(ui.element("knowledge-question").value, "");
  assert.equal(ui.element("knowledge-archive-confirm").checked, false);
  assert.doesNotMatch(ui.element("knowledge-answer-output").textContent, /private/);
  assert.equal(ui.element("knowledge-select").children.length, 1);
});

test("late knowledge document detail cannot repopulate another identity or project", async () => {
  const pending = deferred(), ui = runtime(() => pending.promise);
  ui.element("knowledge-select").value = "doc-a";
  const load = ui.click("knowledge-load");
  ui.transition();
  pending.resolve(detail("doc-a", "A secret"));
  await load;
  assert.equal(ui.element("knowledge-edit-text").value, "");
  assert.doesNotMatch(ui.element("knowledge-detail").textContent, /A secret/);
});

test("late document response cannot replace a newer selection in the same project", async () => {
  const pending = deferred(), ui = runtime(() => pending.promise);
  ui.element("knowledge-select").value = "doc-a";
  const load = ui.click("knowledge-load");
  ui.element("knowledge-select").value = "doc-b";
  ui.change("knowledge-select");
  pending.resolve(detail("doc-a", "A secret"));
  await load;
  assert.equal(ui.element("knowledge-edit-text").value, "");
  assert.equal(ui.element("knowledge-select").value, "doc-b");
});

test("late generated answer cannot enter the new project", async () => {
  const pending = deferred(), ui = runtime(() => pending.promise);
  ui.element("knowledge-question").value = "Question in A";
  ui.element("knowledge-answer-top").value = "5";
  ui.element("knowledge-answer-threshold").value = "0.05";
  const answer = ui.click("knowledge-answer");
  ui.transition();
  pending.resolve({answer: "A secret answer", citations: [], limitations: [], generated: true});
  await answer;
  assert.doesNotMatch(ui.element("knowledge-answer-output").textContent, /A secret answer/);
});
