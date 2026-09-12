"use strict";

(() => {
  const A = window.ARD;
  if (!A) return;
  const labels = {input:"输入",clean:"清洗",filter:"筛选",select:"选择列",derive:"数值派生",train:"模型训练",predict:"模型预测",retrieve:"知识检索",llm:"模型对话",human:"人工审核",output:"输出",condition:"条件分支",variable:"设置变量",template:"文本模板",iterate:"有界逐行迭代",dataset_output:"封存数据版本"};
  const defaults = {
    input:{},output:{},clean:{operations:[{type:"drop_duplicates"}]},filter:{column:"x",op:"gt",value:0},select:{columns:["x"]},
    derive:{source:"x",column:"scaled",scale:1,offset:0},train:{target:"label",task:"classification",features:["x"]},predict:{model_id:""},
    retrieve:{query:"质量检测",top_k:5,mode:"keyword"},llm:{prompt:"概述输入中的实际证据"},human:{message:"请复核输入证据"},
    condition:{path:"enabled",op:"eq",value:true},variable:{values:{batch:"B01"}},template:{template:"批次 {{variables.batch}}",target:"text"},
    iterate:{items_path:"rows",max_items:100,template:"样本 {{index}}：{{item.x}}",target:"summary"},dataset_output:{name:"工作流输出数据",tags:[]}
  };
  const tips = {
    condition:"path 是点分隔字段路径；op 可选 eq、ne、gt、ge、lt、le、contains、exists、truthy。成立和不成立连线都必须连接。",
    variable:'values 存放字面量；引用输入请写 {"batch":{"$path":"batch"}}，后续通过 variables.batch 读取。',
    template:"仅支持 {{字段路径}}。目标字段默认 text。不能执行表达式或访问对象属性。",
    iterate:"逐个处理 items_path 指向的对象列表，最多100项，超限直接失败。operations 使用数据清洗操作；模板可读 item、index、variables。",
    dataset_output:"将输入 rows 封存为新数据版本，保留 asset_id 来源。新版本仍需转库审批后才能训练。",
    train:"只能训练已审批数据版本。变换后的 rows 需要先封存并审批。",
    llm:"调用服务器实际配置的模型；未配置时任务明确失败。",
    human:"运行到此节点会保存证据并等待审批。审批驳回不能通过重试绕过。"
  };
  const panel = A.panel("agents", "workflow-tools", "可视化流程编辑与检查点", `
    <p>节点和连线直接对应下方工作流 JSON。拖动节点条目或使用 Alt + ↑ / ↓ 调整顺序；连线决定实际执行路径。</p>
    <div class="action-bar"><button id="wf-new" class="button ghost" type="button">新建空白流程</button><button id="wf-read-json" class="button ghost" type="button">从现有 JSON 载入</button><button id="wf-check" class="button secondary" type="button">服务器校验</button><button id="wf-save-version" class="button primary" type="button">保存此流程版本</button></div>
    <div class="form-grid"><label class="field">版本名称<input id="wf-name" maxlength="150"></label><label class="field">选择已有版本<select id="wf-version"></select></label></div>
    <div class="action-bar"><button id="wf-load-version" class="button ghost" type="button">载入所选版本</button><button id="wf-history" class="button ghost" type="button">查看版本历史</button><button id="wf-export" class="button ghost" type="button">导出所选版本</button><label class="field">导入流程包<input id="wf-import-file" type="file" accept=".json,application/json"></label><button id="wf-import" class="button ghost" type="button">导入并保存新版本</button></div>
    <p id="wf-version-status" role="status">尚未载入版本</p>
    <div id="wf-canvas" style="overflow:auto;max-height:520px;border:1px solid var(--border,#d8dfe8);border-radius:12px" aria-label="实际流程图"></div>
    <div class="form-grid"><label class="field">新增节点类型<select id="wf-add-type"></select></label><label class="field">新节点标识<input id="wf-add-id" maxlength="80" placeholder="例如 condition1"></label></div>
    <button id="wf-add-node" class="button secondary" type="button">添加节点</button>
    <div id="wf-node-list" class="micro-list" aria-label="可排序节点"></div>
    <div class="form-grid"><label class="field">选中节点标识<input id="wf-node-id" maxlength="80"></label><label class="field">选中节点类型<select id="wf-node-type"></select></label></div>
    <p id="wf-node-tip"></p><label class="field">节点参数 JSON<textarea id="wf-node-params" rows="6" spellcheck="false"></textarea></label>
    <div class="action-bar"><button id="wf-param-example" class="button ghost" type="button">填入此类型参数示例</button><button id="wf-apply-node" class="button secondary" type="button">应用节点配置</button><button id="wf-remove-node" class="button danger-ghost" type="button">删除选中节点及其连线</button></div>
    <div class="form-grid"><label class="field">连线起点<select id="wf-edge-source"></select></label><label class="field">连线终点<select id="wf-edge-target"></select></label><label class="field">条件分支<select id="wf-edge-when"><option value="">普通连线</option><option value="true">条件成立</option><option value="false">条件不成立</option></select></label></div>
    <button id="wf-connect" class="button secondary" type="button">添加连线</button><div id="wf-edge-list" class="micro-list"></div>
    <pre id="wf-result" class="result-box" tabindex="0">校验或版本结果将在这里显示</pre>
    <div class="divider"></div><h3>运行控制与完整日志</h3><p>暂停为协作式：正在执行的CPU计算或网络请求需要到下一个检查点才会暂停。已完成节点会持久保存；重试继续同一任务。</p>
    <button id="wf-jobs-refresh" class="button ghost" type="button">刷新运行记录</button><div id="wf-control-jobs" class="job-panels"></div>
  `);
  const $ = id => panel.querySelector("#" + id);
  const clone = value => JSON.parse(JSON.stringify(value));
  let definition = {nodes:[],edges:[]}, selected = "", parentId = "", dragId = "", flows = [];
  const button = (text, task, className="button ghost compact") => {
    const element = A.el("button", className, text); element.type="button";
    element.addEventListener("click", () => A.run(element, task)); return element;
  };
  const json = (value,label) => {try{return JSON.parse(value);}catch(_){throw new Error(label+"不是有效 JSON");}};
  function object(value,label){if(!value||typeof value!=="object"||Array.isArray(value))throw new Error(label+"必须为对象");return value;}
  function sync(){
    const nodes=document.getElementById("workflow-nodes"),edges=document.getElementById("workflow-edges"),name=document.getElementById("workflow-name");
    if(nodes)nodes.value=JSON.stringify(definition.nodes,null,2);
    if(edges)edges.value=JSON.stringify(definition.edges,null,2);
    if(name)name.value=$("wf-name").value;
  }
  function readDefinition(value){
    object(value,"流程");
    if(!Array.isArray(value.nodes)||!Array.isArray(value.edges)||value.nodes.length>40||value.edges.length>100)throw new Error("需要不超过40个节点和100条连线的数组");
    const names=new Set();
    for(const n of value.nodes){object(n,"节点");if(typeof n.id!=="string"||!n.id.trim()||n.id.length>80||names.has(n.id)||!Object.hasOwn(labels,n.type))throw new Error("节点标识重复、无效或类型未知");names.add(n.id);if(n.params!==undefined)object(n.params,"参数");if(Object.keys(n).some(k=>!["id","type","params"].includes(k)))throw new Error("节点只接受 id、type、params");}
    for(const e of value.edges){object(e,"连线");if(!names.has(e.source)||!names.has(e.target)||Object.keys(e).some(k=>!["source","target","when"].includes(k))||e.when!==undefined&&typeof e.when!=="boolean")throw new Error("连线引用或字段无效");}
    return clone({nodes:value.nodes,edges:value.edges});
  }
  function setSelected(id){selected=id;const item=definition.nodes.find(n=>n.id===id);$("wf-node-id").value=item?.id||"";$("wf-node-type").value=item?.type||"input";$("wf-node-params").value=item?JSON.stringify(item.params||{},null,2):"";$("wf-node-tip").textContent=item?(tips[item.type]||"参数示例可以修改；保存前服务器将验证具体输入。"):"选择图中节点或节点条目进行配置。";}
  function move(id,delta){const index=definition.nodes.findIndex(n=>n.id===id),next=index+delta;if(index<0||next<0||next>=definition.nodes.length)return;const [node]=definition.nodes.splice(index,1);definition.nodes.splice(next,0,node);render();sync();}
  function svgElement(tag,attributes,text){const el=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [key,value] of Object.entries(attributes||{}))el.setAttribute(key,String(value));if(text!==undefined)el.textContent=text;return el;}
  function draw(){
    const box=$("wf-canvas");box.replaceChildren();
    if(!definition.nodes.length){box.append(A.el("p","empty","尚无节点。可新建流程、读取JSON或载入已保存版本。"));return;}
    const height=Math.max(160,Math.ceil(definition.nodes.length/3)*130+30),svg=svgElement("svg",{viewBox:`0 0 840 ${height}`,role:"group","aria-label":"当前节点与实际连线",style:"width:100%;min-width:520px;display:block"});
    const defs=svgElement("defs"),marker=svgElement("marker",{id:"wf-arrow",viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:6,markerHeight:6,orient:"auto-start-reverse"});marker.append(svgElement("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:"#66758f"}));defs.append(marker);svg.append(defs);
    const positions=new Map(definition.nodes.map((n,i)=>[n.id,{x:20+(i%3)*280,y:25+Math.floor(i/3)*130}]));
    for(const edge of definition.edges){const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)continue;const x1=a.x+120,y1=a.y+64,x2=b.x+120,y2=b.y,color=edge.when===true?"#16825d":edge.when===false?"#c66c23":"#66758f";svg.append(svgElement("path",{d:`M${x1},${y1} C${x1},${y1+40} ${x2},${y2-40} ${x2},${y2}`,fill:"none",stroke:color,"stroke-width":2,"marker-end":"url(#wf-arrow)"}));if(edge.when!==undefined)svg.append(svgElement("text",{x:(x1+x2)/2+8,y:(y1+y2)/2,fill:color,"font-size":12},edge.when?"成立":"不成立"));}
    for(const n of definition.nodes){const p=positions.get(n.id),g=svgElement("g",{tabindex:0,role:"button","aria-label":`${n.id}，${labels[n.type]}；按回车编辑，Alt加上下箭头排序`,style:"cursor:pointer"});g.append(svgElement("rect",{x:p.x,y:p.y,width:240,height:64,rx:10,fill:n.id===selected?"#e7efff":"#f8fafc",stroke:n.id===selected?"#285bc4":"#aab7c9","stroke-width":1.5}));g.append(svgElement("text",{x:p.x+12,y:p.y+25,fill:"#16253d","font-size":15},n.id.length>22?n.id.slice(0,21)+"…":n.id));g.append(svgElement("text",{x:p.x+12,y:p.y+47,fill:"#526179","font-size":12},labels[n.type]));g.addEventListener("click",()=>{setSelected(n.id);draw();});g.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();setSelected(n.id);draw();}if(event.altKey&&["ArrowUp","ArrowDown"].includes(event.key)){event.preventDefault();move(n.id,event.key==="ArrowUp"?-1:1);}});svg.append(g);}
    box.append(svg);
  }
  function render(){
    draw();const list=$("wf-node-list");list.replaceChildren();
    definition.nodes.forEach((n,index)=>{const row=A.el("div","action-bar");row.draggable=true;row.tabIndex=0;row.setAttribute("aria-label",`${index+1} ${n.id}；Alt加上下箭头排序`);row.append(button(`${index+1}. ${n.id} · ${labels[n.type]}`,async()=>{setSelected(n.id);draw();}),button("上移",async()=>move(n.id,-1)),button("下移",async()=>move(n.id,1)));row.addEventListener("dragstart",event=>{dragId=n.id;event.dataTransfer?.setData("text/plain",n.id);});row.addEventListener("dragover",event=>event.preventDefault());row.addEventListener("drop",event=>{event.preventDefault();const from=definition.nodes.findIndex(item=>item.id===dragId),to=definition.nodes.findIndex(item=>item.id===n.id);if(from>=0&&to>=0){const [item]=definition.nodes.splice(from,1);definition.nodes.splice(to,0,item);dragId="";render();sync();}});row.addEventListener("keydown",event=>{if(event.altKey&&["ArrowUp","ArrowDown"].includes(event.key)){event.preventDefault();move(n.id,event.key==="ArrowUp"?-1:1);}});list.append(row);});
    for(const id of ["wf-edge-source","wf-edge-target"]){const select=$(id),old=select.value;select.replaceChildren(A.option("","请选择节点"));definition.nodes.forEach(n=>select.append(A.option(n.id,`${n.id} · ${labels[n.type]}`)));if(definition.nodes.some(n=>n.id===old))select.value=old;}
    const edges=$("wf-edge-list");edges.replaceChildren();definition.edges.forEach((e,index)=>{const row=A.el("div","action-bar");row.append(A.el("span","",`${e.source} → ${e.target}${e.when===undefined?"":e.when?" · 条件成立":" · 条件不成立"}`),button("删除连线",async()=>{definition.edges.splice(index,1);render();sync();}));edges.append(row);});
  }
  function selectFlow(context){const flow=flows.find(f=>f.id===$("wf-version").value);if(!flow||flow.project_id!==context.pid)throw new Error("请选择当前项目的工作流版本");return flow;}
  function bind(id,task){const el=$(id);el.addEventListener("click",()=>A.run(el,task));}
  for(const id of ["wf-add-type","wf-node-type"])Object.entries(labels).forEach(([key,label])=>$(id).append(A.option(key,label)));
  $("wf-name").addEventListener("input",sync);
  $("wf-node-type").addEventListener("change",()=>{$("wf-node-tip").textContent=tips[$("wf-node-type").value]||"选择填入参数示例，再编辑具体内容。";});
  $("wf-edge-source").addEventListener("change",()=>{$("wf-edge-when").value=definition.nodes.find(n=>n.id===$("wf-edge-source").value)?.type==="condition"?"true":"";});
  bind("wf-new",async()=>{definition={nodes:[{id:"input",type:"input"},{id:"output",type:"output"}],edges:[{source:"input",target:"output"}]};parentId="";$("wf-name").value="新工作流";$("wf-version-status").textContent="新流程；保存后生成独立版本";setSelected("input");render();sync();});
  bind("wf-read-json",async()=>{definition=readDefinition({nodes:json(document.getElementById("workflow-nodes").value,"节点"),edges:json(document.getElementById("workflow-edges").value,"连线")});$("wf-name").value=document.getElementById("workflow-name").value;parentId="";setSelected(definition.nodes[0]?.id||"");render();sync();});
  bind("wf-add-node",async()=>{if(definition.nodes.length>=40)throw new Error("最多40个节点");const type=$("wf-add-type").value,id=$("wf-add-id").value.trim()||`${type}${definition.nodes.length+1}`;if(definition.nodes.some(n=>n.id===id))throw new Error("节点标识已存在");definition.nodes.push({id,type,params:clone(defaults[type])});setSelected(id);render();sync();$("wf-add-id").value="";});
  bind("wf-param-example",async()=>{$("wf-node-params").value=JSON.stringify(defaults[$("wf-node-type").value],null,2);});
  bind("wf-apply-node",async()=>{const node=definition.nodes.find(n=>n.id===selected);if(!node)throw new Error("请选择节点");const id=$("wf-node-id").value.trim(),type=$("wf-node-type").value,params=object(json($("wf-node-params").value,"节点参数"),"节点参数");if(!id||definition.nodes.some(n=>n.id===id&&n!==node))throw new Error("节点标识不能为空或重复");for(const edge of definition.edges){if(edge.source===selected){edge.source=id;if(type!=="condition")delete edge.when;}if(edge.target===selected)edge.target=id;}Object.assign(node,{id,type,params});setSelected(id);render();sync();});
  bind("wf-remove-node",async()=>{if(!selected)throw new Error("请选择节点");definition.nodes=definition.nodes.filter(n=>n.id!==selected);definition.edges=definition.edges.filter(e=>e.source!==selected&&e.target!==selected);setSelected("");render();sync();});
  bind("wf-connect",async()=>{const source=$("wf-edge-source").value,target=$("wf-edge-target").value;if(!source||!target||source===target||definition.edges.some(e=>e.source===source&&e.target===target))throw new Error("请选择不同节点且不要重复连线");if(definition.edges.length>=100)throw new Error("最多100条连线");const edge={source,target};if(definition.nodes.find(n=>n.id===source)?.type==="condition"){if(!$("wf-edge-when").value)throw new Error("条件节点请选择成立或不成立");edge.when=$("wf-edge-when").value==="true";}definition.edges.push(edge);render();sync();});
  const body = () => ({name:$("wf-name").value.trim(),...clone(definition)});
  bind("wf-check",async context=>{$("wf-result").textContent=JSON.stringify(await A.request(`/api/projects/${context.pid}/workflows/validate`,{method:"POST",body:body()},context),null,2);});
  bind("wf-load-version",async context=>{const flow=selectFlow(context);definition=readDefinition(flow);parentId=flow.id;$("wf-name").value=flow.name;$("wf-version-status").textContent=`已载入 ${flow.id}；保存将创建其子版本`;setSelected(definition.nodes[0]?.id||"");render();sync();});
  bind("wf-save-version",async context=>{const payload=body();const result=await A.request(parentId?`/api/workflows/${parentId}/versions`:`/api/projects/${context.pid}/workflows/import`,{method:"POST",body:parentId?payload:{format:"ard-workflow-v1",workflow:payload}},context);await A.refresh(context);parentId=result.id;$("wf-version").value=result.id;const base=document.getElementById("workflow-select");if(base)base.value=result.id;$("wf-version-status").textContent=`已保存不可变版本 ${result.id}`;$("wf-result").textContent=JSON.stringify(result,null,2);});
  bind("wf-history",async context=>{const flow=selectFlow(context);$("wf-result").textContent=JSON.stringify(await A.request(`/api/workflows/${flow.id}/versions`,{},context),null,2);});
  bind("wf-export",async context=>{const flow=selectFlow(context);A.saveBlob(await A.request(`/api/workflows/${flow.id}/export`,{responseType:"blob"},context),`workflow-${flow.id}.json`);});
  bind("wf-import",async context=>{const file=$("wf-import-file").files[0];if(!file||file.size>2*1024*1024)throw new Error("请选择不超过2MiB的流程JSON包");const payload=json(await file.text(),"流程包");if(!A.current(context))return;const result=await A.request(`/api/projects/${context.pid}/workflows/import`,{method:"POST",body:payload},context);await A.refresh(context);$("wf-version").value=result.id;$("wf-result").textContent=JSON.stringify(result,null,2);$("wf-import-file").value="";});
  async function refreshJobs(context){
    const jobs=await A.request(`/api/projects/${context.pid}/jobs`,{},context);const list=$("wf-control-jobs");list.replaceChildren();
    for(const job of jobs.filter(j=>j.payload?.kind==="workflow").slice(0,30)){
      const card=A.el("article","job-panel");card.append(A.el("strong","",`${job.id.slice(0,10)} · ${job.status}${job.pause_requested&&job.status==="RUNNING"?" · 已请求暂停，等待检查点":""}`));
      const actions=A.el("div","action-bar"),names={pause:"请求暂停",resume:"继续",retry:"从检查点重试",cancel:"取消任务"},allowed=[];
      if(["QUEUED","RUNNING"].includes(job.status)&&!job.pause_requested)allowed.push("pause");if(job.status==="PAUSED")allowed.push("resume");if(["FAILED","INTERRUPTED"].includes(job.status))allowed.push("retry");if(["QUEUED","RUNNING","WAITING_APPROVAL","PAUSED"].includes(job.status))allowed.push("cancel");
      for(const action of allowed)actions.append(button(names[action],async captured=>{if(job.project_id!==captured.pid)throw new Error("项目已切换，请刷新记录");const fresh=await A.request(`/api/jobs/${job.id}`,{},captured);await A.request(`/api/jobs/${job.id}/${action}`,{method:"POST",body:{expected_revision:fresh.revision}},captured);await A.refresh(captured);}));
      const details=A.el("details"),summary=A.el("summary","","查看完整日志、检查点和结果"),pre=A.el("pre","result-box",JSON.stringify({progress:job.progress,retry_count:job.retry_count,error:job.error,logs:job.logs,state:job.state,result:job.result},null,2));details.append(summary,pre);card.append(actions,details);list.append(card);
    }
    if(!list.children.length)list.append(A.el("p","empty","当前没有工作流运行记录"));
  }
  bind("wf-jobs-refresh",refreshJobs);
  A.register("workflow-tools",{
    async refresh(context){flows=[...A.data().workflows];const select=$("wf-version"),old=select.value;select.replaceChildren(A.option("","请选择版本"));flows.forEach(f=>select.append(A.option(f.id,`${f.name} · ${f.id.slice(0,8)}${f.parent_id?" · 子版本":""}`)));if(flows.some(f=>f.id===old))select.value=old;await refreshJobs(context);},
    reset(){definition={nodes:[],edges:[]};selected=parentId=dragId="";flows=[];panel.querySelectorAll("input,textarea").forEach(el=>el.value="");$("wf-version").replaceChildren(A.option("","请选择版本"));$("wf-add-type").value="input";$("wf-edge-when").value="";$("wf-result").textContent="尚无结果";$("wf-version-status").textContent="尚未载入版本";$("wf-control-jobs").replaceChildren();const input=document.getElementById("workflow-input");if(input)input.value="";setSelected("");render();sync();}
  });
  render();
})();
