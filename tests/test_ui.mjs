import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

class FakeClassList {
  constructor(owner) { this.owner = owner; }
  values() { return new Set(this.owner.className.split(/\s+/).filter(Boolean)); }
  write(values) { this.owner.className = [...values].join(" "); }
  add(...names) { const values=this.values(); names.forEach((x)=>values.add(x)); this.write(values); }
  remove(...names) { const values=this.values(); names.forEach((x)=>values.delete(x)); this.write(values); }
  toggle(name, force) { const values=this.values(), enabled=force ?? !values.has(name); if(enabled)values.add(name);else values.delete(name);this.write(values);return enabled; }
  contains(name) { return this.values().has(name); }
}

class FakeElement {
  constructor(tag="div", id="") { this.tagName=tag.toUpperCase();this.id=id;this.className="";this.children=[];this.dataset={};this.style={};this.value="";this.hidden=false;this.disabled=false;this.listeners={};this.classList=new FakeClassList(this);this._text=""; }
  set textContent(value) { this._text=String(value??"");this.children=[]; }
  get textContent() { return this._text+this.children.map((x)=>x.textContent||"").join(""); }
  get options() { return this.children; }
  append(...items) { items.forEach((x)=>this.children.push(typeof x==="string"?new FakeText(x):x)); }
  replaceChildren(...items) { this.children=[];this._text="";this.append(...items); }
  remove() {}
  addEventListener(name, callback) { (this.listeners[name]??=[]).push(callback); }
  setAttribute() {}
  removeAttribute() {}
  showModal() { this.open=true; }
  close() { this.open=false; }
  click() { for(const listener of this.listeners.click||[])listener({target:this}); }
  reset() {}
}
class FakeText extends FakeElement { constructor(text){super("#text");this._text=text;} }

function deferred() { let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject}; }
function json(value,status=200) { return new Response(JSON.stringify(value),{status,headers:{"content-type":"application/json"}}); }

function runtime(fetchImpl=async()=>json([])) {
  const elements=new Map();
  const document={
    visibilityState:"visible",
    getElementById(id){if(!elements.has(id))elements.set(id,new FakeElement("div",id));return elements.get(id);},
    createElement(tag){return new FakeElement(tag);},
    querySelectorAll(){return[];},
    querySelector(){return new FakeElement("div");},
    addEventListener(){},
    body:new FakeElement("body")
  };
  const values=new Map();
  const sessionStorage={getItem:(key)=>values.get(key)||null,setItem:(key,value)=>values.set(key,String(value)),removeItem:(key)=>values.delete(key)};
  let exposed;
  const context={document,sessionStorage,fetch:fetchImpl,Headers,FormData,Response,Blob,URL,console,
    window:{setTimeout(){},setInterval(){return 1}},__expose(value){exposed=value;}};
  vm.createContext(context);
  const source=readFileSync(new URL("../ard/static/app.js",import.meta.url),"utf8")+
    `\n__expose({state,selectDataset,loadDatasets,loadJobs,loadDeployments,previewWorkflow,changeIdentity,predict,`+
    `resetIdentityBoundary:typeof resetIdentityBoundary === "function"?resetIdentityBoundary:undefined,`+
    `captureProject:typeof captureProject === "function"?captureProject:undefined});`;
  vm.runInContext(source,context);
  return{...exposed,elements,document};
}

test("a delayed dataset response cannot replace the newly selected project", async()=>{
  const slow=deferred();
  const ui=runtime(async(url)=>url.includes("/projects/A/datasets")?slow.promise:json({rows:[],total:0}));
  ui.state.projectId="A";
  const pending=ui.loadDatasets();
  ui.state.projectId="B";
  slow.resolve(json([{id:"asset-a",project_id:"A",name:"A secret",row_count:1,columns:["x"]}]));
  await pending.catch(()=>{});
  assert.equal(ui.state.datasets.length,0,"stale project A rows must be discarded");
});

for (const status of [200,400]) {
  test(`late dataset detail ${status} cannot overwrite another selection in the same project`,async()=>{
    const slow=deferred();
    const ui=runtime(async(url)=>url.includes('/api/assets/A/rows')?slow.promise:json({rows:[{value:'B evidence'}],total:1}));
    Object.assign(ui.state,{projectId:'P',datasets:['A','B'].map((id)=>({id,project_id:'P',name:`Dataset ${id}`,columns:['value'],row_count:1,sealed:true}))});
    const first=ui.selectDataset('A');
    await ui.selectDataset('B');
    slow.resolve(status===200?json({rows:[{value:'A evidence'}],total:1}):json({detail:'A request failed'},400));
    await first;
    assert.equal(ui.state.datasetId,'B');
    assert.equal(JSON.stringify(ui.state.rows),JSON.stringify([{value:'B evidence'}]));
    assert.equal(ui.elements.get('dataset-title').textContent,'Dataset B');
    assert.match(ui.elements.get('dataset-preview').textContent,/B evidence/);
  });
}

test("identity reset removes all prior identity and project data",()=>{
  const ui=runtime();
  Object.assign(ui.state,{me:{user:"alice"},projects:[{id:"A"}],projectId:"A",overview:{counts:{}},datasets:[{id:"d"}],datasetId:"d",rows:[{secret:1}],approvals:[{id:"a"}],jobs:[{id:"j"}],models:[{id:"m"}],modelId:"m",deployments:[{id:"p"}],documents:[{id:"doc"}],workflows:[{id:"w"}],audits:[{seq:1}]});
  assert.equal(typeof ui.resetIdentityBoundary,"function","identity boundary reset must exist");
  ui.resetIdentityBoundary();
  for(const key of ["projects","datasets","rows","approvals","jobs","models","deployments","documents","workflows","audits"])assert.equal(ui.state[key].length,0,`${key} should be empty`);
  assert.equal(ui.state.me,null);assert.equal(ui.state.projectId,"");assert.equal(ui.state.datasetId,"");assert.equal(ui.state.modelId,"");
  assert.equal(ui.elements.get("dataset-title").textContent,"请选择数据集");
  assert.match(ui.elements.get("prediction-result").textContent,/预测结果/);
});

