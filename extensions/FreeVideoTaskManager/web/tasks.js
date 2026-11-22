import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { resolveLanguage, translate } from "./i18n.js";
const LANG_KEY="freevideo-task-manager.language";
let savedLanguage;try{savedLanguage=localStorage.getItem(LANG_KEY);}catch{}
let LANGUAGE=resolveLanguage(globalThis.location?.search,globalThis.navigator?.language,savedLanguage);
const t=key=>translate(key,LANGUAGE);

const BASE = new URL("../FreeVideo/", import.meta.url);
const load = name => import(new URL(name + ".js", BASE).href);
const HISTORY = "freevideo-task-manager.history.v1";
const LIMIT = 240;
const states = {running:"Em execução",cancelling:"Cancelando",queued:"Na fila",submitting:"Iniciando",done:"Concluída",failed:"Falhou",cancelled:"Cancelada",interrupted:"Interrompida"};
const agentStates = {waiting:"queued",queued:"queued",running:"running",cancelling:"cancelling",succeeded:"done",done:"done",completed:"done",failed:"failed",cancelled:"cancelled",canceled:"cancelled",interrupted:"interrupted"};
const groups = {all:"Todas",running:"Em execução",queued:"Na fila",done:"Concluídas",failed:"Falhas / canceladas"};
const group = s => ["running","cancelling"].includes(s) ? "running" : ["queued","submitting"].includes(s) ? "queued" : s === "done" ? "done" : "failed";
const number = n => typeof n === "number" && Number.isFinite(n);
const fraction = n => number(n) ? Math.max(0,Math.min(1,n)) : null;
const stamp = n => number(n) ? (n < 1e12 ? n*1000 : n) : Date.parse(n || "") || null;
const elapsed = n => !number(n) || n < 0 ? "—" : [Math.floor(n/3600),Math.floor(n/60)%60,Math.floor(n)%60].map(v=>String(v).padStart(2,"0")).join(":");
const gib = n => number(n) ? (n/1073741824).toFixed(1)+" GB" : "—";
const pct = (a,b) => number(a)&&number(b)&&b>0 ? 100*a/b : null;
const el = (tag, cls, txt) => {const x=document.createElement(tag); if(cls)x.className=cls; if(txt!=null)x.textContent=txt; return x;};
const button = (txt,fn,cls="") => {const b=el("button",cls,txt);b.type="button";b.onclick=fn;return b;};
let ui, sources, opening, observer, timer, tick, unsubscribe, agentUnsubscribe, agent, agentRaw=[], chains=[], journal=[], queueRows=[], hardware=null, samples=[], selected="all", scope="all", search="", error="", busy=new Set(), polling=false, pollEpoch=0, lastGood=0, nativeNav=null;
const rowElements=new Map();
let lastChart=null, lastChartTheme=null, healthKey="Conectando";
try { samples=JSON.parse(localStorage.getItem(HISTORY)||"[]").filter(s=>number(s.t)&&Date.now()-s.t<=86400000).slice(-LIMIT); } catch { samples=[]; }

