// DOM unit evidence only; no browser rendering or pointer-device acceptance claim.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {JSDOM} from "jsdom";

const copy=value=>JSON.parse(JSON.stringify(value));
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};}
function runtime(script,responder=async()=>[]){
  const dom=new JSDOM('<!doctype html><html><body><section id="tab-agents"></section><input id="workflow-name"><textarea id="workflow-nodes"></textarea><textarea id="workflow-edges"></textarea><textarea id="workflow-input"></textarea><select id="workflow-select"></select></body></html>',{runScripts:"outside-only"});
  const {window}=dom,{document}=window,controllers=new Map(),errors=[],calls=[];
  let current={pid:"A",epoch:1},pending=Promise.resolve();
  const data={workflows:[],datasets:[],models:[],documents:[],datasetId:"",modelId:"",me:{user:"local",role:"admin"}};
  const A={
    panel(tab,id,title,markup){const card=document.createElement("article");card.id=id;card.innerHTML=markup;document.getElementById("tab-"+tab).append(card);return card;},
    register(id,controller){controllers.set(id,controller);},
    el(tag,className="",text=""){const node=document.createElement(tag);node.className=className;node.textContent=text;return node;},
    option(value,text){const option=document.createElement("option");option.value=value;option.textContent=text;return option;},
    context(){return{...current};},current(context){return context.pid===current.pid&&context.epoch===current.epoch;},data(){return data;},
    run(button,task){const context={...current};pending=Promise.resolve().then(()=>task(context)).catch(error=>errors.push(error.message));return pending;},
    async request(path,options={},context){calls.push({path,options:copy(options),context:{...context}});const result=await responder(path,options,context);if(!A.current(context))throw new Error("stale context");return result;},
    async refresh(context){for(const controller of controllers.values())await controller.refresh(context);},saveBlob(){},message(){}
  };
  window.ARD=A;
  window.eval(readFileSync(new URL(`../ard/static/${script}`,import.meta.url),"utf8"));
  const controller=[...controllers.values()][0];
  return {document,window,data,calls,errors,controller,
    element:id=>document.getElementById(id),
    async click(id){document.getElementById(id).click();await pending;},
    async clickElement(element){element.click();await pending;},
    async flush(){await pending;},refresh:()=>controller.refresh({...current}),
    transition(){current={pid:"B",epoch:current.epoch+1};data.workflows=[];controller.reset();}
  };
}

function branchDefinition(){return{nodes:[{id:"in",type:"input"},{id:"gate",type:"condition",params:{path:"enabled",op:"eq",value:true}},{id:"yes",type:"output"},{id:"no",type:"output"}],edges:[{source:"in",target:"gate"},{source:"gate",target:"yes",when:true},{source:"gate",target:"no",when:false}]};}
async function loadJson(ui,definition){ui.element("workflow-name").value="合成分支";ui.element("workflow-nodes").value=JSON.stringify(definition.nodes);ui.element("workflow-edges").value=JSON.stringify(definition.edges);await ui.click("wf-read-json");}

test("visual editor renders actual branches and roundtrips node edits to saved JSON",async()=>{
  let ui;
  ui=runtime("workflow-tools.js",async(path,options)=>{
    if(path.endsWith("/workflows/import")){const record={id:"version-1",project_id:"A",...copy(options.body.workflow)};ui.data.workflows=[record];return record;}
    return [];
  });
  const definition=branchDefinition();await loadJson(ui,definition);
  assert.deepEqual(JSON.parse(ui.element("workflow-nodes").value),definition.nodes);
  assert.deepEqual(JSON.parse(ui.element("workflow-edges").value),definition.edges);
  assert.equal(ui.element("wf-canvas").querySelectorAll("path[marker-end]").length,3);
  assert.match(ui.element("wf-canvas").textContent,/不成立/);
  ui.element("wf-canvas").querySelectorAll('g[role="button"]')[1].dispatchEvent(new ui.window.MouseEvent("click",{bubbles:true}));
  ui.element("wf-node-id").value="gate2";await ui.click("wf-apply-node");
  const edges=JSON.parse(ui.element("workflow-edges").value);
  assert.equal(edges[0].target,"gate2");assert.equal(edges[2].source,"gate2");assert.equal(edges[2].when,false);
  await ui.click("wf-save-version");
  const saved=ui.calls.find(call=>call.path.endsWith("/workflows/import"));
  assert.deepEqual(saved.options.body.workflow.edges,edges);
  assert.deepEqual(saved.options.body.workflow.nodes,JSON.parse(ui.element("workflow-nodes").value));
  assert.equal(ui.element("wf-version").value,"version-1");
  assert.deepEqual(ui.errors,[]);
});