test("a valid credential switch cannot reveal a delayed prior identity response",async()=>{
  const prior=deferred();
  const ui=runtime(async(url)=>{
    if(url.includes("/projects/A/datasets"))return prior.promise;
    if(url==="/api/me")return json({user:"bob",role:"viewer",mode:"token",projects:[]});
    if(url==="/api/projects"||url.startsWith("/api/audit"))return json([]);
    if(url==="/api/overview")return json({counts:{},jobs:[],resources:{gpu:{status:"not_connected"}}});
    if(url==="/api/connectors")return json({});
    return json([]);
  });
  Object.assign(ui.state,{token:"alice-token",projectId:"A",datasets:[]});
  const staleLoad=ui.loadDatasets();
  await ui.changeIdentity("bob-token");
  prior.resolve(json([{id:"alice-secret",project_id:"A",name:"secret",row_count:1,columns:["secret"]}]));
  await staleLoad.catch(()=>{});
  assert.equal(ui.state.me.user,"bob");assert.equal(ui.state.projectId,"");assert.equal(ui.state.datasets.length,0);
  assert.equal(ui.elements.get("dataset-title").textContent,"请选择数据集");
});

test("an invalid replacement credential clears prior visible data before failure",async()=>{
  const ui=runtime(async()=>json({detail:"未授权"},401));
  Object.assign(ui.state,{token:"alice-token",me:{user:"alice"},projectId:"A",datasets:[{id:"secret"}],rows:[{secret:true}],models:[{id:"model"}]});
  await ui.changeIdentity("invalid").catch(()=>{});
  assert.equal(ui.state.me,null);assert.equal(ui.state.projectId,"");assert.equal(ui.state.datasets.length,0);assert.equal(ui.state.rows.length,0);assert.equal(ui.state.models.length,0);
  assert.equal(ui.elements.get("dataset-title").textContent,"请选择数据集");
  assert.match(ui.elements.get("prediction-result").textContent,/预测结果/);
});

test("job status transition refreshes the actual overview",async()=>{
  const calls=[];
  const ui=runtime(async(url)=>{calls.push(url);if(url.includes("/jobs"))return json([{id:"j",project_id:"A",status:"SUCCEEDED",payload:{kind:"workflow"},logs:[]}]);if(url==="/api/overview")return json({counts:{},jobs:[],resources:{gpu:{status:"not_connected"}}});return json([]);});
  ui.state.projectId="A";ui.state.jobs=[{id:"j",status:"RUNNING",payload:{kind:"workflow"}}];
  await ui.loadJobs();
  assert.ok(calls.includes("/api/overview"),"overview must refresh when observed job status changes");
});

test("expired ACTIVE deployments are not offered for prediction",async()=>{
  const ui=runtime(async()=>json([{id:"expired",name:"old",status:"ACTIVE",expires_at:"2000-01-01T00:00:00Z"},{id:"live",name:"live",status:"ACTIVE",expires_at:"2999-01-01T00:00:00Z"}]));
  ui.state.projectId="A";
  await ui.loadDeployments();
  assert.deepEqual(ui.elements.get("prediction-deployment").options.map((x)=>x.value),["","live"]);
});

test("a 410 prediction response refreshes and removes the deployment",async()=>{
  const calls=[];
  const ui=runtime(async(url)=>{calls.push(url);if(url.includes("/predict"))return json({detail:"模型服务已停用或超过运行时限"},410);if(url.includes("/deployments"))return json([]);return json([]);});
  const deployment={id:"dep",project_id:"A",name:"live",status:"ACTIVE",expires_at:"2999-01-01T00:00:00Z",revision:1};
  Object.assign(ui.state,{projectId:"A",deployments:[deployment]});
  ui.document.getElementById("prediction-deployment").value="dep";
  ui.document.getElementById("prediction-rows").value='[{"x":1}]';
  await ui.predict();
  assert.ok(calls.some((x)=>x==="/api/projects/A/deployments"));
  assert.equal(ui.state.deployments.length,0);
  assert.deepEqual(ui.elements.get("prediction-deployment").options.map((x)=>x.value),[""]);
});

test("workflow preview rejects an edge whose endpoint is unknown",()=>{
  const ui=runtime();
  ui.document.getElementById("workflow-nodes").value=JSON.stringify([{id:"a",type:"input"},{id:"b",type:"output"}]);
  ui.document.getElementById("workflow-edges").value=JSON.stringify([{source:"a",target:"missing"}]);
  ui.previewWorkflow();
  const messages=ui.elements.get("toast-region").children.map((x)=>x.textContent);
  assert.ok(messages.some((x)=>x.includes("无效")||x.includes("未知")),"preview must report the invalid edge");
  assert.ok(!messages.some((x)=>x.includes("结构可读")));
});

test("workflow preview rejects a node disconnected from the input-output path",()=>{
  const ui=runtime();
  ui.document.getElementById("workflow-nodes").value=JSON.stringify([{id:"in",type:"input"},{id:"orphan",type:"human"},{id:"out",type:"output"}]);
  ui.document.getElementById("workflow-edges").value=JSON.stringify([{source:"in",target:"out"}]);
  ui.previewWorkflow();
  const messages=ui.elements.get("toast-region").children.map((x)=>x.textContent);
  assert.ok(messages.some((x)=>x.includes("未连接")));
  assert.ok(!messages.some((x)=>x.includes("结构校验通过")));
});
