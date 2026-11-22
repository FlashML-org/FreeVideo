// Regenerate the compatibility catalog from a matching FreeVideo source tree.
// Only literal UI pairs are collected; no prompts, workflows or user data.
import fs from 'node:fs';
import path from 'node:path';
const source=process.argv[2];
const upstreamCatalog=process.argv[3];
if(!source||!upstreamCatalog)throw Error('Usage: node scripts/build_native_locales.mjs <FreeVideo web directory> <native locale directory>');
const en=JSON.parse(fs.readFileSync(path.join(upstreamCatalog,'en.json'),'utf8'));
const zh=JSON.parse(fs.readFileSync(path.join(upstreamCatalog,'zh.json'),'utf8'));
const pt=JSON.parse(fs.readFileSync(path.join(upstreamCatalog,'pt-BR.json'),'utf8'));
const files=['view_navigation','topbar_state','topbar','studio','studio_console','result_actions','sampling_effort','settings_room','settings_words','settings_kit','library','context_menu','image_view','image_view_ui','editor','palette','task_float','tooltip_copy','monitor_panel','generation_progress'];
const literal=String.raw`(?:'(?:\\.|[^'\\\r\n])*'|"(?:\\.|[^"\\\r\n])*")`;
const pair=new RegExp(String.raw`(?:\b(?:t|text|t0|tt)\(\s*|\[\s*)(${literal})\s*,\s*(${literal})\s*(?:\)|\])`,'g');
const pairs=new Map();
function add(a,b){if(typeof a!=='string'||typeof b!=='string'||!a.trim()||!b.trim()||a.length>500||b.length>500||!/[\u3400-\u9fff]/.test(b))return;const key=JSON.stringify([a,b]);pairs.set(key,[a,b]);}
for(const file of files){const src=fs.readFileSync(path.join(source,file+'.js'),'utf8');for(const match of src.matchAll(pair)){
  try{add(Function('return '+match[1])(),Function('return '+match[2])());}catch{}
}}
const translated={};
for(const key of Object.keys(en))if(!key.startsWith('progress.')&&!key.startsWith('theme.')&&!key.startsWith('quality.')){translated[en[key]]=pt[key];add(en[key],zh[key]);}
const overrides=[
 ['Create','创作面板'],['Images','图像视图'],['Nodes','节点视图'],['Edit','剪辑视图'],['Autopilot','AI 全自动'],
 ['Creations','作品库'],['Ready','就绪'],['Light','轻量'],['Light','浅色'],['Dark','深色'],['Enhance','增强'],
 ['Media','素材'],['Generate video','生成视频'],['Generate preview','生成预览'],['Extend','接着生成'],
 ['Share','分享'],['Reuse settings','复用设置'],['Download video','下载视频'],['Settings','设置']
];overrides.forEach(([a,b])=>add(a,b));
const output='// Generated compatibility catalog for FreeVideo 0.6.8. See scripts/build_native_locales.mjs.\nexport const nativePairs = '+JSON.stringify([...pairs.values()].sort((a,b)=>a[0].localeCompare(b[0],'en')||a[1].localeCompare(b[1],'zh'))) + ';\nexport const nativePortugueseLabels = '+JSON.stringify(translated)+';\n';
fs.writeFileSync('web/native_locales.js',output);
console.log('Pairs:',pairs.size,'Portuguese labels:',Object.keys(translated).length);
