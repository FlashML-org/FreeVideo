// Optional browser smoke test: disposable Chromium profile, no generation.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const chrome=process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function run(locale){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'fv-locale-test-'));
 fs.mkdirSync(path.join(dir,'Default'));
 fs.writeFileSync(path.join(dir,'Default','Preferences'),JSON.stringify({intl:{accept_languages:locale}}));
 const proc=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+dir,'--lang='+locale,'about:blank'],{stdio:'ignore'});
 let ws;
 try{
  const endpoint=path.join(dir,'DevToolsActivePort');
  for(let i=0;i<100&&!fs.existsSync(endpoint);i++)await delay(100);
  const port=fs.readFileSync(endpoint,'utf8').split(/\r?\n/)[0];
  const tabs=await (await fetch('http://127.0.0.1:'+port+'/json/list')).json();
  ws=new WebSocket(tabs.find(x=>x.type==='page').webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
  let seq=0;const calls=new Map();
  ws.onmessage=e=>{const obj=JSON.parse(e.data);if(obj.id&&calls.has(obj.id)){const [r,j]=calls.get(obj.id);calls.delete(obj.id);obj.error?j(Error(JSON.stringify(obj.error))):r(obj.result);}};
  const call=(method,params={})=>new Promise((r,j)=>{let id=++seq;calls.set(id,[r,j]);ws.send(JSON.stringify({id,method,params}));});
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate',{url:'http://127.0.0.1:8188/?freevideo=launch'});
  let state;
  for(let i=0;i<100;i++){
   await delay(300);
   const r=await call('Runtime.evaluate',{expression:`JSON.stringify({browser:navigator.language, languages:navigator.languages, tabs:[...document.querySelectorAll('.fv-view-switch [data-view=studio]')].map(b=>b.textContent.trim()), code:document.querySelector('.ftm-native-language .language-code')?.textContent, saved:sessionStorage.getItem('freevideo-task-manager.tab-language'), generate:[...document.querySelectorAll('.fvs-key-label')].map(e=>e.textContent.trim())})`,returnByValue:true});
   if(!r.result.value)continue;
   state=JSON.parse(r.result.value);
   if(state.code&&state.tabs.length&&state.generate.length)break;
  }
  console.log(JSON.stringify({requested:locale,...state}));
  const expected=locale.startsWith('pt')?'PT-BR':locale.startsWith('zh')?'中文':'EN';
  if(state?.code!==expected||!state.tabs.includes(expected==='PT-BR'?'Criar':expected==='EN'?'Create':'创作面板'))throw Error('First-visit check failed '+JSON.stringify(state));
 } finally {ws?.close();proc.kill();await delay(500);try{fs.rmSync(dir,{recursive:true,force:true,maxRetries:2})}catch{}}
}
(async()=>{for(const lang of process.argv.slice(2))await run(lang)})().catch(e=>{console.error(e);process.exitCode=1});