test("keyboard and drag ordering change actual node JSON while preserving edges",async()=>{
  const ui=runtime("workflow-tools.js"),definition=branchDefinition();await loadJson(ui,definition);
  ui.element("wf-node-list").children[2].dispatchEvent(new ui.window.KeyboardEvent("keydown",{key:"ArrowUp",altKey:true,bubbles:true}));
  assert.deepEqual(JSON.parse(ui.element("workflow-nodes").value).map(n=>n.id),["in","yes","gate","no"]);
  const rows=[...ui.element("wf-node-list").children];rows[0].dispatchEvent(new ui.window.Event("dragstart",{bubbles:true}));rows[3].dispatchEvent(new ui.window.Event("drop",{bubbles:true,cancelable:true}));
  assert.deepEqual(JSON.parse(ui.element("workflow-nodes").value).map(n=>n.id),["yes","gate","no","in"]);
  assert.deepEqual(JSON.parse(ui.element("workflow-edges").value),definition.edges);
  await ui.clickElement(ui.element("wf-edge-list").children[2].querySelector("button"));
  assert.equal(JSON.parse(ui.element("workflow-edges").value).length,2);
  assert.equal(ui.element("wf-canvas").querySelectorAll("path[marker-end]").length,2);
});

test("workflow reset clears both editor and legacy JSON and discards delayed validation",async()=>{
  const slow=deferred(),ui=runtime("workflow-tools.js",async path=>path.endsWith("/validate")?slow.promise:[]);
  await loadJson(ui,branchDefinition());ui.element("wf-name").value="A private definition";ui.element("workflow-input").value='{"secret":"A"}';
  ui.element("wf-check").click();await Promise.resolve();ui.transition();
  slow.resolve({valid:true,order:["A private node"]});await ui.flush();
  assert.equal(ui.element("wf-name").value,"");assert.equal(ui.element("workflow-name").value,"");
  assert.deepEqual(JSON.parse(ui.element("workflow-nodes").value),[]);assert.deepEqual(JSON.parse(ui.element("workflow-edges").value),[]);
  assert.equal(ui.element("workflow-input").value,"");assert.doesNotMatch(ui.element("wf-result").textContent,/private/);
  assert.equal(ui.element("wf-canvas").querySelectorAll("svg").length,0);
});

const builtin={name:"文本模板",operation:"text.template",description:"安全字段替换",input_schema:{type:"object"},example:{template:"{{value}}",values:{value:"合成值"}}};
const skill={id:"skill-a",project_id:"A",name:"A template",operation:"text.template",description:"用途",defaults:{template:"{{value}}"},creator:"local",version:1,publication_status:"DRAFT"};
function skillResponse(path){if(path.endsWith("/builtins"))return[builtin];if(path.endsWith("/skills"))return[skill];return[];}

test("skill invocation sends the selected version and renders returned text safely",async()=>{
  const ui=runtime("skill-tools.js",async(path,options)=>path.endsWith("/invoke")?{status:"SUCCEEDED",result:{text:"<img src=x onerror=alert(1)>"},arguments:options.body.arguments}:skillResponse(path));
  await ui.refresh();ui.element("sk-selected").value=skill.id;ui.element("sk-arguments").value=JSON.stringify({values:{value:"合成输出"}});
  await ui.click("sk-invoke");
  const call=ui.calls.find(item=>item.path.endsWith("/invoke"));assert.equal(call.path,"/api/skills/skill-a/invoke");assert.equal(call.options.body.arguments.values.value,"合成输出");
  assert.match(ui.element("sk-result").textContent,/<img/);assert.equal(ui.element("sk-result").querySelector("img"),null);assert.deepEqual(ui.errors,[]);
});

test("agent uses explicitly checked grants and reset blocks a delayed prior-project result",async()=>{
  const slow=deferred(),ui=runtime("skill-tools.js",async path=>path.endsWith("/agents/run")?slow.promise:skillResponse(path));
  await ui.refresh();const check=ui.element("sk-grants").querySelector("input");check.checked=true;check.dispatchEvent(new ui.window.Event("change"));
  ui.element("sk-goal").value="A confidential task";ui.element("sk-max-steps").value="2";ui.element("sk-agent-input").value='{"value":"A secret"}';
  ui.element("sk-agent-run").click();await Promise.resolve();
  const request=ui.calls.find(call=>call.path.endsWith("/agents/run"));assert.deepEqual(request.options.body.skill_ids,["skill-a"]);assert.equal(request.options.body.max_steps,2);
  ui.transition();slow.resolve({status:"SUCCEEDED",goal:"A confidential result"});await ui.flush();
  assert.equal(ui.element("sk-goal").value,"");assert.equal(ui.element("sk-agent-input").value,"");assert.equal(ui.element("sk-grants").children.length,0);
  assert.doesNotMatch(ui.element("sk-agent-result").textContent,/confidential/);assert.equal(ui.element("sk-history").children.length,0);
});
