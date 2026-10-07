import {api} from '../../scripts/api.js';
import {closeDialog} from './motion.js';
import {effortFor, effortName, PRISM_COLORS} from './sampling_effort.js';
// Prism (preview) levels by id (prism_tiers.json), as the report records them.
const PRISM_LEVELS = Object.fromEntries([['light', 'Light', '轻量'], ['standard', 'Standard', '标准'], ['high', 'High', '精细'],
    ['max', 'Max', '极致'], ['original', 'Original', '原版']].map(([id, name, zh], i) => [id, {name, zh, color: PRISM_COLORS[i]}]));

const css=document.createElement('link'); css.rel='stylesheet'; css.href=new URL('./share.css',import.meta.url).href; document.head.append(css);
const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;};
const button=(text,action,cls='fv-quiet')=>{const b=el('button',cls,text);b.type='button';b.onclick=action;return b;};
const identity=r=>/^FreeVideo\/(\d{4}-\d{2}-\d{2}\/[a-f0-9]{32})\/video\.mp4$/.exec(r?.video||'')?.[1];
const image=src=>new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=reject;im.src=src;});
const duration=n=>Number.isFinite(n)&&n>=0?`${n.toFixed(1)} s`:'—';
// ComfyUI reads a PNG's graph from tEXt chunks named workflow and prompt; add them after IHDR.
const crcTable=(()=>{const t=new Uint32Array(256);for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;t[n]=c>>>0;}return t;})();
const crc32=bytes=>{let c=0xffffffff;for(const b of bytes)c=crcTable[(c^b)&255]^(c>>>8);return(c^0xffffffff)>>>0;};
async function withGraph(blob,graph){
    const entries=Object.entries(graph||{}).filter(([key,value])=>['workflow','prompt'].includes(key)&&typeof value==='string'&&value);
    if(!entries.length)return blob;
    const png=new Uint8Array(await blob.arrayBuffer()),end=8+12+new DataView(png.buffer).getUint32(8);
    const chunks=entries.map(([key,text])=>{
        const data=new TextEncoder().encode(key+'\0'+text),chunk=new Uint8Array(12+data.length),view=new DataView(chunk.buffer);
        view.setUint32(0,data.length);chunk.set([116,69,88,116],4);chunk.set(data,8);view.setUint32(8+data.length,crc32(chunk.subarray(4,8+data.length)));
        return chunk;
    });
    return new Blob([png.subarray(0,end),...chunks,png.subarray(end)],{type:'image/png'});
}
const tint=(hex,alpha)=>{const v=parseInt(hex.slice(1),16);return `rgba(${v>>16},${v>>8&255},${v&255},${alpha})`;};
let opened;

// Deterministic Canvas rendering: browser fonts cover CJK and no external
// resources or user prompt are included. The image window preserves every pixel.
export function shareLayout(width,height) {
    const w=width>=height?1200:900, scale=w/width, footer=w>=1000?148:200;
    const h=Math.round(Math.min(1800,height*scale)/2)*2, artWidth=Math.round(h*width/height/2)*2;
    return {width:w,height:h+footer,rect:[Math.floor((w-artWidth)/2),0,artWidth,h],footer};
}

