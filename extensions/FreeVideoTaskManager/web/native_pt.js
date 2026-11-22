// Scoped, reversible compatibility localization for FreeVideo 0.6.8.
// Never modify prompts, user-generated content, or workflow data.
import { nativePairs, nativePortugueseLabels } from "./native_locales.js";
const labels = {
  "Create": "Criar",
  "Images": "Imagens",
  "Nodes": "Nós",
  "Edit": "Editar",
  "Autopilot": "Autopilot",
  "Tasks": "Tarefas",
  "Creations": "Criações",
  "Settings": "Configurações",
  "Ready": "Pronto",
  "Generating": "Gerando",
  "Queued": "Na fila",
  "Waiting": "Aguardando",
  "Cancel": "Cancelar",
  "Cancelling": "Cancelando",
  "Media": "Mídia",
  "Light": "Leve",
  "Medium": "Médio",
  "High": "Alto",
  "Enhance": "Aprimorar",
  "Generate video": "Gerar vídeo",
  "Generate preview": "Gerar prévia",
  "Download video": "Baixar vídeo",
  "Download picture": "Baixar imagem",
  "Download": "Baixar",
  "Reuse settings": "Reutilizar configurações",
  "Filled in": "Preenchido",
  "Undo": "Desfazer",
  "Extend": "Estender",
  "Share": "Compartilhar",
  "Regenerate": "Gerar novamente",
  "Finish": "Finalizar",
  "Preview": "Prévia",
  "Search": "Pesquisar",
  "Models": "Modelos",
  "Downloads": "Downloads",
  "Models & downloads": "Modelos e downloads",
  "Use": "Uso",
  "Generation": "Geração",
  "Appearance": "Aparência",
  "Assistant": "Assistente",
  "Storage": "Armazenamento",
  "Maintenance": "Manutenção",
  "Page check": "Verificação da página",
  "Advanced": "Avançado",
  "Version & updates": "Versão e atualizações",
  "Getting started": "Primeiros passos",
  "Look": "Aparência",
  "Theme": "Tema",
  "Dark": "Escuro",
  "Dark panels, best for pictures": "Painéis escuros, ideais para imagens",
  "Light panels": "Painéis claros",
  "Applies at once to every view": "Aplica-se imediatamente a todas as telas",
  "Compatibility view": "Modo de compatibilidade",
  "Solid panels, fewer animations, no autoplay": "Painéis sólidos, menos animações, sem reprodução automática",
  "Top bar": "Barra superior",
  "Show this computer in the status": "Mostrar este computador no status",
  "Graphics memory and temperature in the top bar": "Memória de vídeo e temperatura na barra superior",
  "Language": "Idioma",
  "Interface language": "Idioma da interface",
  "Changing the language reloads the page; generations keep running.": "Mudar o idioma recarrega a página; as gerações continuam.",
  "Reload now": "Recarregar agora",
  "This computer": "Este computador",
  "Temperature": "Temperatura",
  "Disk free": "Espaço livre",
  "Temporary files": "Arquivos temporários",
  "FreeVideo keeps": "FreeVideo mantém",
  "Search settings": "Pesquisar configurações",
  "No setting matches": "Nenhuma configuração encontrada",
  "FreeVideo view": "Tela do FreeVideo",
  "FreeVideo settings": "Configurações do FreeVideo",
  "Settings pages": "Páginas de configurações",
  "FreeVideo creative workspace": "Área de criação do FreeVideo",
  "Search works, prompts and actions": "Pesquisar criações, prompts e ações",
  "Reference media (@)": "Referenciar mídia (@)",
  "Add reference pictures, video and sound": "Adicionar imagens, vídeos e áudio de referência",
  "Save the MP4 to this computer": "Salvar o MP4 neste computador",
  "Fill the creation panel with these settings": "Preencher o painel de criação com estas configurações",
  "Tutorial": "Tutorial",
  "Feedback": "Feedback",
  "Version": "Versão",
  "Version and updates": "Versão e atualizações",
  "Close": "Fechar",
  "Back": "Voltar",
  "More": "Mais",
  "Menu": "Menu"
};
const portuguese={...nativePortugueseLabels,...labels};
const englishToChinese=new Map(), chineseToEnglish=new Map();
function addCandidate(map,key,value){if(!map.has(key))map.set(key,new Set());map.get(key).add(value);}
for(const [en,zh] of nativePairs){addCandidate(englishToChinese,en,zh);addCandidate(chineseToEnglish,zh,en);}
const preferred=new Map([
 ["Create","创作面板"],["Images","图像视图"],["Nodes","节点视图"],["Edit","剪辑视图"],["Autopilot","AI 全自动"],["Creations","作品库"],
 ["Ready","就绪"],["Generating","正在生成"],["Media","素材"],["Light","轻量"],["Dark","深色"],["Enhance","增强"],["Generate video","生成视频"],
 ["Generate preview","生成预览"],["Download video","下载视频"],["Reuse settings","复用设置"],["Extend","接着生成"],["Share","分享"],["Settings","设置"]
]);
const reversePreferred=new Map([...preferred].map(([en,zh])=>[zh,en]));
const unique=(map,key)=>{const values=map.get(key);return values?.size===1?[...values][0]:null;};
const scopes = [
  ".fv-topbar .fv-view-switch > button:not(.ftm-tab)",
  ".fv-topbar .fv-tb-cap-txt",
  ".fv-topbar .fv-tb-link:not(.ftm-native-theme)",
  ".fv-topbar .fv-tb-search",
  ".fv-topbar .fv-tb-menu",
  ".fv-studio .fvs-chip-label",
  ".fv-studio .fvs-key-label",
  ".fv-studio .fvs-key-eta",
  ".fv-studio .fvs-tray-title",
  ".fv-studio .fv-action",
  ".fv-set .st-nav button",
  ".fv-set .st-ng",
  ".fv-set .st-nav-empty",
  ".fv-set .st-ph h1",
  ".fv-set .st-gh",
  ".fv-set .st-rt",
  ".fv-set .st-seg button",
  ".fv-set .st-foot",
  ".fv-set .st-gn",
  ".fv-set .st-rh",
  ".fv-set .st-meter .l",
  ".fv-set input[type=search]",
  ".fv-library-tabs button", ".fv-library .fv-action",
  ".fv-image .fvs-chip-label", ".fv-image .fvs-key-label", ".fv-image .fvs-key-eta",
  ".fv-image .fv-action"
].join(",");
const attrs = ["aria-label","title","placeholder","data-label","data-fv-tip-name","data-fv-tip-use"];
// FreeVideo marks its own dialogs translate="no" to block *browser* translation.
// This is an explicit, local catalog, so that inherited flag must not exclude
// the native UI we intend to translate. The scoped selectors and editable-field
// exclusions below keep user content outside the translation pass.
const skip = ".ftm, .ftm-native-language, [contenteditable], textarea";
export function nativeLabel(value,target,theme=false,base="en"){
  if(typeof value!=="string"||target===base)return value;
  const raw=value.trim();
  const eta=/^About (\d+) min$/.exec(raw)||/^约\s*(\d+)\s*分钟$/.exec(raw);
  const generation=/^Generating (\d+)%$/.exec(raw)||/^(?:正在生成|生成中)\s*(\d+)%$/.exec(raw);
  let translated=null;
  if(eta)translated=target==="zh"?"约 "+eta[1]+" 分钟":target==="pt-BR"?"Cerca de "+eta[1]+" min":"About "+eta[1]+" min";
  else if(generation)translated=target==="zh"?"正在生成 "+generation[1]+"%":target==="pt-BR"?"Gerando "+generation[1]+"%":"Generating "+generation[1]+"%";
  else {
    const english=base==="zh"?(raw==="浅色"||raw==="轻量"?"Light":reversePreferred.get(raw)||unique(chineseToEnglish,raw)):raw;
    if(english){
      if(target==="en")translated=english;
      else if(target==="zh")translated=theme&&english==="Light"?"浅色":preferred.get(english)||unique(englishToChinese,english);
      else if(target==="pt-BR")translated=theme&&english==="Light"?"Claro":Object.hasOwn(portuguese,english)?portuguese[english]:english;
    }
  }
  return translated?value.replace(raw,translated):value;
}
export function ptLabel(value,theme=false){return nativeLabel(value,"pt-BR",theme,"en");}
export function createNativePortuguese(doc=document,base="en"){
  let language=base, scheduled=false;
  const records=new WeakMap(), pending=new Set();
  function patchValue(owner,key,read,write,theme){
    let state=records.get(owner);
    const now=read();if(now==null)return;
    const previous=state?.get(key);
    const original=previous&&now===previous.applied?previous.original:now;
    const next=nativeLabel(original,language,theme,base);
    if(next!==now)write(next);
    if(language!==base&&next!==original){if(!state){state=new Map();records.set(owner,state);}state.set(key,{original,applied:next});}
    else state?.delete(key);
  }
  function scan(element){
    if(!element?.isConnected||element.closest(skip))return;
    const theme=Boolean(element.closest(".fv-set"));
    const walker=doc.createTreeWalker(element,4);
    let node;while((node=walker.nextNode())){
      if(node.parentElement?.closest(skip))continue;
      patchValue(node,"text",()=>node.nodeValue,v=>{node.nodeValue=v;},theme);
    }
    for(const a of attrs)patchValue(element,a,()=>element.getAttribute(a),v=>element.setAttribute(a,v),theme);
  }
  function collect(element){
    if(!element||element.nodeType!==1||element.closest(skip))return;
    const owner=element.closest(scopes);if(owner)pending.add(owner);
    if(element.matches(scopes))pending.add(element);
    for(const child of element.querySelectorAll(scopes))pending.add(child);
  }
  function flush(){scheduled=false;for(const element of pending)scan(element);pending.clear();}
  function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(flush);}}
  const observer=new MutationObserver(changes=>{
    if(language===base)return;
    for(const change of changes){
      if(change.type==="characterData"){const owner=change.target.parentElement?.closest(scopes);if(owner)pending.add(owner);}
      else if(change.type==="attributes"){const owner=change.target.closest(scopes);if(owner)pending.add(owner);}
      else {const owner=change.target.closest?.(scopes);if(owner)pending.add(owner);for(const node of change.addedNodes)if(node.nodeType===1)collect(node);}
    }
    if(pending.size)schedule();
  });
  observer.observe(doc.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:attrs});
  function setLanguage(value){if(!["en","zh","pt-BR"].includes(value))return;language=value;for(const element of doc.querySelectorAll(scopes))scan(element);}
  return {setLanguage,setEnabled(value){setLanguage(value?"pt-BR":base);},dispose(){observer.disconnect();setLanguage(base);}};
}
