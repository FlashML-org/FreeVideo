import {api} from '../../scripts/api.js';
import {closeDialog} from './motion.js';
import {SAMPLING_EFFORTS} from './sampling_effort.js';

const css=document.createElement('link'); css.rel='stylesheet'; css.href=new URL('./share.css',import.meta.url).href; document.head.append(css);
const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;};
const button=(text,action,cls='fv-quiet')=>{const b=el('button',cls,text);b.type='button';b.onclick=action;return b;};
const identity=r=>/^FreeVideo\/(\d{4}-\d{2}-\d{2}\/[a-f0-9]{32})\/video\.mp4$/.exec(r?.video||'')?.[1];
const image=src=>new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=reject;im.src=src;});
const duration=n=>Number.isFinite(n)&&n>=0?`${n.toFixed(1)} s`:'—';
const memory=n=>Number.isFinite(n)&&n>=0?`${(n/2**30).toFixed(1)} GiB`:'—';
let opened;

// Deterministic Canvas rendering: browser fonts cover CJK and no external
// resources or user prompt are included. The image window preserves every pixel.
export function shareLayout(width,height) {
    const w=width>=height?1200:900, scale=w/width;
    const h=Math.round(Math.min(1800,height*scale)/2)*2, artWidth=Math.round(h*width/height/2)*2;
    return {width:w,height:h+228,rect:[Math.floor((w-artWidth)/2),0,artWidth,h],footer:228};
}

export function drawShareCard(canvas,frame,logo,record,t) {
    const g=record.geometry||{}, shape=shareLayout(g.width||frame?.naturalWidth||1344,g.height||frame?.naturalHeight||768);
    canvas.width=shape.width;canvas.height=shape.height;
    const ctx=canvas.getContext('2d'),w=shape.width,y=shape.rect[3],pad=40;
    ctx.fillStyle='#101720';ctx.fillRect(0,0,w,shape.height);ctx.clearRect(...shape.rect);
    const bg=ctx.createLinearGradient(0,y,w,shape.height);bg.addColorStop(0,'#192330');bg.addColorStop(1,'#101720');
    ctx.fillStyle=bg;ctx.fillRect(0,y,w,shape.footer);
    if(frame)ctx.drawImage(frame,...shape.rect);
    ctx.drawImage(logo,pad,y+27,168,168*337/2016);
    const font='"Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif';
    const fit=(text,size,max)=>{while(size>12){ctx.font=`500 ${size}px ${font}`;if(ctx.measureText(text).width<=max)break;size--;}return size;};
    const gpu=(record.gpu||t('GPU unavailable','显卡信息未记录')).replace(/^NVIDIA\s+/,'').replace(/GeForce\s+/,'');
    fit(gpu,21,w-320);ctx.fillStyle='#a9b8ca';ctx.textAlign='right';ctx.fillText(gpu,w-pad,y+52);ctx.textAlign='left';
    ctx.fillStyle='#2a3746';ctx.fillRect(pad,y+82,w-pad*2,1);
    const p=record.sampling_plan||{},steps=p.base_steps,refine=p.enabled?p.refine_steps:0;
    const tier=SAMPLING_EFFORTS.find(r=>r.steps===steps&&(!p.enabled||(steps===8&&[2,3].includes(refine))));
    const tierName=tier?.name||(steps?t('Custom','自定义'):'—');
    const unified=record.memory_model==='unified';
    const items=[
        [t('Generation time','生成耗时'),duration(record.request_seconds),t('Sampling ','采样 ')+duration(record.sample_seconds),'#edf3fc'],
        [unified?t('Process RAM peak','进程内存峰值'):t('Peak VRAM · PyTorch','显存峰值 · PyTorch'),
            memory(unified?record.ram_peak_bytes:record.vram_peak_bytes),
            unified?t('Unified memory ','统一内存 ')+memory(record.unified_total_bytes):'RAM '+memory(record.ram_peak_bytes),'#edf3fc'],
        [t('Quality','质量'),tierName,steps?`${steps}${refine?' + '+refine:''} ${t('steps','步')}`:'',tier?.color||'#b4bed0'],
    ];
    const col=(w-pad*2)/3;
    items.forEach(([label,value,detail,color],i)=>{
        const x=pad+col*i;fit(label,18,col-24);ctx.fillStyle='#93a3b8';ctx.fillText(label,x,y+118);
        fit(value,36,col-24);ctx.fillStyle=color;ctx.fillText(value,x,y+163);
        ctx.font=`400 16px ${font}`;ctx.fillStyle='#8595aa';ctx.fillText(detail,x,y+196);
    });
    const seconds=g.seconds||g.frames/(g.fps||24);
    ctx.font=`400 14px ${font}`;ctx.fillStyle='#667b91';ctx.textAlign='right';
    ctx.fillText(`${g.width} × ${g.height}${Number.isFinite(seconds)?' · '+seconds.toFixed(1)+' s':''}`,w-pad,y+216);
    return shape;
}

export function shareButton(record,t){
    const b=button(t('Share','分享'),()=>openShare(record,t),'fv-quiet fv-share-trigger');
    b.hidden=!identity(record);return b;
}

export async function openShare(record,t){
    if(opened?.open){opened.focus();return;}
    const id=identity(record);if(!id)return;
    const dialog=el('dialog','fv-studio fv-share');opened=dialog;dialog.setAttribute('aria-label',t('Share','分享'));
    const heading=el('header','fv-share-header'),title=el('h2','',t('Share','分享'));
    const close=button('×',()=>closeDialog(dialog),'fv-quiet fv-share-close');close.setAttribute('aria-label',t('Close','关闭'));
    const tabs=el('div','fv-share-tabs');tabs.setAttribute('role','tablist');
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
            canvas.toBlob(blob=>{if(!blob||disposed)return;const url=URL.createObjectURL(blob);const a=el('a');a.href=url;a.download=`FreeVideo_share_${id.split('/')[1].slice(0,12)}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);},'image/png');return;
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
    document.body.append(dialog);dialog.showModal();message.textContent=t('Preparing preview…','正在准备预览…');
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
