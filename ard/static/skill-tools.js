"use strict";

(() => {
  const A=window.ARD;
  if(!A)return;
  const panel=A.panel("agents","skill-tools","可复用技能与受限智能体",`
    <p>技能使用平台实际的数据、知识和模型引擎。默认参数可留空，由每次调用补充。每次修改保存为新版本；发布申请由审核员或管理员处理。</p>
    <div class="form-grid"><label class="field">技能名称<input id="sk-name" maxlength="150"></label><label class="field">内置操作<select id="sk-operation"></select></label></div>
    <p id="sk-description"></p><label class="field">用途说明<textarea id="sk-notes" rows="2" maxlength="3000"></textarea></label>
    <label class="field">版本默认参数 JSON<textarea id="sk-defaults" rows="4" spellcheck="false">{}</textarea></label>
    <div class="action-bar"><button id="sk-new" class="button ghost" type="button">清空并新建技能</button><button id="sk-save" class="button primary" type="button">保存技能新版本</button></div>
    <label class="field">已保存技能版本<select id="sk-selected"></select></label>
    <div class="action-bar"><button id="sk-load" class="button ghost" type="button">载入定义</button><button id="sk-versions" class="button ghost" type="button">查看版本历史</button><button id="sk-publish" class="button secondary" type="button">申请发布所选版本</button><button id="sk-export" class="button ghost" type="button">导出所选版本</button></div>
    <div class="action-bar"><label class="field">导入技能包<input id="sk-file" type="file" accept=".json,application/json"></label><button id="sk-import" class="button ghost" type="button">导入并保存</button></div>
    <p id="sk-version-status" role="status">尚未载入技能</p>
    <details><summary>所选内置操作参数规范</summary><pre id="sk-schema" class="result-box"></pre></details>
    <h3>实际调用</h3><p>清洗、拆分会写入新的数据版本。参数中资产ID必须属于当前项目。点击示例可填入当前已选择的数据或模型。</p>
    <button id="sk-example" class="button ghost" type="button">填入所选技能调用示例</button>
    <label class="field">调用参数 JSON<textarea id="sk-arguments" rows="5" spellcheck="false">{}</textarea></label><button id="sk-invoke" class="button primary" type="button">调用所选技能</button>
    <pre id="sk-result" class="result-box" tabindex="0">技能定义、调用结果或版本历史将在这里显示</pre>
    <h3>发布审核</h3><div id="sk-reviews" class="approval-list"></div>
    <div class="divider"></div><h3>受限智能体运行</h3>
    <p>明确勾选允许调用的技能。服务器配置的聊天模型只可规划这些技能，最多8步；全部调用参数先通过校验，再执行实际操作。未配置聊天模型会报错。此运行不提供自动恢复，失败记录保留已经完成的步骤。</p>
    <div id="sk-grants" class="micro-list" aria-label="授权智能体可调用的技能"></div>
    <label class="field">任务目标<textarea id="sk-goal" rows="3" maxlength="4000" placeholder="例如：读取指定数据的概览并生成分组统计"></textarea></label>
    <div class="form-grid"><label class="field">最多执行步骤<input id="sk-max-steps" type="number" min="1" max="8" value="3"></label><label class="field">提供给规划模型的输入 JSON<textarea id="sk-agent-input" rows="4" spellcheck="false">{}</textarea></label></div>
    <button id="sk-agent-run" class="button primary" type="button">按勾选授权运行</button>
    <pre id="sk-agent-result" class="result-box" tabindex="0">智能体运行结果将在这里显示</pre>
    <h3>调用与运行历史</h3><button id="sk-refresh" class="button ghost" type="button">刷新技能、审核和历史</button><div id="sk-history" class="micro-list"></div>
  `);
  const $=id=>panel.querySelector("#"+id);
  let catalogue=[],records=[],parentId="",grants=new Set();
  const parse=(id,label)=>{let value;try{value=JSON.parse($(id).value||"{}");}catch(_){throw new Error(label+"不是有效JSON");}if(!value||Array.isArray(value)||typeof value!=="object")throw new Error(label+"必须为JSON对象");return value;};
  const bind=(id,task)=>{const button=$(id);button.addEventListener("click",()=>A.run(button,task));};
  const makeButton=(label,task)=>{const el=A.el("button","button ghost compact",label);el.type="button";el.addEventListener("click",()=>A.run(el,task));return el;};
  function selected(context){const record=records.find(r=>r.id===$("sk-selected").value);if(!record||record.project_id!==context.pid)throw new Error("请选择当前项目的技能版本");return record;}
  function showOperation(){const builtin=catalogue.find(x=>x.operation===$("sk-operation").value);$("sk-description").textContent=builtin?.description||"请选择操作";$("sk-schema").textContent=builtin?JSON.stringify(builtin.input_schema,null,2):"";}
  function setChoices(select,items,label){const old=select.value;select.replaceChildren(A.option("",label));items.forEach(([value,text])=>select.append(A.option(value,text)));if(items.some(([value])=>value===old))select.value=old;}
  function clearDefinition(){parentId="";$("sk-name").value="";$("sk-notes").value="";$("sk-defaults").value="{}";$("sk-version-status").textContent="新技能；保存后生成独立版本";}
  $("sk-operation").addEventListener("change",showOperation);
  bind("sk-new",async()=>{clearDefinition();$("sk-result").textContent="尚无结果";});
  bind("sk-save",async context=>{const operation=$("sk-operation").value;if(!operation)throw new Error("请选择内置操作");const result=await A.request(`/api/projects/${context.pid}/skills`,{method:"POST",body:{name:$("sk-name").value.trim(),description:$("sk-notes").value,operation,defaults:parse("sk-defaults","默认参数"),parent_id:parentId||null}},context);await A.refresh(context);parentId=result.id;$("sk-selected").value=result.id;$("sk-version-status").textContent=`已保存 v${result.version} · ${result.id}`;$("sk-result").textContent=JSON.stringify(result,null,2);});
  bind("sk-load",async context=>{const record=selected(context);parentId=record.id;$("sk-name").value=record.name;$("sk-notes").value=record.description;$("sk-operation").value=record.operation;$("sk-defaults").value=JSON.stringify(record.defaults,null,2);$("sk-version-status").textContent=`正在编辑 v${record.version} · ${record.id}；保存将创建其子版本`;showOperation();});
  bind("sk-versions",async context=>{const record=selected(context);$("sk-result").textContent=JSON.stringify(await A.request(`/api/skills/${record.id}/versions`,{},context),null,2);});
  bind("sk-publish",async context=>{const record=selected(context);const result=await A.request(`/api/skills/${record.id}/publication`,{method:"POST"},context);await A.refresh(context);$("sk-result").textContent=JSON.stringify(result,null,2);});
  bind("sk-export",async context=>{const record=selected(context);A.saveBlob(await A.request(`/api/skills/${record.id}/export`,{responseType:"blob"},context),`skill-${record.id}.json`);});
  bind("sk-import",async context=>{const file=$("sk-file").files[0];if(!file||file.size>300*1024)throw new Error("请选择不超过300KiB的技能JSON包");let payload;try{payload=JSON.parse(await file.text());}catch(_){throw new Error("技能包不是有效JSON");}if(!A.current(context))return;const result=await A.request(`/api/projects/${context.pid}/skills/import`,{method:"POST",body:payload},context);await A.refresh(context);$("sk-selected").value=result.id;$("sk-result").textContent=JSON.stringify(result,null,2);$("sk-file").value="";});
  bind("sk-example",async context=>{const record=selected(context),builtin=catalogue.find(x=>x.operation===record.operation);if(!builtin)throw new Error("内置操作目录尚未加载");const example=JSON.parse(JSON.stringify(builtin.example)),data=A.data();if("asset_id" in example)example.asset_id=data.datasetId||data.datasets[0]?.id||"请选择数据版本ID";if("model_id" in example)example.model_id=data.modelId||data.models[0]?.id||"请选择模型版本ID";if("document_id" in example)example.document_id=data.documents[0]?.id||"请选择文档版本ID";$("sk-arguments").value=JSON.stringify({...example,...record.defaults},null,2);});
  bind("sk-invoke",async context=>{const record=selected(context),argumentsValue=parse("sk-arguments","调用参数");$("sk-result").textContent="正在执行实际调用…";try{const result=await A.request(`/api/skills/${record.id}/invoke`,{method:"POST",body:{arguments:argumentsValue}},context);$("sk-result").textContent=JSON.stringify(result,null,2);}catch(error){if(A.current(context))$("sk-result").textContent="调用失败，请查看错误提示和调用历史。";throw error;}finally{if(A.current(context))await A.refresh(context);}});
  bind("sk-agent-run",async context=>{const maxSteps=Number($("sk-max-steps").value),skillIds=[...grants];if(!skillIds.length)throw new Error("请勾选明确授权的技能");if(!Number.isInteger(maxSteps)||maxSteps<1||maxSteps>8)throw new Error("步骤上限为1到8的整数");const input=parse("sk-agent-input","智能体输入"),goal=$("sk-goal").value.trim();if(!goal)throw new Error("请输入任务目标");$("sk-agent-result").textContent="正在调用已配置模型并执行授权技能…";try{const result=await A.request(`/api/projects/${context.pid}/agents/run`,{method:"POST",body:{goal,skill_ids:skillIds,max_steps:maxSteps,input}},context);$("sk-agent-result").textContent=JSON.stringify(result,null,2);}catch(error){if(A.current(context))$("sk-agent-result").textContent="运行失败，请查看错误提示和已完成步骤的历史。";throw error;}finally{if(A.current(context))await A.refresh(context);}});
  async function refresh(context){
    const results=await Promise.all([A.request("/api/skills/builtins",{},context),A.request(`/api/projects/${context.pid}/skills`,{},context),A.request(`/api/projects/${context.pid}/skill-reviews`,{},context),A.request(`/api/projects/${context.pid}/skill-runs?limit=30`,{},context),A.request(`/api/projects/${context.pid}/agent-runs?limit=20`,{},context)]);
    if(!A.current(context))return;
    [catalogue,records]=results;
    setChoices($("sk-operation"),catalogue.map(x=>[x.operation,`${x.name} · ${x.operation}`]),"请选择内置操作");showOperation();
    setChoices($("sk-selected"),records.map(x=>[x.id,`${x.name} · v${x.version} · ${x.publication_status==="PUBLISHED"?"已发布":"草稿"} · ${x.id.slice(0,8)}`]),"请选择技能版本");
    const identity=A.data().me||{},canReview=["admin","reviewer"].includes(identity.role),grantBox=$("sk-grants");grantBox.replaceChildren();
    grants=new Set([...grants].filter(id=>records.some(r=>r.id===id)));
    records.filter(r=>r.publication_status==="PUBLISHED"||r.creator===identity.user||identity.role==="admin").forEach(record=>{const label=A.el("label","field"),check=A.el("input");check.type="checkbox";check.checked=grants.has(record.id);check.addEventListener("change",()=>{if(check.checked)grants.add(record.id);else grants.delete(record.id);});label.append(check,A.el("span","",`${record.name} · v${record.version} · ${record.operation}`));grantBox.append(label);});
    if(!grantBox.children.length)grantBox.append(A.el("p","empty","先保存自己的技能或等待其他技能通过发布审核。"));
    const reviews=$("sk-reviews");reviews.replaceChildren();
    for(const review of results[2]){const row=A.el("article","approval-item");row.append(A.el("strong","",`${review.name} · ${review.status}`));if(review.status==="PENDING"&&canReview){const comment=A.el("input");comment.placeholder="审核意见";comment.maxLength=2000;row.append(comment);for(const [decision,label] of [["approve","通过发布"],["reject","驳回发布"]])row.append(makeButton(label,async captured=>{if(review.project_id!==captured.pid)throw new Error("项目已切换");await A.request(`/api/skill-reviews/${review.id}/decide`,{method:"POST",body:{decision,expected_revision:review.revision,comment:comment.value}},captured);await A.refresh(captured);}));}else if(review.comment)row.append(A.el("p","",review.comment));reviews.append(row);}
    if(!reviews.children.length)reviews.append(A.el("p","empty","暂无发布申请"));
    const history=$("sk-history");history.replaceChildren();for(const [kind,items] of [["技能",results[3]],["智能体",results[4]]])for(const record of items){const details=A.el("details"),summary=A.el("summary","",`${kind} · ${record.operation||record.goal} · ${record.status} · ${record.created_at}`),pre=A.el("pre","result-box",JSON.stringify(record,null,2));details.append(summary,pre);history.append(details);}
    if(!history.children.length)history.append(A.el("p","empty","暂无实际调用记录"));
  }
  bind("sk-refresh",refresh);
  A.register("skill-tools",{refresh,reset(){catalogue=[];records=[];parentId="";grants=new Set();panel.querySelectorAll("input,textarea").forEach(el=>{if(el.type==="checkbox")el.checked=false;else el.value="";});for(const id of ["sk-operation","sk-selected"])$(id).replaceChildren(A.option("","请选择"));for(const id of ["sk-description","sk-schema","sk-reviews","sk-grants","sk-history"])$(id).replaceChildren();$("sk-result").textContent="尚无结果";$("sk-agent-result").textContent="尚无结果";$("sk-version-status").textContent="尚未载入技能";}});
})();