export function drawShareCard(canvas,frame,logo,record,t) {
    const g=record.geometry||{}, shape=shareLayout(g.width||frame?.naturalWidth||1344,g.height||frame?.naturalHeight||768);
    canvas.width=shape.width;canvas.height=shape.height;
    const ctx=canvas.getContext('2d'),w=shape.width,y=shape.rect[3],H=shape.footer,pad=44,wide=w>=1000;
    const p=record.sampling_plan||{},steps=p.base_steps,refine=p.enabled?p.refine_steps:0;
    // Results made with the earlier 8 + 2 default count as Light.
    // Prism (preview) records its own level; H3 levels are matched by their plan.
    const prismLevel=PRISM_LEVELS[p.model==='prism'?p.tier:''];
    const tier=p.model==='prism'?prismLevel:effortFor(steps,!!p.enabled,p.enabled&&steps===8&&refine===2?3:refine), accent=tier?.color||'#8fa3bb';
    ctx.fillStyle='#0b1016';ctx.fillRect(0,0,w,shape.height);ctx.clearRect(...shape.rect);
    const bg=ctx.createLinearGradient(0,y,0,y+H);bg.addColorStop(0,'#131b26');bg.addColorStop(1,'#0b1016');
    ctx.fillStyle=bg;ctx.fillRect(0,y,w,H);
    const glow=ctx.createRadialGradient(w*.86,y+H*.55,0,w*.86,y+H*.55,w*.42);
    glow.addColorStop(0,tint(accent,.16));glow.addColorStop(1,tint(accent,0));ctx.fillStyle=glow;ctx.fillRect(0,y,w,H);
    const line=ctx.createLinearGradient(0,0,w,0);line.addColorStop(0,tint(accent,0));line.addColorStop(.5,tint(accent,.75));line.addColorStop(1,tint(accent,0));
    ctx.fillStyle=line;ctx.fillRect(0,y,w,2);
    if(frame)ctx.drawImage(frame,...shape.rect);
    const font='"Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif';
    const fit=(text,weight,size,max)=>{while(size>12){ctx.font=`${weight} ${size}px ${font}`;if(ctx.measureText(text).width<=max)break;size--;}return ctx.measureText(text).width;};
    const logoWidth=wide?156:140, logoTop=wide?y+H/2-26:y+30;
    ctx.drawImage(logo,pad,logoTop,logoWidth,logoWidth*337/2016);
    const seconds=g.seconds||g.frames/(g.fps||24);
    ctx.font=`400 14px ${font}`;ctx.fillStyle='#6f8399';ctx.textAlign=wide?'left':'right';
    ctx.fillText(`${g.width} × ${g.height}${Number.isFinite(seconds)?' · '+seconds.toFixed(1)+' s':''}`,wide?pad:w-pad,wide?y+H/2+24:y+46);
    ctx.textAlign='left';
    const gpu=(record.gpu||t('GPU not recorded','显卡未记录')).replace(/^NVIDIA\s+/,'').replace(/GeForce\s+/,'');
    const stats=[[t('Generation time','生成耗时'),duration(record.request_seconds)],[t('GPU','显卡'),gpu],
        [t('Quality','质量'),tier?effortName(t,tier):(steps?t('Custom','自定义'):'—'),true]];
    const labelY=wide?y+H/2-16:y+H-80, valueY=wide?y+H/2+26:y+H-38;
    const drawStat=([label,value,pill],x,max)=>{
        ctx.font=`500 13px ${font}`;ctx.fillStyle='#7d90a6';ctx.fillText(label,x,labelY);
        if(!pill){fit(value,600,28,max);ctx.fillStyle='#eef3fa';ctx.fillText(value,x,valueY);return;}
        const text=fit(value,650,20,max-32),pw=text+32;
        ctx.beginPath();ctx.roundRect(x,valueY-27,pw,34,17);ctx.fillStyle=tint(accent,.16);ctx.fill();
        ctx.lineWidth=1.2;ctx.strokeStyle=tint(accent,.6);ctx.stroke();
        ctx.fillStyle=accent;ctx.fillText(value,x+16,valueY-3);
    };
    if(wide){
        // Right-aligned columns; each takes its own width, separated by hairlines.
        const widths=stats.map(([label,value,pill])=>{ctx.font=`500 13px ${font}`;const l=ctx.measureText(label).width;
            const v=pill?Math.min(fit(value,650,20,220),220)+32:Math.min(fit(value,600,28,300),300);return Math.max(l,v);});
        let x=w-pad;
        for(let i=stats.length-1;i>=0;i--){
            x-=widths[i];drawStat(stats[i],x,i===2?220+32:300);
            if(i){x-=28;ctx.fillStyle='#ffffff14';ctx.fillRect(x,labelY-12,1,valueY-labelY+20);x-=28;}
        }
    } else {
        const col=(w-pad*2)/3;
        stats.forEach((stat,i)=>drawStat(stat,pad+col*i,col-20));
    }
    return shape;
}

export function shareButton(record,t){
    const b=button(t('Share','分享'),()=>openShare(record,t),'fv-quiet fv-share-trigger');
    b.hidden=!identity(record);return b;
}