async function get(path,options={}) {
  const r=await api.fetchApi(path,{cache:"no-store",signal:AbortSignal.timeout(6000),...options});
  if(!r.ok)throw new Error("HTTP "+r.status);
  return r.json();
}
const post = (path, body) => get(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
const visible = () => Boolean(ui?.open);
function sourceLabel(row) {
  if(row.kind!=="agent"&&!row.agentClient)return row.origin && row.origin!=="page" ? String(row.origin) : "FreeVideo";
  const name=String(row.agentClient||row.job?.client||"");
  return /mcp/i.test(name) ? "MCP" : /cli/i.test(name) ? "CLI" : name || t("Agente");
}
function rows() {
  const map=new Map((sources?.jobs.list()||[]).map(r=>[r.id,{...r}]));
  for(const r of journal) {
    if(!r.id||!["video","chain","image"].includes(r.kind))continue;
    const id=r.kind+":"+r.id;
    if(!map.has(id)) map.set(id,{...r,id,kind:r.kind,status:r.status,created_at:stamp(r.created_at),started_at:stamp(r.started_at),finished_at:stamp(r.finished_at),cancellable:false});
  }
  // The native queue adapter follows the current browser only. Cover other clients
  // using the server queue, matching progress only to a unique running node.
  for(const q of queueRows){
    const id="video:"+q.prompt_id, prev=map.get(id);
    if(prev&&["done","failed","cancelled","interrupted"].includes(prev.status))continue;
    const linked=agentRaw.find(j=>q.client==="agent:"+j.id);
    const externalCancel=!q.client?.startsWith("agent:")&&!q.chainId;
    map.set(id,{...prev,...q,id,kind:"video",agentClient:linked?.client||(q.client?.startsWith("agent:")?t("Agente"):null),externalCancel,cancellable:externalCancel||(prev?.cancellable===true&&Boolean(sources.jobs.get(id))),percent:q.percent??prev?.percent,phase:q.phase||prev?.phase,eta_seconds:q.eta_seconds??prev?.eta_seconds,started_at:q.started_at??prev?.started_at,created_at:q.created_at??prev?.created_at});
  }
  for(const c of chains) {
    if(!c.id)continue;
    const id="chain:"+c.id;
    const status=({pending:"queued",queued:"queued",running:"running",cancelling:"cancelling",done:"done",complete:"done",failed:"failed",cancelled:"cancelled",stopped:"cancelled",interrupted:"interrupted"})[c.state];
    if(!status)continue;
    const prev=map.get(id);
    map.set(id,{id,kind:"chain",created_at:stamp(c.created),percent:c.count?c.done/c.count:null,cancellable:["running","queued"].includes(status),...prev,status:c.output_state==="joining"?"running":status,chain:c});
  }
  for(const r of agent?.rows()||[])map.set(r.id,{...r,phase:r.job?.progress?.phase,percent:fraction(r.job?.progress?.fraction),created_at:stamp(r.job?.created_at),started_at:stamp(r.job?.started_at)});
  for(const j of agentRaw) {
    if(!j.id||!agentStates[j.status])continue;
    const id="agent:"+j.id, prev=map.get(id);
    map.set(id,{id,kind:"agent",agentId:j.id,status:agentStates[j.status],cancellable:false,job:j,phase:j.progress?.phase,percent:fraction(j.progress?.fraction),eta_seconds:j.progress?.remaining_seconds,created_at:stamp(j.created_at),started_at:stamp(j.started_at),finished_at:stamp(j.finished_at),...prev});
  }
  return [...map.values()].filter(r=>states[r.status]).sort((a,b)=>["running","queued","done","failed"].indexOf(group(a.status))-["running","queued","done","failed"].indexOf(group(b.status)) || (stamp(b.finished_at||b.created_at)||0)-(stamp(a.finished_at||a.created_at)||0));
}
function taskName(r) {return r.job?.label||r.model||({video:t("Geração de vídeo"),image:t("Geração de imagem"),chain:t("Vídeo em segmentos"),agent:t("Atividade do agente")})[r.kind]||r.kind;}
function phaseLabel(phase){if(!phase)return "";return String(phase).replace(/\bsampling\b/gi,t("Amostragem"));}
function canResume(r) {return r.kind==="chain"&&["failed","cancelled","interrupted"].includes(r.status)&&Boolean(r.chain);}
function canRetry(r) {return r.kind==="video"&&["failed","cancelled","interrupted"].includes(r.status)&&r.id.startsWith("video:");}
async function action(r,type) {
  if(busy.has(r.id))return;
  const question=type==="cancel"?t("Cancelar somente esta tarefa?"):type==="resume"?t("Retomar esta cadeia do último ponto aceito pelo FreeVideo?"):t("Reenfileirar o workflow original? Uma nova execução será criada.");
  if(!window.confirm(question))return;
  busy.add(r.id);error="";render();
  try {
    if(type==="cancel"){
      if(r.kind==="agent")await agent.cancel(r.agentId);
      else if(sources.jobs.get(r.id)?.cancellable===true){if(!await sources.jobs.cancel(r.id))throw Error(t("Cancelamento indisponível"));}
      else if(r.kind==="chain"&&r.chain)await post("/freevideo/chain/"+encodeURIComponent(r.id.slice(6))+"/cancel",{});
      else if(r.externalCancel&&r.prompt_id&&r.node){const reply=await post("/freevideo/cancel",{prompt_id:r.prompt_id,node_id:String(r.node)});if(!["cancelled","cancelling","finished"].includes(reply.status))throw Error(t("Cancelamento não confirmado"));}
      else throw Error(t("Cancelamento indisponível"));
    } else if(type==="resume"){
      await post("/freevideo/chain/"+encodeURIComponent(r.id.slice(6))+"/resume",{client_id:api.clientId});
    } else {
      const id=r.prompt_id||r.id.slice(6);
      const history=await get("/history/"+encodeURIComponent(id));
      const p=history[id]?.prompt;
      const nodes=Object.entries(p?.[2]||{}).filter(([,n])=>n.class_type==="FreeVideoGenerate");
      const node=nodes.find(([key])=>String(key)===String(r.node))|| (nodes.length===1?nodes[0]:null);
      if(!node)throw Error(t("Workflow original indisponível ou ambíguo; nenhuma tarefa foi enviada."));
      // Native helper clones the frozen workflow; no new seed is applied.
      const snapshot=sources.queue.seededPrompt({output:p[2],workflow:p[3]?.extra_pnginfo?.workflow||{}},node[0],-1,undefined);
      const reply=await api.queuePrompt(0,snapshot);
      if(!reply?.prompt_id)throw Error(t("Fila não confirmou a nova execução."));
      api.dispatchCustomEvent?.("promptQueued",{number:0,batchCount:1});
    }
    if(visible())await poll();
  } catch(e){error=String(e.message||e);} finally {busy.delete(r.id);render();}
}
function taskRow(r) {
  const tr=el("tr");const main=el("td");
  main.append(el("strong","",taskName(r)),el("small","",r.id.replace(/^[^:]+:/,"").slice(0,12)+" · "+({video:t("Vídeo"),image:t("Imagem"),chain:t("Cadeia"),agent:t("Agente")})[r.kind]));
  const st=el("td");st.append(el("span","badge "+group(r.status),t(states[r.status])));
  const origin=el("td");origin.append(el("span","origin",sourceLabel(r)));
  const progress=el("td");const f=r.status==="done"?1:fraction(r.percent);const bar=el("div","track");const fill=el("i");fill.style.width=(f===null?0:f*100)+"%";bar.append(fill);progress.append(el("strong","",f===null?"—":Math.round(f*100)+"%"),bar);
  const phase=el("td","",phaseLabel(r.phase||r.job?.progress?.phase)|| (r.status==="queued"&&number(r.position)&&r.position>0?t("Posição")+" "+r.position:"—"));
  const start=stamp(r.started_at||r.created_at), end=stamp(r.finished_at)||Date.now();
  const time=el("td","time");time.append(el("span","",t("Decorrido")+" "+(start?elapsed((end-start)/1000):"—")),el("small","",t("Restante")+" "+(group(r.status)==="running"&&number(r.eta_seconds)?"~"+elapsed(r.eta_seconds):"—")));
  const actions=el("td","actions");
  if(["running","queued","submitting"].includes(r.status)&&r.cancellable!==false&&(r.kind==="agent"||r.externalCancel||r.chain||sources.jobs.get(r.id)))actions.append(button(t("Cancelar"),()=>action(r,"cancel"),"danger"));
  if(canResume(r))actions.append(button(t("Retomar"),()=>action(r,"resume")));
  else if(canRetry(r))actions.append(button(t("Reexecutar"),()=>action(r,"retry")));
  for(const b of actions.children)b.disabled=busy.has(r.id);
  if(!actions.children.length)actions.textContent="—";
  if(r.failure?.code) main.append(el("small","failure",String(r.failure.code)));
  tr.append(main,st,origin,progress,phase,time,actions);return tr;
}
function render() {
  if(!visible()||!sources||document.hidden)return;
  const all=rows();
  for(const [key,value] of Object.entries(groups)){
    const c=ui.querySelector('[data-count="'+key+'"]');if(c)c.textContent=String(key==="all"?all.length:all.filter(r=>group(r.status)===key).length);
  }
  const q=search.trim().toLowerCase();
  const filtered=all.filter(r=>(selected==="all"||group(r.status)===selected)&&(scope==="all"|| (scope==="agent"?r.kind==="agent":r.kind!=="agent"))&&(!q||[taskName(r),r.id,sourceLabel(r),r.phase].some(x=>String(x||"").toLowerCase().includes(q))));
  const body=ui.querySelector("tbody"), keep=new Set(filtered.map(r=>r.id));
  for(const [id,entry] of rowElements){if(!keep.has(id)){entry.node.remove();rowElements.delete(id);}}
  filtered.forEach((r,index)=>{
    // The timer only updates the clock; it does not rebuild the table each second.
    const key=JSON.stringify([taskName(r),r.kind,r.status,sourceLabel(r),r.percent,r.phase,r.position,r.eta_seconds,r.cancellable,r.failure?.code,canResume(r),canRetry(r),busy.has(r.id)]);
    let entry=rowElements.get(r.id);
    if(!entry||entry.key!==key){const node=taskRow(r); if(entry){const focused=document.activeElement;const label=entry.node.contains(focused)?focused.textContent:null;entry.node.replaceWith(node);if(label) [...node.querySelectorAll("button")].find(b=>b.textContent===label)?.focus({preventScroll:true});}entry={key,node};rowElements.set(r.id,entry);}
    if(body.children[index]!==entry.node)body.insertBefore(entry.node,body.children[index]||null);
    const start=stamp(r.started_at||r.created_at),end=stamp(r.finished_at)||Date.now();
    entry.node.querySelector(".time span").textContent=t("Decorrido")+" "+(start?elapsed((end-start)/1000):"—");
  });
  ui.querySelector(".empty").hidden=filtered.length!==0;
  ui.querySelector(".message").textContent=error;
  ui.querySelector(".connection").textContent=lastGood?t("Atualizado")+" "+new Date(lastGood).toLocaleTimeString(LANGUAGE):t("Aguardando dados");
  renderHardware();
}
const metrics=[
  ["gpu","GPU",s=>number(s.gpu)?Math.round(s.gpu)+"%":"—",s=>s.gpu],
  ["vram","VRAM",s=>gib(s.vram_used)+" / "+gib(s.vram_total),s=>pct(s.vram_used,s.vram_total)],
  ["cpu","CPU",s=>number(s.cpu)?Math.round(s.cpu)+"%":"—",s=>s.cpu],
  ["ram","RAM",s=>gib(s.ram_used)+" / "+gib(s.ram_total),s=>pct(s.ram_used,s.ram_total)],
  ["temp","Temperatura GPU",s=>number(s.temp)?Math.round(s.temp)+" °C":"—",s=>s.temp],
  ["disk","Disco do engine",s=>gib(s.disk_free)+" "+t("livres"),s=>pct(s.disk_used,s.disk_total)]
];
function renderHardware(){
  if(lastChart===hardware&&lastChartTheme===ui.dataset.theme)return;
  lastChart=hardware;lastChartTheme=ui.dataset.theme;
  const h=hardware||{};
  ui.querySelector(".gpu-name").textContent=h.name||t("Hardware");
  for(const [id,label,format,measure] of metrics){
    const card=ui.querySelector('[data-metric="'+id+'"]');card.querySelector("strong").textContent=format(h);
    const p=measure(h);card.querySelector("i").style.width=number(p)?Math.max(0,Math.min(100,p))+"%":"0%";
    const canvas=card.querySelector("canvas"), ctx=canvas.getContext("2d");ctx.clearRect(0,0,canvas.width,canvas.height);
    const series=samples.map(measure);ctx.strokeStyle=getComputedStyle(card).getPropertyValue("--ft-accent").trim()||"#4285ff";ctx.lineWidth=2;ctx.beginPath();let begun=false;
    series.forEach((v,i)=>{if(!number(v)){begun=false;return;}const x=i*canvas.width/Math.max(1,series.length-1),y=canvas.height-2-Math.max(0,Math.min(100,v))*(canvas.height-4)/100;if(!begun){ctx.moveTo(x,y);begun=true;}else ctx.lineTo(x,y);});ctx.stroke();
  }
}
async function poll(){
  if(!visible()||document.hidden||polling)return;
  polling=true;const epoch=pollEpoch;
  try {
    const results=await Promise.allSettled([get("/freevideo/monitor"),get("/freevideo-task-manager/host"),get("/freevideo/jobs"),get("/freevideo/chains"),get("/queue"),get("/freevideo/progress")]);
    if(!visible()||epoch!==pollEpoch)return;
    const [gpu,host,j,c,q,p]=results.map(r=>r.status==="fulfilled"?r.value:null);
    if(j)journal=Array.isArray(j.jobs)?j.jobs:[];
    if(c)chains=Array.isArray(c.chains)?c.chains:[];
    if(q){
      const active=(q.queue_running||[]).map(r=>[r,"running"]), pending=(q.queue_pending||[]).map(r=>[r,"queued"]);
      const entries=[...active,...pending].flatMap(([r,status],position)=>{
        const nodes=Object.entries(r[2]||{}).filter(([,n])=>n.class_type==="FreeVideoGenerate");
        if(nodes.length!==1||!r[1])return [];
        const node=nodes[0][0];return [{prompt_id:r[1],node,status,client:r[3]?.client_id,chainId:r[3]?.freevideo_chain?.id,created_at:stamp(r[3]?.create_time),position}];
      });
      queueRows=entries.map(r=>{
        const matched=r.status==="running"&&entries.filter(x=>x.node===r.node&&x.status==="running").length===1?(p?.progress||[]).filter(m=>String(m.node)===r.node&&(!m.prompt_id||m.prompt_id===r.prompt_id)&&m.overall?.status==="running"&&!m.result).sort((a,b)=>(a.age_seconds??Infinity)-(b.age_seconds??Infinity))[0]:null;
        const o=matched?.overall;
        return {...r,phase:matched?.label||matched?.phase,percent:fraction(o?.fraction),eta_seconds:o?.remaining_seconds,started_at:number(o?.elapsed_seconds)?Date.now()-o.elapsed_seconds*1000:null};
      });
    } else queueRows=[];
    if(gpu||host){
      hardware={t:Date.now(),name:gpu?.name||"",gpu:gpu?.utilization,vram_used:gpu?.memory_used,vram_total:gpu?.memory_total,temp:gpu?.temperature,disk_free:gpu?.disk_free,...(host||{})};
      if(!number(hardware.disk_free)&&number(host?.disk_total)&&number(host?.disk_used))hardware.disk_free=host.disk_total-host.disk_used;
      samples.push(hardware);samples=samples.filter(s=>Date.now()-s.t<=86400000).slice(-LIMIT);
      try{localStorage.setItem(HISTORY,JSON.stringify(samples));}catch{}
      lastGood=Date.now();
    } else hardware=null;
    healthKey=results.every(r=>r.status==="fulfilled")?"Online":"Dados parciais";
    ui.querySelector(".health").textContent=t(healthKey);
  } finally {polling=false;render();}
}
const icons={sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4m0-14.2-1.4 1.4M6.3 17.7l-1.4 1.4"/>',moon:'<path d="M20.9 13.3A9 9 0 0 1 10.7 3.1 9 9 0 1 0 20.9 13.3Z"/>'};
// Keep native Settings behavior; replace its sun-like glyph with a toothed cog.
const cog='<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.08A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.08A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.08A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.08A1.65 1.65 0 0 0 19.4 15Z"/>';
const iconMask=path=>'url("data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+path+'</svg>')+'")';
const currentTheme=()=>document.documentElement.dataset.theme==="light"?"light":"dark";
const nextTheme=theme=>theme==="light"?"dark":"light";
function syncNativeSettingsTheme(){
  // The native segmented control hooks the select value setter to repaint.
  // Do not dispatch change: the global theme has already been applied.
  for(const select of document.querySelectorAll(".st-seg > select")){
    const values=Array.from(select.options,o=>o.value);
    if(values.length!==2||!values.includes("dark")||!values.includes("light"))continue;
    if(select.value!==currentTheme())select.value=currentTheme();
  }
}
function toggleTheme(){
  const theme=nextTheme(currentTheme());
  if(globalThis.FreeVideoShell?.setTheme)globalThis.FreeVideoShell.setTheme(theme);else document.documentElement.dataset.theme=theme;
  try{localStorage.setItem("freevideo.theme",theme);}catch{}
}
function syncNativeTheme(b){
  const dark=currentTheme()==="dark";
  const nativeLang=resolveLanguage(globalThis.location?.search,globalThis.navigator?.language)==="zh"?"zh":"en";
  const label=translate(dark?"Ativar tema claro":"Ativar tema escuro",nativeLang);
  b.style.setProperty("--fv-tb-icon",iconMask(icons[dark?"sun":"moon"]));
  b.setAttribute("aria-label",label);b.title=label;
  const text=b.querySelector(".fv-tb-lbl");if(text.textContent!==label)text.textContent=label;
}
const globe='<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>';
const worksGlyph='<rect x="4" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.6"/>';
const svgIcon=path=>'<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+path+'</svg>';
const nativeLanguage=()=>resolveLanguage(globalThis.location?.search,globalThis.navigator?.language)==="zh"?"zh":"en";
let languageMenuId=0;
function languageControl(native=false){
  const wrapper=el("div","ftm-language"+(native?" ftm-native-language":""));
  const trigger=button("",null,"language");
  const menu=el("div","language-menu");menu.id="ftm-language-menu-"+(++languageMenuId);menu.hidden=true;menu.setAttribute("role","group");
  trigger.setAttribute("aria-expanded","false");trigger.setAttribute("aria-controls",menu.id);
  trigger.innerHTML=svgIcon(globe)+'<span class="language-code"></span><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>';
  wrapper.append(trigger,menu);bindLanguageMenu(wrapper,native);return wrapper;
}
function attachNativeControls(){
  for(const settings of document.querySelectorAll(".fv-settings-entry")){
    if(!settings.closest(".fv-tb-links"))continue;
    if(!settings.classList.contains("ftm-settings-cog")){
      settings.classList.add("ftm-settings-cog");
      settings.style.setProperty("--fv-tb-icon",iconMask(cog));
    }
    const links=settings.parentElement;
    if(!links.querySelector(".ftm-native-language"))settings.before(languageControl(true));
    if(!links.querySelector(".ftm-native-theme")){
      const b=button("",toggleTheme,"fv-tb-link fv-tb-ico ftm-native-theme");
      b.append(el("span","fv-tb-lbl"));syncNativeTheme(b);
      settings.before(b);
    }
  }
}
function syncTheme(d){
  d.dataset.theme=document.documentElement.dataset.theme==="light"?"light":"dark";
  const b=d.querySelector(".theme"), dark=d.dataset.theme==="dark";
  const label=t(dark?"Ativar tema claro":"Ativar tema escuro");
  b.title=label;b.setAttribute("aria-label",label);
  b.innerHTML='<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[dark?"sun":"moon"]+'</svg>';
  if(visible())renderHardware();
}
function populate(d){
  rowElements.clear();lastChartTheme=null;
  d.innerHTML=`<header><div class="brand">FreeVideo <b>Task Manager</b><small> v0.1</small></div><nav></nav><button class="creations" type="button">${t("Criações")}</button><button class="theme" type="button">${t("Tema")}</button><button class="back" type="button">${t("Voltar")}</button></header><main><div class="hero"><div><p>${t("WORKSPACE / TASKS")}</p><h1>${t("Central de tarefas")}</h1><span>${t("Acompanhe a geração e os recursos do computador.")}</span></div><div><span class="health">${t(healthKey)}</span><small class="connection"></small></div></div><section class="counts"></section><section class="hardware"><div class="section-head"><div><h2>${t("Recursos do sistema")}</h2><small class="gpu-name"></small></div><small>${t("Histórico local · até 240 amostras")}</small></div><div class="metrics"></div></section><section class="tasks"><div class="section-head"><div><h2>${t("Atividade")}</h2><small>${t("Gerações e atividades do agente são registros distintos.")}</small></div><div class="filters"><select aria-label="${t("Origem")}"><option value="all">${t("Todas as origens")}</option><option value="engine">${t("Gerações")}</option><option value="agent">${t("Agente / MCP / CLI")}</option></select><input type="search" placeholder="${t("Buscar tarefa...")}" aria-label="${t("Buscar tarefa")}"></div></div><div class="message" role="status"></div><div class="table-wrap"><table><thead><tr><th>${t("Tarefa")}</th><th>${t("Status")}</th><th>${t("Origem")}</th><th>${t("Progresso")}</th><th>${t("Etapa")}</th><th>${t("Tempo")}</th><th>${t("Ações")}</th></tr></thead><tbody></tbody></table><p class="empty">${t("Nenhuma tarefa neste filtro.")}</p></div></section></main>`;
  const counts=d.querySelector(".counts");
  for(const [id,label] of Object.entries(groups)){const b=button("",()=>{selected=id;for(const c of counts.children)c.setAttribute("aria-pressed",String(c===b));render();},"count "+id);b.setAttribute("aria-pressed",String(id===selected));const n=el("strong","","0");n.dataset.count=id;b.append(el("span","",t(label)),n);counts.append(b);}
  for(const [id,label] of metrics){const c=el("article","metric");c.dataset.metric=id;c.append(el("span","",t(label)),el("strong","","—"));const tr=el("div","track");tr.append(el("i"));c.append(tr);const canvas=el("canvas");canvas.width=200;canvas.height=34;canvas.setAttribute("aria-hidden","true");c.append(canvas);d.querySelector(".metrics").append(c);}
  d.querySelector(".back").onclick=()=>d.close();
  d.querySelector(".theme").onclick=()=>{toggleTheme();syncTheme(d);};
  d.querySelector("input").oninput=e=>{search=e.target.value;render();};
  const filter=d.querySelector(".filters select");filter.value=scope;filter.onchange=e=>{scope=e.target.value;render();};
  d.querySelector("input").value=search;
  const creations=d.querySelector(".creations");creations.innerHTML=svgIcon(worksGlyph)+"<span>"+t("Criações")+"</span>";creations.title=t("Criações");creations.setAttribute("aria-label",t("Criações"));
  creations.onclick=()=>openNativeCreations(d);
  d.querySelector(".theme").before(languageControl());
}
const languageOptions=[["pt-BR","Português (Brasil)"],["en","English"],["zh","简体中文"]];
function setLanguageMenu(d,open,restoreFocus=false){
  const trigger=d.querySelector(".language"), menu=d.querySelector(".language-menu");
  if(!trigger||!menu)return;
  menu.hidden=!open;trigger.setAttribute("aria-expanded",String(open));
  if(restoreFocus)trigger.focus({preventScroll:true});
}
function syncLanguageControl(d,native=false){
  const translateHere=key=>native?translate(key,nativeLanguage()):t(key);
  const trigger=d.querySelector(".language"),menu=d.querySelector(".language-menu");
  trigger.setAttribute("aria-label",translateHere("Idioma"));trigger.title=translateHere("Idioma");menu.setAttribute("aria-label",translateHere("Idioma"));
  trigger.querySelector(".language-code").textContent=LANGUAGE==="zh"?"中文":LANGUAGE==="en"?"EN":"PT-BR";
  for(const choice of menu.querySelectorAll(".language-choice")){
    const on=choice.dataset.language===LANGUAGE;choice.setAttribute("aria-pressed",String(on));choice.querySelector(".check").textContent=on?"✓":"";
  }
}
function selectLanguage(value,native=false){
  if(!languageOptions.some(([id])=>id===value))return;
  const changed=value!==LANGUAGE;
  LANGUAGE=value;try{localStorage.setItem(LANG_KEY,LANGUAGE);}catch{}
  for(const control of document.querySelectorAll(".ftm-native-language"))syncLanguageControl(control,true);
  // The upstream UI only ships English and Chinese; use its own URL switch for those.
  // Portuguese remains a Tasks-only preference. Dashboard selection never reloads.
  if(native&&value!=="pt-BR"&&nativeLanguage()!==value){
    const url=new URL(location.href);url.searchParams.set("freevideo_lang",value);location.assign(url.href);return;
  }
  if(changed&&ui){populate(ui);fillNav(ui,nativeNav);syncTheme(ui);render();}
}
function bindLanguageMenu(d,native=false){
  const trigger=d.querySelector(".language"),menu=d.querySelector(".language-menu");
  for(const [value,label] of languageOptions){
    const choice=button("",()=>{
      setLanguageMenu(d,false);
      selectLanguage(value,native);
      (native?d:ui)?.querySelector(".language")?.focus({preventScroll:true});
    },"language-choice");
    choice.dataset.language=value;choice.setAttribute("aria-pressed",String(value===LANGUAGE));choice.lang=value;
    choice.append(el("span","",label),el("span","check",value===LANGUAGE?"✓":""));
    menu.append(choice);
  }
  if(native)menu.append(el("small","language-note",translate("PT-BR apenas no Tasks; EN e 中文 também alteram o FreeVideo.",nativeLanguage())));
  syncLanguageControl(d,native);
  trigger.onclick=()=>{const open=menu.hidden;for(const other of document.querySelectorAll(".ftm-native-language"))if(other!==d)setLanguageMenu(other,false);setLanguageMenu(d,open);};
  trigger.onkeydown=e=>{if(e.key!=="ArrowDown"&&e.key!=="ArrowUp")return;e.preventDefault();setLanguageMenu(d,true);const items=menu.querySelectorAll("button");items[e.key==="ArrowDown"?0:items.length-1]?.focus();};
  menu.onkeydown=e=>{if(!["ArrowDown","ArrowUp","Home","End"].includes(e.key))return;e.preventDefault();const items=[...menu.querySelectorAll("button")], at=items.indexOf(document.activeElement);const index=e.key==="Home"?0:e.key==="End"?items.length-1:(at+(e.key==="ArrowDown"?1:-1)+items.length)%items.length;items[index]?.focus();};
}
function openNativeCreations(d){
  const native=nativeNav?.closest(".fv-topbar")?.querySelector(".fv-tb-works");
  d.addEventListener("close",()=>{if(native?.isConnected)native.click();else void load("palette").then(m=>m.openCreations()).catch(e=>console.warn("FreeVideo Task Manager",e));},{once:true});
  d.close();
}
function build(){
  const d=el("dialog","ftm");d.setAttribute("aria-label","FreeVideo Task Manager");populate(d);
  d.addEventListener("click",e=>{if(!e.target.closest(".ftm-language"))setLanguageMenu(d,false);});
  d.addEventListener("focusout",e=>{if(e.target.closest(".ftm-language")&&e.relatedTarget&&!e.relatedTarget.closest(".ftm-language"))setLanguageMenu(d,false);});
  d.addEventListener("keydown",e=>{if(e.key==="Escape"&&d.querySelector(".language")?.getAttribute("aria-expanded")==="true"){e.preventDefault();e.stopPropagation();setLanguageMenu(d,false,true);}},true);
  d.addEventListener("close",()=>{setLanguageMenu(d,false);pollEpoch++;clearInterval(timer);clearInterval(tick);unsubscribe?.();agentUnsubscribe?.();agent?.watch(false);nativeNav?.focus({preventScroll:true});});
  document.body.append(d);
  new MutationObserver(()=>syncTheme(d)).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
  return d;
}
function fillNav(d,anchor){
  const nav=d.querySelector("nav");nav.replaceChildren();
  for(const tab of anchor?.parentElement?.querySelectorAll("[data-view]:not(.ftm-tab)")||[]){if(tab.hidden)continue;const b=button(tab.dataset.label||tab.textContent,()=>{d.close();tab.click();});b.setAttribute("aria-pressed","false");nav.append(b);}
  const current=button(t("Tasks"),()=>{});current.setAttribute("aria-current","page");current.setAttribute("aria-pressed","true");nav.append(current);
}
async function open(anchor){
  if(opening||visible())return;opening=true;nativeNav=anchor;
  try {
    if(!sources){
      const [store,float,agentModule,queue]=await Promise.all([load("task_store"),load("task_float"),load("agent_tasks"),load("studio_queue")]);
      float.startJobSources();sources={jobs:store.jobs,queue};
      // Reuse agent adapter; capture full history on its own reads, without additional activity polls.
      agent=agentModule.createAgentTasks(new Proxy(api,{get(target,key){if(key==="fetchApi")return async (path,opts)=>{if(!visible()||document.hidden)return new Response("",{status:503});const r=await target.fetchApi(path,opts);if(path==="/freevideo/agent/activity"&&r.ok){try{agentRaw=(await r.clone().json()).jobs||[];}catch{}}return r;};const v=Reflect.get(target,key);return typeof v==="function"?v.bind(target):v;}}));
    }
    ui??=build();
    syncTheme(ui);

    fillNav(ui,anchor);
    ui.showModal();pollEpoch++;unsubscribe=sources.jobs.subscribe(render);agentUnsubscribe=agent.subscribe(render);agent.watch(true);render();void poll();timer=setInterval(poll,5000);tick=setInterval(render,1000);
  }catch(e){console.warn("FreeVideo Task Manager",e);window.alert(t("Task Manager indisponível:")+" "+String(e.message||e));}finally{opening=false;}
}
function install(){
  if(observer)return;
  const css=el("link");css.rel="stylesheet";css.href=new URL("./tasks.css",import.meta.url).href;document.head.append(css);
  const attach=()=>{for(const nav of document.querySelectorAll(".fv-view-switch")){if(nav.querySelector(".ftm-tab"))continue;const b=button("",()=>open(b),"ftm-tab fv-tb-tab");b.append(el("span","",t("Tasks")));b.dataset.view="tasks";b.dataset.label=t("Tasks");b.setAttribute("aria-pressed","false");b.setAttribute("aria-haspopup","dialog");b.title="FreeVideo Task Manager";nav.append(b);}attachNativeControls();syncNativeSettingsTheme();};
  let queued=false;observer=new MutationObserver(()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;attach();});});
  observer.observe(document.body,{childList:true,subtree:true});attach();
  document.addEventListener("pointerdown",e=>{for(const control of document.querySelectorAll(".ftm-native-language"))if(!control.contains(e.target))setLanguageMenu(control,false);},true);
  document.addEventListener("keydown",e=>{if(e.key!=="Escape")return;const control=[...document.querySelectorAll(".ftm-native-language")].find(c=>c.querySelector(".language")?.getAttribute("aria-expanded")==="true");if(control){e.preventDefault();e.stopPropagation();setLanguageMenu(control,false,true);}},true);
  globalThis.addEventListener?.("freevideo-view",()=>{for(const control of document.querySelectorAll(".ftm-native-language"))setLanguageMenu(control,false);});
  new MutationObserver(()=>{for(const b of document.querySelectorAll(".ftm-native-theme"))syncNativeTheme(b);syncNativeSettingsTheme();}).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
}
app.registerExtension({name:"Lordelo.FreeVideoTaskManager",setup(){install();}});
