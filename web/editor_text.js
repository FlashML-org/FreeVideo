// Text UI owns its controls, preview overlays and secondary menus. Timeline
// editing remains in editor_tracks/model/select and the shared history.
import * as model from './editor_model.js';
import {FONT_KEYS} from './fonts.js';
import {row, button, slider, number, segmented, toggle} from './editor_adjust.js';
import {isText, isTextTrack, addText, changeText, changeSubtitleStyle, styleOf, activeText, setTextTrack, parseSRT, decodeSRT, importSRT, addSubtitles, defaults, cleanStyle, SUBTITLE_DEFAULTS} from './editor_text_model.js';
import {loadCoverage, loadTextFonts, textFontStack, missingGlyphs, missingHint, overTwoHint, textLayout, drawText} from './editor_text_layout.js';
import {createSelectionActions, isTyping} from './editor_select.js';
import {plan} from './editor_geometry.js';
import {isolateDialogKeys} from './dialog_keys.js';
import {issueButton} from './issue_button.js';
import {skinSelect} from './fv_select.js';
const SPEECH_ISSUE = ['Auto subtitles', '自动字幕'], FONT_ISSUE = ['Editor text fonts', '剪辑器文字字体'];
const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;};
const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
const PRESET_KEY='freevideo.subtitle-presets',PRESET_LIMIT=6;
// Saved subtitle looks live in this browser, so any project can reuse them; a bad or missing store is an empty list.
function readPresets(){
    let saved;try{saved=JSON.parse(localStorage.getItem(PRESET_KEY));}catch{return [];}
    if(!Array.isArray(saved))return [];
    return saved.flatMap(value=>{try{return [cleanStyle(value,'subtitle')];}catch{return [];}}).slice(0,PRESET_LIMIT);
}
function writePresets(list){try{localStorage.setItem(PRESET_KEY,JSON.stringify(list));}catch{/* The look still applies without storage. */}}
const sameStyle=(a,b)=>JSON.stringify(Object.entries(cleanStyle(a,'subtitle')).sort())===JSON.stringify(Object.entries(cleanStyle(b,'subtitle')).sort());
export function createEditorText({element,state,t,actions,adjust,player,toolbar,exportBox,tracksHost}) {
    const frame=player.element, canvas=el('canvas','fv-ed-text-canvas'), boundsLayer=el('div','fv-ed-text-handles');
    canvas.setAttribute('aria-hidden','true'); frame.append(canvas,boundsLayer);
    const ctx=canvas.getContext?.('2d'), measureCanvas=el('canvas'), metrics=measureCanvas.getContext?.('2d');
    const buttons=el('div','fv-ed-tool-group fv-ed-text-buttons');
    // Icons only on the toolbar; the name lives in the label and hover.
    const textButton=button('',{onClick:()=>add('text')}), subtitleButton=button('',{onClick:()=>add('subtitle')});
    textButton.classList.add('fv-ed-add-text'); subtitleButton.classList.add('fv-ed-add-subtitle'); buttons.dataset.tourId = 'text-entry';
    // The same 16 px line glyphs as the other toolbar tools.
    const glyph=d=>{const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 16 16');svg.setAttribute('aria-hidden','true');svg.classList.add('fv-ed-glyph');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);return svg;};
    textButton.classList.add('fv-ed-icon'); subtitleButton.classList.add('fv-ed-icon');
    textButton.append(glyph('M3 5V3.5h10V5M8 3.5v9M6 12.5h4')); subtitleButton.append(glyph('M4 3.5h8a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2zM4.5 7.5h3M9.5 7.5h2M4.5 10h2M8.5 10h3'));
    textButton.setAttribute('aria-label',t('Text','文字')); subtitleButton.setAttribute('aria-label',t('Subtitles','字幕'));
    textButton.title=t('Text T','文字 T'); subtitleButton.title=t('Subtitles','字幕');
    buttons.append(textButton,subtitleButton);
    const marker=toolbar.querySelector('.fv-ed-marker, .fv-ed-add-marker');
    if(marker) marker.after(buttons); else toolbar.insertBefore(buttons,toolbar.querySelector('.fv-ed-zoom'));
    const notice=el('span');notice.setAttribute('role','button');notice.tabIndex=0;notice.onclick=()=>jumpMissing();notice.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();jumpMissing();}}; notice.classList.add('fv-ed-text-notice'); notice.hidden=true; exportBox.prepend(notice);
    const fontFailure=()=>t('Text fonts could not be loaded','文字字体没能加载');
    let fontIssue=null;
    const menu=el('div','fv-ed-text-menu'); menu.hidden=true; menu.setAttribute('role','menu'); element.append(menu);
    const names={sans:t('Sans','黑体'),serif:t('Serif','宋体'),kai:t('Kai','楷体'),mono:t('Mono','等宽')};
    let disposed=false,ready=false,table=null,loadError=false,draft=null,fresh=null,typing=null,lastStyle={},editing=null,drag=null,raf=null,layouts=new Map(),mapped={x:0,y:0,w:1,h:1};
    const panels=new Map(), commands=createSelectionActions({state,t,actions});
    const selected=()=>state.selection?.kind==='clip' && state.selection.ids.length===1 ? model.find(state.record,state.selection.ids[0]) : null;
    const record=()=>draft?.record||state.record;
    const selectedText=()=>{const c=selected();return isText(c)?c:null;};
    // Refusals only flash red, as on the other tracks; anything else asks to try again.
    const report=e=>actions.editError?.(e);
    const locked=c=>state.record.tracks.some(row=>row.locked && row.clips.some(v=>v.id===c?.id));
    function apply(label,change) { draft=null;try{state.apply(label,change);return true;}catch(e){report(e);render();return false;} }
    function modifyStyle(changes,preview=false) {
        const c=selectedText();if(!c)return;
        const change=r=>c.text_kind==='subtitle' ? changeSubtitleStyle(r,changes) : changeText(r,c.id,{text_style:{...c.text_style,...changes}});
        if(preview){try{draft={record:change(state.record),change};render();}catch(e){report(e);}return;}
        if(apply(t('Style','样式'),change) && c.text_kind==='text') lastStyle={...styleOf(state.record,model.find(state.record,c.id))};
    }
    function updateContent(c,value,preview=false) {
        const change=r=>changeText(r,c.id,{text:value});
        if(preview){try{draft={record:change(state.record),change};render();updateWarnings();}catch(e){report(e);}return;}
        apply(t('Content','内容'),change);
    }
    // Subtitles from the speech on the timeline. One press starts, the next cancels; the lines replace the subtitle track in one step.
    // `need` is what has to be downloaded before the first use, in bytes; the same press downloads it and goes on.
    const speech={buttons:new Set(),available:false,need:0,busy:false,loading:false,shown:null,job:null,stop:null,run:0};
    function speechLabel(fraction){speech.shown=fraction;const p=Math.round(Math.min(1,Math.max(0,Number(fraction)||0))*100),size=Math.max(1,Math.round(speech.need/1e6)),text=fraction==null?(speech.need?t(`Auto subtitles · Download ${size} MB`,`自动字幕 · 下载 ${size} MB`):t('Auto subtitles','自动字幕')):speech.loading?t(`Cancel · Downloading ${p}%`,`取消 · 下载 ${p}%`):t(`Cancel · ${p}%`,`取消 · ${p}%`);for(const b of speech.buttons){b.textContent=text;b.hidden=!(speech.available||speech.need);}}
    function speechEnd(text,where=null){speech.stop?.();speech.stop=null;speech.job=null;speech.busy=false;speech.loading=false;speechLabel(null);if(text)actions.notify?.(text,where);}
    const speechFailed=()=>t('Could not create subtitles','没能生成字幕'),speechSilent=()=>t('No speech found','没有听到说话声');
    const speechDisk=()=>t('The disk is full. Free some space, then try again.','磁盘空间不足，请清理出空间后重试。');
    // Causes the person can act on are named and carry no issue button; anything else is unexplained.
    const speechReasons={no_sound:speechSilent,language:()=>t('Language not supported yet','暂不支持这种语言'),too_long:()=>t('Auto subtitles work on up to 2 hours of sound.','自动字幕最多处理 2 小时的声音。'),
        source_missing:()=>t('A source file is missing. Import it again from the library.','有素材的原文件找不到了，请在素材库重新导入。'),unreadable:()=>t('The sound in a clip cannot be read.','有素材的声音无法读取。'),disk_full:speechDisk};
    const speechReason=reason=>(Object.hasOwn(speechReasons,reason)?speechReasons[reason]:speechFailed)();
    function speechResult(job){
        if(job.state==='running'){speechLabel(job.fraction);return true;}
        if(job.state!=='done'){speechEnd(job.state==='cancelled'?null:speechReason(job.reason),job.state==='cancelled'||Object.hasOwn(speechReasons,job.reason)?null:SPEECH_ISSUE);return false;}
        const cues=(Array.isArray(job.result?.lines)?job.result.lines:[]).filter(l=>typeof l?.text==='string'&&l.text.trim()&&l.end>l.start).map(l=>({start:l.start,frames:l.end-l.start,text:l.text.trim()}));
        speechEnd();if(!cues.length){actions.notify?.(speechSilent());return false;}
        finishInline();commitDraft();
        if(!apply(t('Auto subtitles','自动字幕'),r=>addSubtitles(r,cues,{replace:true})))actions.notify?.(speechFailed(),SPEECH_ISSUE);
        else{const missing=projectMissing(state.record);if(missing.length)actions.notify?.(missingHint([...new Set(missing.flatMap(r=>r.chars))],t));}
        return false;
    }
    async function toggleSpeech(){
        if(speech.busy){const id=speech.job,loading=speech.loading;speech.run++;speechEnd();if(loading)Promise.resolve(actions.speech.cancelDownload()).catch(()=>{});else if(id)Promise.resolve(actions.speech.cancel(id)).catch(()=>{});return;}
        const run=++speech.run;speech.busy=true;
        try{
            if(speech.need){
                speech.loading=true;speechLabel(0);
                const done=await actions.speech.download(fraction=>{if(run===speech.run)speechLabel(fraction);});
                if(run!==speech.run)return;
                // `done` is true, or the reason the download stopped.
                const ability=done===true?await actions.speech.ability():null;
                if(run!==speech.run)return;
                if(ability){speech.available=ability.available;speech.need=ability.need;}
                if(done!==true||speech.need){speechEnd(done==='disk_space'?speechDisk():t('Could not download. Try again.','没能下载，请再试一次。'),done==='disk_space'?null:SPEECH_ISSUE);return;}
                if(!speech.available){speechEnd(speechFailed(),SPEECH_ISSUE);return;}
                speech.loading=false;
            }
            speechLabel(0);
            const job=await actions.speech.start();
            if(run!==speech.run){if(job?.id)Promise.resolve(actions.speech.cancel(job.id)).catch(()=>{});return;}
            if(!job){speechEnd(speechFailed(),SPEECH_ISSUE);return;}
            speech.job=job.id??null;
            if(speechResult(job))speech.stop=actions.speech.watch(job.id,value=>{if(run!==speech.run)return false;if(!value){speechEnd(speechFailed(),SPEECH_ISSUE);return false;}return speechResult(value);},()=>{if(run===speech.run)speechEnd(speechFailed(),SPEECH_ISSUE);});
        }catch{if(run===speech.run)speechEnd(speech.loading?t('Could not download. Try again.','没能下载，请再试一次。'):speechFailed(),SPEECH_ISSUE);}
    }
    function commitDraft(){if(draft){const change=draft.change;apply(t('Text','文字'),change);const c=selectedText();if(c?.text_kind==='text')lastStyle={...styleOf(state.record,c)};}}
    // Editing a text ended. One left without words is taken away; one that never had any leaves no step behind.
    function endEditing(id){
        const shown=model.find(record(),id),made=fresh?.id===id?fresh:null;if(made)fresh=null;
        if(!isText(shown)||shown.text.trim()||locked(shown)){commitDraft();return;}
        draft=null;if(!model.find(state.record,id)){render();return;}
        if(!made||!state.withdraw(made.step))apply(t('Delete','删除'),r=>model.remove(r,[id]));
        render();
    }
    function finishContent(){if(typing){const id=typing;typing=null;endEditing(id);}else commitDraft();}
    function add(kind,at=state.playhead){
        commitDraft(); finishInline(); player.pause();
        const text='';
        let id;
        if(!apply(kind==='text'?t('Text','文字'):t('Subtitles','字幕'),r=>{const next=addText(r,{kind,at,text,style:{...lastStyle,x:.5,y:.5}});id=`c${next.serial}`;return next;}))return;
        fresh={id,step:state.step};state.select({kind:'clip',ids:[id]});state.seek(at);adjust.open();
        if(kind==='text') startInline(id,true);
        else {const content=panels.get(kind)?.content;content?.focus();content?.select();typing=id;}
    }
    actions.addText=(kind,at)=>add(kind,at);
    function buildPanel(host,kind){
        host.closest('.fv-ed-adjust-pane').dataset.textControls='true';
        const content=el('textarea','fv-ed-text-content');content.rows=3;content.maxLength=16000;content.setAttribute('aria-label',t('Content','内容'));
        const warning=el('p','fv-ed-text-warning');warning.setAttribute('role','status');warning.hidden=true;
        const overflow=el('p','fv-ed-text-warning');overflow.hidden=true;overflow.textContent=overTwoHint(t);
        host.append(row(t('Content','内容'),content),warning,overflow);
        const duration=number({min:1/24,max:1e7/24,step:1/24,label:t('Duration','时长'),onChange:v=>{const c=selectedText();if(c)apply(t('Duration','时长'),r=>changeText(r,c.id,{frames:Math.max(1,Math.round(v*24))}));}});
        const durationRow=row(t('Duration','时长'),duration);
        let presetRow=null;
        if(kind==='subtitle'){
            presetRow=el('div','fv-ed-style-presets');
            host.append(durationRow,el('hr'),el('strong','fv-ed-subtitle-style-title',t('Style for all subtitles','全部字幕的样式')),presetRow);
        }
        const font=el('select','fv-ed-text-font');font.setAttribute('aria-label',t('Font','字体'));
        for(const key of FONT_KEYS){const option=el('option','',names[key]);option.value=key;option.style.fontFamily=textFontStack(key);font.append(option);}
        font.onchange=()=>modifyStyle({font:font.value});host.append(row(t('Font','字体'),skinSelect(font)));
        const sizeNumber=number({min:16,max:200,step:1,label:t('Size','大小'),onChange:v=>modifyStyle({size:v/1080})});
        const size=slider({min:16,max:200,value:kind==='text'?64:44,onInput:v=>{sizeNumber.value=Math.round(v);modifyStyle({size:v/1080},true);},onChange:v=>modifyStyle({size:v/1080})});
        host.append(row(t('Size','大小'),size,sizeNumber));
        const colors=el('div','fv-ed-text-colors');
        for(const [color,en,zh] of [['#ffffff','White','白'],['#000000','Black','黑'],['#ffff00','Yellow','黄'],['#ff0000','Red','红'],['#0080ff','Blue','蓝'],['#00c853','Green','绿']]){
            const swatch=button('',{onClick:()=>modifyStyle({color})});swatch.style.backgroundColor=color;swatch.title=t(en,zh);swatch.setAttribute('aria-label',t(en,zh));swatch.dataset.color=color;colors.append(swatch);
        }
        const custom=el('input');custom.type='color';custom.setAttribute('aria-label',t('Custom','自定'));custom.title=t('Custom','自定');custom.oninput=()=>modifyStyle({color:custom.value},true);custom.onchange=()=>modifyStyle({color:custom.value});
        const customButton=button(t('Custom','自定'),{onClick:()=>custom.click()});customButton.classList.add('fv-ed-text-custom');custom.hidden=true;
        colors.append(customButton,custom);host.append(row(t('Color','颜色'),colors));
        const backing=segmented([['none','None','无'],['outline','Outline','描边'],['box','Box','底色']].map(([value,en,zh])=>({value,label:t(en,zh)})),{onChange:backing=>modifyStyle({backing})});
        host.append(row(t('Backing','衬底'),backing));
        let bold,align,fade;
        if(kind==='text'){
            bold=toggle(t('Bold','加粗'),{onChange:bold=>modifyStyle({bold})});host.append(bold);
            align=segmented([['left','Left','左'],['center','Center','中'],['right','Right','右']].map(([value,en,zh])=>({value,label:t(en,zh)})),{onChange:align=>modifyStyle({align})});host.append(row(t('Align','对齐'),align));
        }
        const positions=kind==='text'?[[.15,'Top','上'],[.5,'Middle','中'],[.85,'Bottom','下']]:[['bottom','Bottom','下'],['top','Top','上']];
        const position=segmented(positions.map(([value,en,zh])=>({value,label:t(en,zh)})),{onChange:value=>modifyStyle(kind==='text'?{x:.5,y:value}:{position:value,margin:.08})});host.append(row(t('Position','位置'),position));
        if(kind==='text'){
            host.append(durationRow);fade=toggle(t('Fade in and out','淡入淡出'),{onChange:fade=>modifyStyle({fade})});host.append(fade);
            host.append(button(t('Reset style','恢复默认样式'),{onClick:()=>{const c=selectedText();if(c)apply(t('Reset style','恢复默认样式'),r=>changeText(r,c.id,{text_style:{}}));lastStyle={};}}));
        }else{
            const file=el('input');file.type='file';file.accept='.srt';file.hidden=true;
            const importButton=button(t('Import subtitle file','导入字幕文件'),{onClick:()=>file.click()});importButton.classList.add('fv-ed-import-srt');
            // A file that cannot be used says why; a refused edit (a locked track) flashes red like any other.
            file.onchange=async()=>{const f=file.files?.[0];if(!f)return;let text;const result={};
                try{
                    if(f.size>4*1024*1024){actions.notify?.(t('Subtitle files can be up to 4 MB.','字幕文件不能超过 4 MB。'));return;}
                    try{text=decodeSRT(await f.arrayBuffer(),t('windows-1252','gb18030'));}catch{actions.notify?.(t('Could not read this subtitle file','读不出这个字幕文件'));return;}
                    try{parseSRT(text,result);}catch{actions.notify?.(t('No subtitles found. Only SRT files can be imported.','未找到字幕。仅支持导入 SRT 字幕文件。'));return;}
                }finally{file.value='';}
                if(apply(t('Import subtitle file','导入字幕文件'),r=>importSRT(r,text))){const missing=projectMissing(state.record);if(missing.length)actions.notify?.(missingHint([...new Set(missing.flatMap(r=>r.chars))],t));
                    if(result.truncated)actions.notify?.(t('Only the first 500 subtitles were imported.','只导入了前 500 条字幕。'));
                    else if(result.skipped)actions.notify?.(t(`${result.skipped} subtitles could not be read and were skipped.`,`${result.skipped} 条字幕无法读取，已跳过。`));}};
            // Shown only where this computer can recognise speech.
            const hear=button(t('Auto subtitles','自动字幕'),{onClick:toggleSpeech});hear.classList.add('fv-ed-auto-subtitles');for(const b of speech.buttons)if(!b.isConnected)speech.buttons.delete(b);speech.buttons.add(hear);speechLabel(speech.busy?speech.shown:null);
            host.append(importButton,hear,file);
        }
        content.oninput=()=>{const c=selectedText();if(c)updateContent(c,content.value,true);};content.onchange=finishContent;
        content.onfocus=()=>{typing=selectedText()?.id??null;};
        // A press on one of its own controls is not leaving the text: it ends when another part is selected.
        let within=false;host.addEventListener('pointerdown',e=>{if(e.target===content)return;within=true;setTimeout(()=>{within=false;},0);},true);
        content.onblur=()=>{if(within&&typing)commitDraft();else finishContent();};
        content.onkeydown=e=>{
            e.stopPropagation();
            if(e.key==='Escape'){e.preventDefault();finishContent();content.blur();}
            else if(kind==='subtitle' && e.key==='Enter' && !e.shiftKey && !e.isComposing){e.preventDefault();const c=selectedText();if(!c||!model.find(record(),c.id)?.text.trim())return;commitDraft();add('subtitle',c.start+c.frames);content.value='';}
        };
        panels.set(kind,{presetRow,content,warning,overflow,font,size,sizeNumber,custom,backing,bold,align,position,duration,fade,host});
        updatePanel(kind);
    }
    // One press saves the subtitle look as a preset; one press on a preset sets it on every subtitle.
    function renderPresets(host,current,replaced=false){
        const list=readPresets(),found=list.findIndex(value=>sameStyle(value,current));
        host.replaceChildren();
        list.forEach((value,index)=>{
            const full={...SUBTITLE_DEFAULTS,...value},chip=el('span','fv-ed-preset');chip.dataset.current=String(index===found);
            const apply_=button('Aa',{onClick:()=>modifyStyle({...SUBTITLE_DEFAULTS,...value})});apply_.classList.add('fv-ed-preset-apply');
            apply_.setAttribute('aria-label',t(`Use style ${index+1}`,`套用样式 ${index+1}`));apply_.title=t(`Use style ${index+1}`,`套用样式 ${index+1}`);
            apply_.style.fontFamily=textFontStack(full.font);apply_.style.color=full.color;
            const light=parseInt(full.color.slice(1,3),16)*.3+parseInt(full.color.slice(3,5),16)*.59+parseInt(full.color.slice(5,7),16)*.11>140;
            apply_.style.backgroundColor=full.backing==='box'?'#000000':light?'#2a2f38':'#e5e7eb';
            if(full.backing==='outline')apply_.style.textShadow=light?'0 0 2px #000, 0 0 2px #000':'0 0 2px #fff, 0 0 2px #fff';
            const remove=button('×',{onClick:()=>{const next=readPresets();next.splice(index,1);writePresets(next);renderPresets(host,current);}});
            remove.classList.add('fv-ed-preset-remove');remove.setAttribute('aria-label',t(`Delete style ${index+1}`,`删除样式 ${index+1}`));remove.title=t('Delete','删除');
            chip.append(apply_,remove);host.append(chip);
        });
        const save=button(t('Save style','保存样式'),{onClick:()=>{
            // A full list gives up its oldest look for the new one, and says so for a moment.
            const next=readPresets(),full=next.length>=PRESET_LIMIT;if(full)next.shift();
            next.push(cleanStyle(current,'subtitle'));writePresets(next);renderPresets(host,current,full);}});
        save.classList.add('fv-ed-preset-save');
        if(found>=0){save.disabled=true;save.title=t('Already saved','已保存过');}
        host.append(save);
        if(replaced){
            const note=el('span','fv-ed-preset-note',t('Replaced the oldest','已替换最早的一套'));note.setAttribute('role','status');host.append(note);
            setTimeout(()=>note.remove(),2000);
        }
    }
    function updatePanel(kind){
        const p=panels.get(kind),c=selectedText();if(!p||!c||c.text_kind!==kind)return;
        p.host.disabled=locked(c);
        const s=styleOf(state.record,c);if(document.activeElement!==p.content)p.content.value=c.text;
        p.font.value=s.font;p.font.style.fontFamily=textFontStack(s.font);
        if(!draft){p.size.value=s.size*1080;p.sizeNumber.value=Math.round(s.size*1080);p.custom.value=s.color;}
        p.backing.value=s.backing;p.position.value=kind==='text'?s.y:s.position;p.duration.value=c.frames/24;
        if(p.bold){p.bold.value=s.bold;p.align.value=s.align;p.fade.value=s.fade;}
        if(p.presetRow&&!draft)renderPresets(p.presetRow,s);
        updateWarnings();
    }
    for(const kind of ['text','subtitle'])adjust.register(kind,{render:host=>buildPanel(host,kind),update:()=>updatePanel(kind)});
    function projectMissing(r){
        if(!table)return [];
        return r.tracks.filter(track=>isTextTrack(track)&&!track.hidden).flatMap(track=>track.clips.map(c=>{const s=styleOf(r,c);return {id:c.id,chars:missingGlyphs(c.text,s.font,!!s.bold,table)};})).filter(v=>v.chars.length);
    }
    function updateWarnings(){
        const r=record(),c=selectedText(),s=c&&styleOf(r,model.find(r,c.id));
        const missing=projectMissing(r),chars=[...new Set(missing.flatMap(r=>r.chars))];
        notice.hidden=!chars.length&&!loadError;notice.textContent=loadError?fontFailure():missingHint(chars,t);notice.title=notice.textContent;
        for(const [kind,p]of panels){
            if(c?.text_kind!==kind)continue;
            const entry=model.find(r,c.id),absent=table?missingGlyphs(entry.text,s.font,!!s.bold,table):[];
            p.warning.hidden=!absent.length&&!loadError;p.warning.textContent=loadError?fontFailure():missingHint(absent,t);
            if(loadError)p.warning.append(issueButton(t,()=>({where:FONT_ISSUE,message:fontFailure()})));
            p.overflow.hidden=kind!=='subtitle'||!ready||!metrics||!textLayout(metrics,entry.text,s,kind,r.width,r.height).overTwo;
        }
        // Both messages can be on screen at once; the button stays with the one nearest the text panel.
        const panelShowing=loadError&&[...panels.values()].some(p=>p.warning.offsetParent!==null);
        if(loadError&&!panelShowing&&!fontIssue){fontIssue=issueButton(t,()=>({where:FONT_ISSUE,message:fontFailure()}));notice.after(fontIssue);}
        if((!loadError||panelShowing)&&fontIssue){fontIssue.remove();fontIssue=null;}
    }
    function jumpMissing(){const first=projectMissing(state.record)[0];if(first){const c=model.find(state.record,first.id);state.select({kind:'clip',ids:[c.id]});state.seek(c.start);adjust.open();}}
    function updateClip(item,r){
        const c=item.clip;item.media.hidden=false;item.media.textContent=c.text.replaceAll('\n',' ');item.media.classList.add('fv-ed-text-label');item.element.dataset.textKind=c.text_kind;
        // Text after the picture and sound is not exported; that part is drawn faint.
        const stop=model.exportFrames(r),past=Math.max(0,Math.min(c.frames,c.start+c.frames-stop));
        if(past){item.element.dataset.past='true';item.element.style.setProperty('--fv-ed-past',`${(c.frames-past)/c.frames*100}%`);}else{delete item.element.dataset.past;item.element.style.removeProperty('--fv-ed-past');}
        let badge=item.element.querySelector('.fv-ed-text-overflow');
        const over=ready&&metrics&&c.text_kind==='subtitle'&&textLayout(metrics,c.text,styleOf(r,c),'subtitle',r.width,r.height).overTwo;
        if(over&&!badge){badge=el('span','fv-ed-text-overflow','⚠\uFE0E');badge.setAttribute('aria-label',overTwoHint(t));item.element.append(badge);}
        if(badge){badge.hidden=!over;badge.title=overTwoHint(t);}
        item.element.title=over?overTwoHint(t):c.text;
    }
    actions.textClip=updateClip;
    // Track switches stay out of undo (Q9); earlier snapshots get the same switch.
    function switchTrack(id,flag){
        const value=!state.record.tracks.find(v=>v.id===id)?.[flag];
        try{state.settle(r=>setTextTrack(r,id,flag,value));}catch(e){report(e);render();}
    }
    actions.textTrack=(row,track)=>{
        if(!isTextTrack(track))return;
        row.element.dataset.hidden=String(!!track.hidden);row.element.dataset.locked=String(!!track.locked);row.head.dataset.textTrack='true';
        let controls=row.head.querySelector('.fv-ed-text-track-controls');
        if(!controls){controls=el('span','fv-ed-text-track-controls');for(const [flag,en,zh,glyph]of [['hidden','Hide','隐藏','◉'],['locked','Lock','锁定','▣']]){
            const b=button(glyph,{onClick:()=>switchTrack(track.id,flag)});b.dataset.flag=flag;b.setAttribute('aria-label',t(en,zh));b.onpointerdown=e=>e.stopPropagation();controls.append(b);
        }row.head.append(controls);}
        for(const b of controls.children){b.setAttribute('aria-pressed',String(!!track[b.dataset.flag]));b.title=b.getAttribute('aria-label');}
    };
    function textEntries(){return activeText(record(),state.playhead);}
    function frameMap(){
        const r=record(),c=selectedText(),hasSelection=!!c;
        const sw=Number(frame.style.getPropertyValue('--fv-ed-width'))||r.width,sh=Number(frame.style.getPropertyValue('--fv-ed-height'))||r.height;
        if(hasSelection){frame.style.setProperty('--fv-ed-width',String(r.width));frame.style.setProperty('--fv-ed-height',String(r.height));return {x:0,y:0,w:r.width,h:r.height,cw:r.width,ch:r.height};}
        const video=model.at(r,state.playhead).v1?.clip;
        const scale=(video?.fit==='contain'?Math.max:Math.min)(sw/r.width,sh/r.height);
        return {x:(sw-r.width*scale)/2,y:(sh-r.height*scale)/2,w:r.width*scale,h:r.height*scale,cw:sw,ch:sh};
    }
    function paintPicture(r){
        ctx.fillStyle='#000';ctx.fillRect(0,0,r.width,r.height);
        const video=frame.querySelector('video.fv-ed-visible');if(!video||video.readyState<2)return;
        const clip=model.at(r,state.playhead).v1?.clip||{},w=video.videoWidth,h=video.videoHeight;if(!w||!h)return;
        const p=plan(clip,w,h,r.width,r.height),background=plan({...clip,fit:'cover',scale:1.05,offset_x:0,offset_y:0},w,h,r.width,r.height);
        ctx.save();ctx.filter=`blur(${Math.max(1,r.height*.025)}px)`;ctx.transform(...background.matrix);const b=background.crop;ctx.drawImage(video,b.x,b.y,b.width,b.height,b.x,b.y,b.width,b.height);ctx.restore();
        ctx.save();ctx.transform(...p.matrix);const {x,y,width:cw,height:ch}=p.crop;ctx.drawImage(video,x,y,cw,ch,x,y,cw,ch);ctx.restore();
    }
    function render(){
        if(disposed||!ctx)return;
        const count=String(record().tracks.filter(isTextTrack).length),audio2=String(record().tracks.some(t=>t.id==='a2'));
        if(element.dataset.textTracks!==count)element.dataset.textTracks=count;
        if(element.dataset.textAudio2!==audio2)element.dataset.textAudio2=audio2;
        if(!ready&&record().tracks.some(isTextTrack))ensureFonts();
        const entries=textEntries(),c=selectedText(),r=record(),sourcePreview=frame.querySelector('.fv-ed-source-preview');
        canvas.hidden=!ready||(!entries.length&&!c)||!!sourcePreview;
        frame.dataset.textSelected=String(!!c&&!sourcePreview);
        if(canvas.hidden){boundsLayer.replaceChildren();return;}
        mapped=frameMap();const {x,y,w,h,cw,ch}=mapped;
        if(canvas.width!==cw)canvas.width=cw;if(canvas.height!==ch)canvas.height=ch;ctx.clearRect(0,0,cw,ch);
        if(c)paintPicture(r);
        ctx.save();ctx.translate(x,y);ctx.scale(w/r.width,h/r.height);layouts=new Map();
        for(const entry of entries){const l=entry.id===editing?.id ? textLayout(ctx,entry.text,entry.style,entry.text_kind,r.width,r.height) : drawText(ctx,entry,r.width,r.height);layouts.set(entry.id,l);}
        ctx.restore();
        if(editing && layouts.has(editing.id)){const [ix,iy,iw,ih]=layouts.get(editing.id).bounds;Object.assign(editing.input.style,{left:`${clamp((ix/r.width*100)-2,0,78)}%`,top:`${clamp(iy/r.height*100,0,80)}%`,width:`${Math.max(20,Math.min(90,iw/r.width*100+4))}%`,height:`${Math.max(18,ih/r.height*100+8)}%`});}
        if(!editing&&!drag)renderBounds(c);
        if(player.playing&&!raf)raf=requestAnimationFrame(()=>{raf=null;render();});
    }
    function renderBounds(c){
        if(!c||player.playing||locked(c)||!layouts.has(c.id)){boundsLayer.replaceChildren();return;}
        const r=record(),[x,y,w,h]=layouts.get(c.id).bounds;
        let box=boundsLayer.querySelector('.fv-ed-text-box');
        const fresh=!box||box.dataset.clip!==c.id;
        if(fresh){boundsLayer.replaceChildren();box=el('div','fv-ed-text-box');box.dataset.clip=c.id;}
        Object.assign(box.style,{left:`${x/r.width*100}%`,top:`${y/r.height*100}%`,width:`${Math.max(w,8)/r.width*100}%`,height:`${Math.max(h,8)/r.height*100}%`});
        box.setAttribute('aria-label',t('Drag to move; double-click to edit','拖动移动，双击改内容'));box.onpointerdown=e=>beginDrag(e,c);
        if(fresh&&c.text_kind==='text')for(const corner of ['tl','tr','bl','br']){const handle=el('span','fv-ed-text-corner');handle.dataset.corner=corner;box.append(handle);}
        if(fresh)boundsLayer.append(box);
    }
    function startInline(id,selectAll=false){
        if(!ready){ensureFonts().then(()=>{if(ready&&!disposed&&selectedText()?.id===id)startInline(id,selectAll);});return;}
        finishInline();const c=model.find(state.record,id);if(!c||locked(c))return;player.pause();render();
        const input=el('textarea','fv-ed-inline-text');input.value=c.text;input.rows=Math.max(2,c.text.split('\n').length);input.setAttribute('aria-label',t('Content','内容'));
        const s=styleOf(state.record,c),r=state.record,[x,y,w,h]=layouts.get(id)?.bounds||[r.width*.25,r.height*.4,r.width*.5,r.height*.2];
        Object.assign(input.style,{left:`${clamp(x/r.width*100,0,80)}%`,top:`${clamp(y/r.height*100,0,80)}%`,width:`${Math.max(20,Math.min(86,w/r.width*100+4))}%`,height:`${Math.max(18,h/r.height*100+8)}%`,fontFamily:textFontStack(s.font),fontSize:`${s.size*frame.getBoundingClientRect().height}px`,fontWeight:s.bold?'700':'400',textAlign:s.align,color:s.color});
        editing={id,input};boundsLayer.replaceChildren(input);
        input.oninput=()=>updateContent(c,input.value,true);
        input.onkeydown=e=>{e.stopPropagation();if(e.key==='Escape'){e.preventDefault();finishInline();} };input.onkeyup=e=>e.stopPropagation();
        input.onblur=e=>finishInline(e.relatedTarget);render();input.focus();if(selectAll)input.select();
    }
    function finishInline(next){if(!editing)return;const old=editing;editing=null;old.input.onblur=null;const c=model.find(state.record,old.id);
        // Moving to the side panel's Content box keeps typing the same text; an empty one is cleaned up when that box is left.
        if(!old.input.value.trim()&&next?.classList?.contains('fv-ed-text-content')&&selectedText()?.id===old.id)typing=old.id;else if(!old.input.value.trim())endEditing(old.id);else if(c&&c.text!==old.input.value)updateContent(c,old.input.value);else commitDraft();old.input.remove();render();}
    function guide(axis,value){const line=el('span','fv-ed-text-guide '+axis);line.style[axis==='x'?'left':'top']=`${value*100}%`;boundsLayer.append(line);}
    function beginDrag(e,c){
        if(e.button!==0||locked(c)||editing)return;e.preventDefault();e.stopPropagation();
        const s=styleOf(state.record,c),rect=frame.getBoundingClientRect();drag={id:c.id,kind:c.text_kind,style:s,rect,x:e.clientX,y:e.clientY,corner:e.target.dataset.corner,pointer:e.pointerId};
        boundsLayer.setPointerCapture(e.pointerId);actions.gestureStart?.();
    }
    boundsLayer.onpointermove=e=>{
        if(!drag||drag.pointer!==e.pointerId)return;e.preventDefault();const d=drag,dx=(e.clientX-d.x)/d.rect.width,dy=(e.clientY-d.y)/d.rect.height;
        boundsLayer.querySelectorAll('.fv-ed-text-guide').forEach(n=>n.remove());let change;
        if(d.corner){const horizontal=dx*(d.corner.includes('r')?1:-1),vertical=dy*(d.corner.includes('b')?1:-1);change={size:clamp(d.style.size*(1+3*(Math.abs(horizontal)>Math.abs(vertical)?horizontal:vertical)),16/1080,200/1080)};}
        else if(d.kind==='subtitle'){change={margin:clamp(d.style.margin+(d.style.position==='bottom'?-dy:dy),0,1)};}
        else{change={x:clamp(d.style.x+dx,0,1),y:clamp(d.style.y+dy,0,1)};const l=layouts.get(d.id);for(const axis of ['x','y']){
            const half=(l?.bounds[axis==='x'?2:3]||0)/(axis==='x'?state.record.width:state.record.height)/2;
            for(const [target,line]of [[.5,.5],[.05+half,.05],[.95-half,.95]])if(Math.abs(change[axis]-target)*(axis==='x'?d.rect.width:d.rect.height)<6){change[axis]=target;guide(axis,line);break;}
        }}
        modifyStyle(change,true);
        const box=boundsLayer.querySelector('.fv-ed-text-box'),l=layouts.get(d.id);if(box&&l){const [x,y,w,h]=l.bounds;Object.assign(box.style,{left:`${x/state.record.width*100}%`,top:`${y/state.record.height*100}%`,width:`${w/state.record.width*100}%`,height:`${h/state.record.height*100}%`});}
    };
    const endDrag=(e,cancel=false)=>{if(!drag)return;const d=drag;drag=null;if(boundsLayer.hasPointerCapture?.(d.pointer))boundsLayer.releasePointerCapture(d.pointer);if(cancel)draft=null;else commitDraft();actions.gestureEnd?.();render();};
    boundsLayer.onpointerup=e=>endDrag(e);boundsLayer.onpointercancel=e=>endDrag(e,true);boundsLayer.onlostpointercapture=e=>{if(drag)endDrag(e,true);};
    // Pressing the box captures the pointer on this layer, so clicks land here: a double-click is placed by where it falls.
    boundsLayer.ondblclick=e=>{const box=boundsLayer.querySelector('.fv-ed-text-box'),c=box&&model.find(state.record,box.dataset.clip);if(!c)return;
        const r=box.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)return;
        e.preventDefault();if(c.text_kind==='text')startInline(c.id);else panels.get('subtitle')?.content.focus();};
    function contextMenu(e){
        const c=e.target.closest('[data-clip]'),entry=c&&model.find(state.record,c.dataset.clip);
        if(!tracksHost.contains(e.target)||!isText(entry))return;e.preventDefault();e.stopPropagation();commitDraft();
        state.select({kind:'clip',ids:[entry.id]});
        menu.replaceChildren();const addItem=(label,key,run)=>{const b=button(label,{onClick:()=>{menu.hidden=true;run();}});b.setAttribute('role','menuitem');if(key)b.append(el('kbd','',key));menu.append(b);};
        for(const [key,en,zh,kbd]of [['c','Copy','复制','Ctrl/Cmd+C'],['x','Cut','剪切','Ctrl/Cmd+X'],['d','Duplicate','创建副本','Ctrl/Cmd+D'],['delete','Delete','删除','Delete'],['b','Split','剪开','Ctrl/Cmd+B']])addItem(t(en,zh),kbd,()=>commands.run(key));
        const rect=element.getBoundingClientRect();Object.assign(menu.style,{left:`${Math.min(e.clientX-rect.left,rect.width-240)}px`,top:`${Math.min(e.clientY-rect.top,rect.height-220)}px`});menu.hidden=false;
    }
    tracksHost.addEventListener('contextmenu',contextMenu);
    const outside=e=>{if(!menu.contains(e.target))menu.hidden=true;};element.addEventListener('pointerdown',outside);
    const unsubscribe=state.subscribe(event=>{if(['record','selection','playhead'].includes(event.type)){if(editing&&selectedText()?.id!==editing.id)finishInline();if(typing&&document.activeElement?.classList?.contains('fv-ed-text-content')!==true&&selectedText()?.id!==typing){const id=typing;typing=null;endEditing(id);}if(event.type==='record'&&draft){try{draft={...draft,record:draft.change(state.record)};}catch{draft=null;}}render();updateWarnings();}});
    player.setTextRenderer?.(render);
    const resize=typeof ResizeObserver==='function'?new ResizeObserver(()=>render()):null;resize?.observe(frame);
    const seeked=()=>render();frame.addEventListener('seeked',seeked,true);frame.addEventListener('loadeddata',seeked,true);
    let fontReady=null;
    function ensureFonts(){return fontReady ||= ctx&&typeof FontFace!=='undefined'?Promise.all([loadTextFonts(),loadCoverage()]).then(([,value])=>{table=value;ready=true;render();updateWarnings();for(const node of tracksHost.querySelectorAll('.fv-ed-clip[data-text-kind]')){const c=model.find(state.record,node.dataset.clip);if(c)updateClip({clip:c,element:node,media:node.querySelector('.fv-ed-clip-media')},state.record);}}).catch(()=>{loadError=true;updateWarnings();}):Promise.resolve();}
    if(state.record.tracks.some(isTextTrack))ensureFonts();
    async function beforeExport(){
        if(!state.record.tracks.some(isTextTrack))return true;finishInline();commitDraft();await ensureFonts();
        const chars=[...new Set(projectMissing(state.record).flatMap(r=>r.chars))];if(!chars.length&&!loadError)return true;
        const dialog=el('dialog','fv-studio fv-ed-text-export-dialog'),title=el('h3','',t('Export','导出')),message=el('p','',loadError?fontFailure():missingHint(chars,t));
        const close=button(t('Back to edit','返回修改'),{onClick:()=>dialog.close()});dialog.append(title,message);if(loadError)dialog.append(issueButton(t,()=>({where:FONT_ISSUE,message:fontFailure()})));dialog.append(close);document.body.append(dialog);
        const stop=isolateDialogKeys(dialog,(e)=>{if(e.type==='keydown'&&e.key==='Escape'){e.preventDefault();dialog.close();}else if(e.type==='keydown'&&e.key==='Enter'&&!e.target.closest?.('.fv-issue-button')){e.preventDefault();dialog.close();}});
        dialog.onclose=()=>{stop();dialog.remove();jumpMissing();};dialog.showModal();close.focus();return false;
    }
    return {get ready(){return ensureFonts();},beforeExport,speech(value){speech.available=value?.available===true;speech.need=Number(value?.need)||0;if(!speech.busy)speechLabel(null);},flush(){finishInline();commitDraft();},keyboard(e){
        if(isTyping(e.target)||e.ctrlKey||e.metaKey||e.altKey||e.isComposing)return false;
        if(e.key.toLowerCase()==='t'){e.preventDefault();e.stopPropagation();add('text');return true;}
        if(e.key==='Escape'&&(drag||!menu.hidden)){e.preventDefault();if(drag)endDrag(e,true);menu.hidden=true;return true;}return false;
    },dispose(){disposed=true;if(speech.busy){const id=speech.job;speech.run++;speechEnd();if(id)Promise.resolve(actions.speech.cancel(id)).catch(()=>{});}speech.buttons.clear();player.setTextRenderer?.(null);unsubscribe();resize?.disconnect();cancelAnimationFrame(raf);tracksHost.removeEventListener('contextmenu',contextMenu);element.removeEventListener('pointerdown',outside);frame.removeEventListener('seeked',seeked,true);frame.removeEventListener('loadeddata',seeked,true);canvas.remove();boundsLayer.remove();buttons.remove();notice.remove();fontIssue?.remove();menu.remove();}};
}
