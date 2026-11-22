// Offline regression checks: no server calls and no model operations.
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");
const js=fs.readFileSync(path.join(__dirname,"../web/tasks.js"),"utf8")
  .replace(/^import .*;$/gm,"").replaceAll("import.meta.url","'http://localhost/extensions/FreeVideoTaskManager/tasks.js'");
const i18n=fs.readFileSync(path.join(__dirname,"../web/i18n.js"),"utf8").replace(/^export /gm,"");
function context(){
  const c=vm.createContext({URL,URLSearchParams,Date,Math,JSON,Number,Map,Set,console,app:{registerExtension(){}},api:{},localStorage:{getItem(){return null},setItem(){}},document:{hidden:false}});
  vm.runInContext(i18n+"\n"+js,c);
  vm.runInContext("sources={jobs:{list:()=>[],get:()=>null},queue:{}}",c);
  return c;
}
const evaluate=(c,expr)=>vm.runInContext(expr,c);
test("language trigger toggles the menu and exposes all three languages",()=>{
  const c=context();
  evaluate(c,"globalThis.menu={hidden:true};globalThis.trigger={expanded:'false',setAttribute(k,v){this.expanded=v},focus(){this.focused=true}};globalThis.dashboard={querySelector(s){return s==='.language'?trigger:menu}}");
  evaluate(c,"setLanguageMenu(dashboard,true)");
  assert.equal(evaluate(c,"menu.hidden"),false);
  assert.equal(evaluate(c,"trigger.expanded"),"true");
  evaluate(c,"setLanguageMenu(dashboard,false,true)");
  assert.equal(evaluate(c,"menu.hidden"),true);
  assert.equal(evaluate(c,"trigger.expanded"),"false");
  assert.equal(evaluate(c,"trigger.focused"),true);
  assert.equal(evaluate(c,"languageOptions.map(x=>x[0]).join(',')"),"pt-BR,en,zh");
});
test("native appearance selector tracks global theme without changing language controls",()=>{
  const c=context();
  evaluate(c,"document.documentElement={dataset:{theme:'light'}};globalThis.themeControl={options:[{value:'dark'},{value:'light'}],value:'dark'};globalThis.languageControl={options:[{value:'zh'},{value:'en'}],value:'en'};document.querySelectorAll=()=>[themeControl,languageControl]");
  evaluate(c,"syncNativeSettingsTheme()");
  assert.equal(evaluate(c,"themeControl.value"),"light");
  assert.equal(evaluate(c,"languageControl.value"),"en");
  evaluate(c,"document.documentElement.dataset.theme='dark';syncNativeSettingsTheme()");
  assert.equal(evaluate(c,"themeControl.value"),"dark");
});
test("settings cog and theme glyphs are distinct; theme toggle uses shared preference",()=>{
  const c=context();
  assert.notEqual(evaluate(c,"iconMask(cog)"),evaluate(c,"iconMask(icons.sun)"));
  assert.notEqual(evaluate(c,"iconMask(icons.sun)"),evaluate(c,"iconMask(icons.moon)"));
  evaluate(c,"document.documentElement={dataset:{theme:'dark'}};localStorage.setItem=(key,value)=>{globalThis.saved=[key,value]};globalThis.FreeVideoShell={setTheme(value){document.documentElement.dataset.theme=value}}");
  evaluate(c,"toggleTheme()");
  assert.equal(evaluate(c,"currentTheme()"),"light");
  assert.equal(evaluate(c,"saved.join(':')"),"freevideo.theme:light");
  evaluate(c,"toggleTheme()");
  assert.equal(evaluate(c,"currentTheme()"),"dark");
});
test("locale detection uses native explicit language then browser, with English fallback",()=>{
  const c=context();
  assert.equal(evaluate(c,"resolveLanguage('', 'pt-BR')"),"pt-BR");
  assert.equal(evaluate(c,"resolveLanguage('?freevideo_lang=en','pt-BR')"),"en");
  assert.equal(evaluate(c,"resolveLanguage('?freevideo_lang=zh','pt-BR')"),"zh");
  assert.equal(evaluate(c,"resolveLanguage('', 'fr-FR')"),"en");
  assert.equal(evaluate(c,"resolveLanguage('?freevideo_lang=en', 'en', 'pt-BR')"),"pt-BR");
  assert.equal(evaluate(c,"resolveLanguage('', 'pt-BR', 'zh')"),"zh");
  assert.equal(evaluate(c,"resolveLanguage('', 'pt-BR', 'invalid')"),"pt-BR");
  assert.equal(evaluate(c,"translate('Cancelar','en')"),"Cancel");
  assert.equal(evaluate(c,"translate('Cancelar','zh')"),"取消");
  assert.equal(evaluate(c,"translate('Cancelar','pt-BR')"),"Cancelar");
});
test("changing the selected language updates runtime status and group labels",()=>{
  const c=context();
  evaluate(c,"LANGUAGE='pt-BR'");
  assert.equal(evaluate(c,"t(states.running)"),"Em execução");
  evaluate(c,"LANGUAGE='en'");
  assert.equal(evaluate(c,"t(states.running)"),"Running");
  assert.equal(evaluate(c,"t(groups.all)"),"All");
  evaluate(c,"LANGUAGE='zh'");
  assert.equal(evaluate(c,"t(states.running)"),"运行中");
  assert.equal(evaluate(c,"t(groups.all)"),"全部");
});
test("timestamps, unknown estimates, fractions and units",()=>{
  const c=context();
  assert.equal(evaluate(c,"elapsed(3661)"),"01:01:01");
  assert.equal(evaluate(c,"elapsed(null)"),"—");
  assert.equal(evaluate(c,"fraction(1.5)"),1);
  assert.equal(evaluate(c,"stamp(1795383596008)"),1795383596008);
  assert.equal(evaluate(c,"stamp(1795383596)"),1795383596000);
  assert.equal(evaluate(c,"gib(17179869184)"),"16.0 GB");
});
test("origin separates MCP, CLI and engine",()=>{
  const c=context();
  assert.equal(evaluate(c,"sourceLabel({kind:'agent',job:{client:'freevideo-mcp'}})"),"MCP");
  assert.equal(evaluate(c,"sourceLabel({kind:'agent',job:{client:'freevideo-cli'}})"),"CLI");
  assert.equal(evaluate(c,"sourceLabel({kind:'video'})"),"FreeVideo");
});
test("server queue and agent history are merged without duplicating an ID",()=>{
  const c=context();
  evaluate(c,`journal=[{id:'x',kind:'video',status:'running'}];queueRows=[{prompt_id:'x',node:'n',status:'running',client:'agent:j',percent:.42,eta_seconds:80}];
  agentRaw=[{id:'j',status:'running',client:'freevideo-mcp',progress:{fraction:.42}},{id:'f',status:'failed',client:'freevideo-cli'},{id:'d',status:'succeeded'}];`);
  assert.equal(evaluate(c,"rows().length"),4);
  assert.equal(evaluate(c,"rows().find(r=>r.id==='video:x').percent"),.42);
  assert.equal(evaluate(c,"sourceLabel(rows().find(r=>r.id==='video:x'))"),"MCP");
  assert.equal(evaluate(c,"rows().find(r=>r.id==='video:x').externalCancel"),false);
  assert.equal(evaluate(c,"rows().find(r=>r.id==='agent:d').status"),"done");
  assert.equal(evaluate(c,"rows().find(r=>r.id==='agent:f').status"),"failed");
});
test("only eligible terminal tasks expose resume or retry",()=>{
  const c=context();
  assert.equal(evaluate(c,"canResume({kind:'chain',status:'interrupted',chain:{id:'x'}})"),true);
  assert.equal(evaluate(c,"canResume({kind:'chain',status:'running',chain:{id:'x'}})"),false);
  assert.equal(evaluate(c,"canRetry({kind:'video',status:'failed',id:'video:x'})"),true);
  assert.equal(evaluate(c,"canRetry({kind:'agent',status:'failed',id:'agent:x'})"),false);
});
test("terminal store rows cannot be revived by a stale queue sample",()=>{
  const c=context();
  evaluate(c,"sources.jobs.list=()=>[{id:'video:x',kind:'video',status:'done'}];queueRows=[{prompt_id:'x',node:'n',status:'running'}]");
  assert.equal(evaluate(c,"rows()[0].status"),"done");
});