export async function openShare(record,t){
    if(opened?.open){opened.focus();return;}
    const id=identity(record);if(!id)return;
    const dialog=el('dialog','fv-studio fv-share');opened=dialog;dialog.setAttribute('aria-label',t('Share','分享'));dialog.tabIndex=-1;
    const heading=el('header','fv-share-header'),title=el('h2','',t('Share','分享'));
    const close=button('×',()=>closeDialog(dialog),'fv-quiet fv-share-close');close.setAttribute('aria-label',t('Close','关闭'));
    const tabs=el('div','fv-share-tabs');tabs.setAttribute('role','tablist');
    const indicator=el('span','fv-share-indicator');indicator.setAttribute('aria-hidden','true');tabs.append(indicator);
    const slide=()=>{const b=tabs.querySelector('[aria-selected=true]');if(b)indicator.style.cssText=`transform:translateX(${b.offsetLeft-3}px);width:${b.offsetWidth}px`;};
    let kind='image',ready=false,busy=false,disposed=false,metadata,shape,template,first,logo,exported;
    let exportRequest;
    const abort=new AbortController();
    const panel=el('div','fv-share-body'),preview=el('div','fv-share-preview'),canvas=el('canvas'),player=el('video');
    canvas.setAttribute('aria-label',t('Sharing image preview','分享图预览'));
    player.loop=true;player.playsInline=true;player.muted=true;player.controls=true;player.preload='none';player.hidden=true;
    const split=record.video.lastIndexOf('/');
    player.src=api.apiURL('/view?'+new URLSearchParams({filename:record.video.slice(split+1),subfolder:record.video.slice(0,split),type:'output'}));
    preview.append(player,canvas);panel.append(preview);
    const footer=el('footer','fv-share-footer'),message=el('span','fv-share-status');message.setAttribute('role','status');
    const save=button(t('Save image','保存分享图'),download,'fv-primary');save.disabled=true;
    const cancel=button(t('Cancel','取消'),()=>exportRequest?.abort());cancel.hidden=true;
    footer.append(message,cancel,save);heading.append(title,tabs,close);dialog.append(heading,panel,footer);
    const choose=next=>{
        kind=next;tabs.querySelectorAll('button').forEach(b=>{
            b.setAttribute('aria-selected',String(b.dataset.kind===kind));b.tabIndex=b.dataset.kind===kind?0:-1;
        });
        save.textContent=kind==='image'?t('Save image','保存分享图'):t('Save video','保存分享视频');
        slide();
        if(!ready)return;
        drawShareCard(canvas,kind==='image'?first:null,logo,metadata,t);
        player.hidden=kind!=='video';
        if(kind==='video')player.play().catch(()=>{});else player.pause();
    };
    for(const [name,label] of [['image',t('Image','图片')],['video',t('Video','视频')]]){
        const b=button(label,()=>choose(name));b.dataset.kind=name;b.setAttribute('role','tab');b.setAttribute('aria-selected',String(name===kind));b.tabIndex=name===kind?0:-1;tabs.append(b);
    }
    tabs.onkeydown=event=>{
        if(busy||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();choose(event.key==='Home'?'image':event.key==='End'?'video':kind==='image'?'video':'image');
        tabs.querySelector('[aria-selected=true]').focus();
    };
    async function download(){
        if(!ready||busy)return;
        if(kind==='image'){
            canvas.toBlob(async blob=>{if(!blob||disposed)return;blob=await withGraph(blob,metadata?.graph);const url=URL.createObjectURL(blob);const a=el('a');a.href=url;a.download=`FreeVideo_share_${id.split('/')[1].slice(0,12)}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);},'image/png');return;
        }
        busy=true;save.disabled=true;cancel.hidden=false;tabs.querySelectorAll('button').forEach(b=>b.disabled=true);
        message.textContent=t('Preparing sharing video…','正在导出分享视频…');dialog.dataset.busy='true';
        exportRequest=new AbortController();
        try{
            if(!exported){
                const response=await api.fetchApi('/freevideo/share/video',{method:'POST',headers:{'Content-Type':'application/json'},signal:exportRequest.signal,
                    body:JSON.stringify({id,template,rect:shape.rect})});
                if(response.status===409)throw new Error(t('Another export is running. Try again shortly.','已有分享视频正在导出，请稍后再试。'));
                if(!response.ok)throw new Error(t('Export could not complete. Try again.','分享视频导出未完成，请重试。'));
                exported=await response.json();
            }
            if(disposed)return;
            const a=el('a');a.href=api.apiURL('/freevideo/share/video?'+new URLSearchParams(exported));a.download='';a.click();message.textContent='';
        }catch(error){if(!disposed)message.textContent=error.name==='AbortError'?'':error.message;}
        finally{busy=false;if(!disposed){save.disabled=false;cancel.hidden=true;delete dialog.dataset.busy;tabs.querySelectorAll('button').forEach(b=>b.disabled=false);}}
    }
    dialog.addEventListener('cancel',event=>{event.preventDefault();closeDialog(dialog);});
    dialog.onclose=()=>{disposed=true;abort.abort();exportRequest?.abort();player.pause();player.removeAttribute('src');player.load();dialog.remove();if(opened===dialog)opened=null;};
    // Start focus on the dialog itself, not with a focus ring on the first tab.
    document.body.append(dialog);dialog.showModal();dialog.focus();slide();message.textContent=t('Preparing preview…','正在准备预览…');
    try{
        const response=await api.fetchApi('/freevideo/share?'+new URLSearchParams({id}),{signal:abort.signal});
        if(!response.ok)throw new Error(t('This saved video is unavailable.','这个已保存的视频暂时无法读取。'));
        metadata=await response.json();
        [first,logo]=await Promise.all([image(api.apiURL('/freevideo/share/frame?'+new URLSearchParams({id}))),image(new URL('./assets/freevideo.svg',import.meta.url).href)]);
        if(disposed)return;
        if(document.fonts?.ready)await document.fonts.ready;
        shape=drawShareCard(canvas,null,logo,metadata,t);template=canvas.toDataURL('image/png').split(',')[1];
        preview.style.aspectRatio=`${shape.width}/${shape.height}`;
        player.style.cssText=`left:${shape.rect[0]/shape.width*100}%;top:0;width:${shape.rect[2]/shape.width*100}%;height:${shape.rect[3]/shape.height*100}%`;
        ready=true;choose(kind);save.disabled=false;message.textContent='';
    }catch(error){if(!disposed){message.textContent=error.message||t('Preview unavailable. Close and try again.','预览暂时无法读取，请关闭后重试。');dialog.dataset.error='true';}}
}
