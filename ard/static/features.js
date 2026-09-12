"use strict";

// The feature host shares the application's checked identity/project boundary.
(() => {
  const controllers = new Map();
  const panels = new Map();
  const busy = new Map();

  function panel(tab, id, title, markup) {
    if (panels.has(id)) throw new Error("功能面板重复：" + id);
    const parent = document.getElementById("tab-" + tab);
    if (!parent) throw new Error("功能页面不存在：" + tab);
    const card = node("article", "card feature-panel");
    card.id = id;
    const header = node("div", "card-head");
    header.append(node("h2", "", title));
    card.append(header);
    const contents = node("div", "feature-contents");
    // Only constant, author-written markup is accepted here. Runtime values use textContent.
    const template = document.createElement("template");
    template.innerHTML = markup;
    contents.append(template.content.cloneNode(true));
    card.append(contents);
    parent.append(card);
    panels.set(id, card);
    return card;
  }

  function register(id, controller) {
    if (controllers.has(id)) throw new Error("功能控制器重复：" + id);
    controllers.set(id, controller);
  }

  function reset() {
    for (const [button, operation] of busy) {
      button.textContent = operation.original;
      button.disabled = operation.disabled;
    }
    busy.clear();
    for (const [id, controller] of controllers) {
      const card = panels.get(id);
      try {
        if (card) {
          card.querySelectorAll("input,textarea").forEach(input => {
            if (["checkbox", "radio"].includes(input.type)) input.checked = input.defaultChecked;
            else if (input.type !== "button" && input.type !== "submit") input.value = "";
          });
          card.hidden = true;
        }
        controller.reset?.();
      } catch (error) {
        // Failed cleanup must never leave the previous identity's data visible.
        if (card) {
          card.replaceChildren(node("p", "status-banner", "功能状态清理失败，请重新加载页面。"));
          card.hidden = true;
        }
      }
    }
  }

  async function refreshFeatures(context) {
    requireCurrent(context);
    const outcomes = await Promise.allSettled([...controllers].map(async ([id, controller]) => {
      requireCurrent(context);
      await controller.refresh?.(context);
      requireCurrent(context);
      if (panels.has(id)) panels.get(id).hidden = false;
    }));
    requireCurrent(context);
    outcomes.forEach((result, index) => {
      if (result.status !== "rejected" || result.reason instanceof StaleContext) return;
      const id = [...controllers.keys()][index];
      const card = panels.get(id);
      if (card) card.hidden = false;
      toast("功能区域未能刷新：" + detail(result.reason), "error");
    });
  }

  async function run(button, task) {
    if (button?.disabled || busy.has(button)) return;
    let context;
    try { context = captureProject(); } catch (error) { toast(detail(error), "error"); return; }
    const original = button?.textContent;
    const operation = {original, disabled: button?.disabled};
    if (button) { busy.set(button, operation); button.disabled = true; button.textContent = "处理中…"; }
    try {
      const result = await task(context);
      requireCurrent(context);
      return result;
    } catch (error) {
      if (!(error instanceof StaleContext)) toast(detail(error), "error");
    } finally {
      if (button && contextIsCurrent(context) && busy.get(button) === operation) {
        button.disabled = operation.disabled; button.textContent = original; busy.delete(button);
      }
    }
  }

  function request(path, options = {}, context = captureProject()) {
    let prepared = options;
    if (options.body && typeof options.body === "object" &&
        !(options.body instanceof FormData) && !(options.body instanceof Blob)) {
      prepared = {...options, body: JSON.stringify(options.body)};
    }
    return api(path, prepared, context);
  }

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const anchor = node("a");
    anchor.href = url;
    anchor.download = filename.replace(/[\\/\x00-\x1f]/g, "_");
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  window.ARD = Object.freeze({
    panel, register, reset, refreshFeatures, run, request, saveBlob,
    context: captureProject,
    current: contextIsCurrent,
    el: node, option, message: toast,
    refresh: async (context = captureProject()) => { requireCurrent(context); await refreshProject(context); },
    data: () => Object.freeze({
      projectId: state.projectId,
      project: state.projects.find(item => item.id === state.projectId),
      projects: state.projects, me: state.me,
      datasets: state.datasets, datasetId: state.datasetId, rows: state.rows,
      models: state.models, modelId: state.modelId, deployments: state.deployments,
      workflows: state.workflows, documents: state.documents, jobs: state.jobs,
      approvals: state.approvals
    })
  });
})();
