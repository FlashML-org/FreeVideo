import {plan, cssTransform, visibleRect} from './editor_geometry.js';
import {differentPictureShape} from './editor_frame.js';
import {PICTURE_DEFAULTS} from './editor_picture_fields.js';
import {pictureSession} from './editor_picture_session.js';
import {setHidden} from './dom_quiet.js';
const metrics = new WeakMap();
export function previewPictureTimings(state, reset = false) {
    const value = metrics.get(state) || {draw: [], layout: []};
    const result = {draw: [...value.draw], layout: [...value.layout]};
    if (reset) { value.draw.length = 0; value.layout.length = 0; }
    return result;
}
const el = (tag, cls) => { const e = document.createElement(tag); e.className = cls; return e; };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const point = (m, x, y) => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
const corners = p => { const c = p.crop; return [[c.x,c.y],[c.x+c.width,c.y],[c.x+c.width,c.y+c.height],[c.x,c.y+c.height]].map(([x,y]) => point(p.matrix,x,y)); };
// Q12: inspection follows the playhead in full; editing always uses output
// coordinates, including a neutral clip and the crop transaction. Fit shows
// what export writes: the edit's frame, the whole picture, a blurred fill.
export function previewPictureFrame(clip, p, mode, editing = false) {
    return editing || clip?.fit === 'contain' || ['move', 'scale', 'crop'].includes(mode)
        ? {mode: 'output', ...p.output} : {mode: 'source', ...p.rotated};
}
// Clip source-pixel corners against the output without changing the frozen
// transform used while cropping. Return source coordinates for CSS clip-path.
export function picturePolygon(p) {
    let polygon = corners(p);
    for (const [axis, bound, sign] of [[0,0,1],[0,p.output.width,-1],[1,0,1],[1,p.output.height,-1]]) {
        const next = [];
        for (let i=0; i<polygon.length; i++) {
            const a=polygon[i], b=polygon[(i+1)%polygon.length], da=sign*(a[axis]-bound), db=sign*(b[axis]-bound);
            if (da>=0) next.push(a);
            if ((da>=0)!==(db>=0)) { const t=da/(da-db); next.push(a.map((v,j)=>v+t*(b[j]-v))); }
        }
        polygon=next;
    }
    const [a,b,c,d,e,f]=p.matrix, det=a*d-b*c;
    return polygon.map(([x,y])=>[(d*(x-e)-c*(y-f))/det,(-b*(x-e)+a*(y-f))/det]);
}
export function snapPicturePosition(value, size, output, pixels, threshold = 6) {
    const candidates = [{value:0,line:.5}, {value:(size/output-1)/2,line:0}, {value:(1-size/output)/2,line:1}];
    const closest = candidates.reduce((a,b) => Math.abs(b.value-value) < Math.abs(a.value-value) ? b : a);
    return Math.abs(closest.value-value) * pixels <= threshold ? closest : {value, line: null};
}
export function createPreviewPicture(host, frame, {state, t}) {
    const session = pictureSession(state, t);
    const bed = el('canvas', 'fv-ed-picture-bed'), outside = el('canvas', 'fv-ed-picture-outside');
    const small = document.createElement('canvas'), padded = document.createElement('canvas');
    const shade = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); shade.classList.add('fv-ed-picture-shade');
    for (const node of [bed, outside, shade]) node.setAttribute('aria-hidden', 'true');
    const shadePath = document.createElementNS(shade.namespaceURI, 'path'); shadePath.setAttribute('fill-rule', 'evenodd'); shade.append(shadePath);
    const handles = el('div', 'fv-ed-picture-handles'), cropBox = el('div', 'fv-ed-picture-crop-box');
    const cropGuide = document.createElementNS(shade.namespaceURI, 'svg');
    cropGuide.classList.add('fv-ed-picture-crop-guide');
    const cropPath = document.createElementNS(shade.namespaceURI, 'path'); cropGuide.append(cropPath); cropBox.append(cropGuide);
    cropPath.onpointerdown = e => start(e, 'crop', 'move');
    const lines = [el('div', 'fv-ed-picture-snap-x'), el('div', 'fv-ed-picture-snap-y')];
    const buttons = [], cropButtons = [];
    for (const direction of ['nw','ne','se','sw']) {
        const b = el('button', 'fv-ed-picture-handle'); b.type = 'button'; b.dataset.corner = direction;
        b.setAttribute('aria-label', `${t('Scale', '缩放')} ${direction.toUpperCase()}`); b.onpointerdown = e => start(e, 'scale', direction);
        b.onkeydown = e => { if (['ArrowUp','ArrowDown'].includes(e.key) && current?.clip) { e.preventDefault(); session.change(current.clip.id, {scale: (current.clip.scale ?? 1) + (e.key === 'ArrowUp' ? 1 : -1) * (e.shiftKey ? .1 : .01)}); } };
        handles.append(b); buttons.push(b);
    }
    for (const direction of ['n','e','s','w','nw','ne','se','sw']) {
        const b = el('button', 'fv-ed-picture-handle'); b.type = 'button'; b.dataset.cropEdge = direction;
        b.setAttribute('aria-label', `${t('Crop', '裁剪')} ${direction.toUpperCase()}`); b.onpointerdown = e => start(e, 'crop', direction);
        b.onkeydown = e => {
            if (!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)) return; e.preventDefault();
            const step = (e.shiftKey ? 10 : 1), dx = e.key === 'ArrowRight' ? step : e.key === 'ArrowLeft' ? -step : 0, dy = e.key === 'ArrowDown' ? step : e.key === 'ArrowUp' ? -step : 0;
            session.preview(cropFields(session.clip(current.clip), direction, dx / current.size.width, dy / current.size.height, current.size));
        };
        cropBox.append(b); cropButtons.push(b);
    }

    frame.prepend(bed); frame.append(shade, handles, cropBox, ...lines); host.append(outside);
    let current = null, layout = null, gesture = null, key = '', disposed = false, observedVideo = null, videoCallback = null;
    let followFrame = 0, inputs = null, drawn = null;
    const samples = [], layouts = []; metrics.set(state, {draw: samples, layout: layouts});
    const recordTime = (list, start) => { list.push(performance.now()-start); if(list.length>240)list.shift(); };
    // No part under the playhead (a gap, or past the end) is never the selected one.
    const isSelected = clip => !!clip && state.selection?.kind === 'clip' && state.selection.ids.includes(clip.id)
        && state.selection.ids.every(id => id === clip.id || id === clip.link);
    const editable = clip => isSelected(clip) && session.canEdit(clip);
    // (Writing a value an element already has still wakes every observer, so only real changes are written.)
    const hide = (node, gone) => { gone = Boolean(gone); if (node.hidden !== gone) node.hidden = gone; };
    function reset() {
        for (const node of [bed, outside, shade, handles, cropBox, ...lines]) setHidden(node, true);
        if (shade.style.display !== 'none') shade.style.display = 'none';
        frame.classList.remove('fv-ed-picture-adjusted'); layout = null; key = ''; inputs = drawn = null;
        frame.querySelectorAll('video').forEach(v => { if (!v.matches('.fv-ed-source-preview')) v.removeAttribute('style'); });
    }
    function watch(video) {
        if (observedVideo === video) return;
        if (observedVideo) { observedVideo.cancelVideoFrameCallback?.(videoCallback); observedVideo.removeEventListener('seeked', draw); observedVideo.removeEventListener('loadeddata', draw); }
        observedVideo = video;
        key = ''; drawn = null;
        if (video) {
            video.addEventListener('seeked', draw); video.addEventListener('loadeddata', draw);
            const next = () => { if (disposed || observedVideo !== video) return; draw(); videoCallback = video.requestVideoFrameCallback?.(next); };
            videoCallback = video.requestVideoFrameCallback?.(next);
        }
    }
    function update(value = current, force = false) {
        const started = performance.now();
        current = value;
        if (!value?.clip || !value.size || value.preview || !value.video) { if (session.draft?.mode === 'crop') session.cancel(); watch(null); reset(); return; }
        if (value.playing && session.draft && session.draft.mode !== 'slider') session.cancel();
        const nextInputs = [value.clip, state.record, session.draft, state.selection, value.video, value.video.dataset.source,
            value.size.width, value.size.height, !!value.playing, session.output(value.clip)];
        // ResizeObserver and the transition follower invalidate geometry. A
        // transport tick alone needs neither style writes nor a layout read.
        if (!force && layout && inputs && nextInputs.every((v, i) => v === inputs[i])) {
            if (!value.video.requestVideoFrameCallback) draw();
            return;
        }
        inputs = nextInputs;
        watch(value.video);
        const clip = {...PICTURE_DEFAULTS, ...session.clip(value.clip)}, record = state.record;
        const cropping = session.draft?.mode === 'crop' && session.draft.id === clip.id;
        let p = plan(clip, value.size.width, value.size.height, record.width, record.height);
        if (cropping) {
            const original = plan(session.draft.original, value.size.width, value.size.height, record.width, record.height);
            p = {...original, crop: p.crop, covers: false};
        }
        const policy = previewPictureFrame(clip, p, session.draft?.mode, session.output(clip));
        frame.dataset.pictureMode = policy.mode;
        if (!frame.classList.contains('fv-ed-picture-adjusted')) frame.classList.add('fv-ed-picture-adjusted');
        frame.style.setProperty('--fv-ed-width', String(policy.width)); frame.style.setProperty('--fv-ed-height', String(policy.height));
        const rect = frame.getBoundingClientRect(), hostRect = host.getBoundingClientRect();
        // The frame eases to a new shape over 150 ms; keep the picture and handles on it each frame until it settles.
        if (!followFrame && frame.getAnimations?.().some(a => a.playState === 'running' || a.pending))
            followFrame = requestAnimationFrame(() => { followFrame = 0; if (!disposed) update(current, true); });
        const oldShade = frame.querySelector('.fv-ed-crop-shade');
        const traditional = policy.mode === 'source' && !clip.rotate && !clip.flip_h && !clip.flip_v && !clip.crop_left && !clip.crop_top && !clip.crop_right && !clip.crop_bottom;
        const different = differentPictureShape({width:Math.round(p.rotated.width),height:Math.round(p.rotated.height)}, record);
        if (oldShade) hide(oldShade, !traditional || !different || clip.fit === 'contain');
        // The Fill/Fit switch sits on the preview area (editor_frame.js shows or hides it); here it only turns gray while the part cannot change.
        const tools = host.querySelector('.fv-ed-picture-tools');
        if (tools) for (const button of tools.querySelectorAll('button')) button.disabled = !session.canEdit(clip);
        const nextKey = JSON.stringify([clip, policy, value.video.dataset.source, rect.width, rect.height, hostRect.width, hostRect.height, isSelected(clip), value.playing]);
        if (!force && nextKey === key) { recordTime(layouts, started); return; } key = nextKey;
        let view = p;
        if (policy.mode === 'source') {
            view = {...p, output: {...p.size}, matrix: [...p.matrix]};
            view.matrix[4] -= p.center.x - p.size.width / 2; view.matrix[5] -= p.center.y - p.size.height / 2;
        }
        const bw = rect.width, bh = rect.height;
        if (bw <= 0 || bh <= 0) return;
        const sx = bw / view.output.width, sy = bh / view.output.height;
        const m = view.matrix.map((v,i) => v * (i % 2 ? sy : sx));
        Object.assign(value.video.style, cssTransform(view, bw, bh));
        if (traditional) value.video.style.objectFit = 'contain';
        // The picture frame already clips the corners (radius token); the video itself is never rounded by a scaled value.
        value.video.style.borderRadius = '0';
        if (view.crop.x === 0 && view.crop.y === 0 && view.crop.width === view.source.width && view.crop.height === view.source.height) value.video.style.clipPath = 'none';
        if (policy.mode === 'output') {
            const polygon = cropping ? picturePolygon(p) : visibleRect(p).polygon;
            value.video.style.clipPath = polygon.length ? `polygon(${polygon.map(([x,y])=>`${x}px ${y}px`).join(',')})` : 'inset(100%)';
        }
        // Existing guide keeps the fit switch; its axis-only shade is replaced here.
        layout = {p, view, policy, clip, bw, bh, m, left: rect.left-hostRect.left, top: rect.top-hostRect.top, hw: hostRect.width, hh: hostRect.height};
        hide(shade, traditional || policy.mode !== 'source' || clip.fit === 'contain');
        { const shown = shade.hidden ? 'none' : ''; if (shade.style.display !== shown) shade.style.display = shown; }
        shade.setAttribute('viewBox', `0 0 ${bw} ${bh}`);
        const path = polygon => polygon.length ? `M${polygon.map(([x,y])=>`${x},${y}`).join('L')}Z` : '';
        const c = view.crop;
        const outer = [[c.x,c.y],[c.x+c.width,c.y],[c.x+c.width,c.y+c.height],[c.x,c.y+c.height]].map(([x,y])=>point(m,x,y));
        const inner = visibleRect(p).polygon.map(([x,y])=>point(m,x,y));
        shadePath.setAttribute('d', path(outer)+path(inner));
        hide(handles, !editable(clip) || value.playing || cropping);
        const pts = corners(view).map(([x,y])=>[x*sx,y*sy]);
        buttons.forEach((b,i) => { b.style.left = `${clamp(pts[i][0], -layout.left+12, layout.hw-layout.left-12)}px`; b.style.top = `${clamp(pts[i][1], -layout.top+12, layout.hh-layout.top-12)}px`; });
        hide(cropBox, !cropping);
        if (cropping) {
            cropGuide.setAttribute('viewBox', `0 0 ${bw} ${bh}`);
            const c = view.crop, at = (x,y) => point(m,c.x+c.width*x,c.y+c.height*y);
            cropPath.setAttribute('d', path([at(0,0),at(1,0),at(1,1),at(0,1)]) + [1/3,2/3].map(v =>
                `M${at(v,0).join(',')}L${at(v,1).join(',')}M${at(0,v).join(',')}L${at(1,v).join(',')}`).join(''));
            cropButtons.forEach(b => {
                const edge=b.dataset.cropEdge, [x,y]=at(edge.includes('w')?0:edge.includes('e')?1:.5,edge.includes('n')?0:edge.includes('s')?1:.5);
                b.style.left=`${clamp(x,-layout.left+12,layout.hw-layout.left-12)}px`;
                b.style.top=`${clamp(y,-layout.top+12,layout.hh-layout.top-12)}px`;
            });
        }
        hide(bed, cropping || policy.mode !== 'output' || p.covers);
        hide(outside, policy.mode !== 'output' || !isSelected(clip));
        draw(); recordTime(layouts, started);
    }
    function sourceDraw(ctx, matrix, video, p) {
        const c = p.crop; ctx.setTransform(...matrix);
        ctx.drawImage(video,c.x,c.y,c.width,c.height,c.x,c.y,c.width,c.height);
        ctx.resetTransform();
    }
    function draw() {
        if (!layout || !current?.video || current.video.readyState < 2 || !current.video.videoWidth || !current.video.videoHeight || (bed.hidden && outside.hidden)) return;
        const start = performance.now(), {p, bw, bh, m, left, top, hw, hh} = layout, video = current.video;
        // loadeddata, seeked and the frame callback can describe the same frame.
        // Keep the existing blur resolution/filter, but rasterize it only once.
        const mediaTime = video.currentTime;
        if (drawn?.video === video && drawn.layout === layout && drawn.time === mediaTime) return;
        drawn = {video, layout, time: mediaTime};
        const cropping = session.draft?.mode === 'crop';
        if (!bed.hidden) {
            const w = Math.max(1, Math.ceil(bw/4)), h = Math.max(1, Math.ceil(bh/4));
            if (small.width !== w || small.height !== h) { small.width=w; small.height=h; }
            const ctx = small.getContext('2d'), c=p.crop, angle=p.rotate*Math.PI/180, co=Math.abs(Math.cos(angle)), si=Math.abs(Math.sin(angle));
            const ratio = Math.max((co*w+si*h)/c.width,(si*w+co*h)/c.height);
            // Normalize the renderer's oriented basis, then cover the opaque
            // rectangle. The bed has no user-scale cap (important for thin crops).
            const [a,b,cc,d] = p.matrix, xscale=ratio/Math.hypot(a,b), yscale=ratio/Math.hypot(cc,d);
            const matrix=[a*xscale,b*xscale,cc*yscale,d*yscale,0,0], cx=c.x+c.width/2,cy=c.y+c.height/2;
            matrix[4]=w/2-matrix[0]*cx-matrix[2]*cy;matrix[5]=h/2-matrix[1]*cx-matrix[3]*cy;
            ctx.clearRect(0,0,w,h); sourceDraw(ctx,matrix,video,p);
            // Extend border pixels before blur: no transparent/black halo.
            const radius=Math.min(bw,bh)/20/4, pad=Math.ceil(radius*3), pw=w+2*pad, ph=h+2*pad;
            if (padded.width!==pw || padded.height!==ph) { padded.width=pw; padded.height=ph; }
            const pc=padded.getContext('2d'); pc.clearRect(0,0,pw,ph); pc.drawImage(small,pad,pad);
            pc.drawImage(small,0,0,w,1,pad,0,w,pad); pc.drawImage(small,0,h-1,w,1,pad,pad+h,w,pad);
            pc.drawImage(padded,pad,0,1,ph,0,0,pad,ph); pc.drawImage(padded,pad+w-1,0,1,ph,pad+w,0,pad,ph);
            if (bed.width!==w || bed.height!==h) { bed.width=w; bed.height=h; }
            const bc=bed.getContext('2d'); bc.clearRect(0,0,w,h); bc.filter=`blur(${radius}px) brightness(0.7)`; bc.drawImage(padded,-pad,-pad); bc.filter='none';
        }
        if (!outside.hidden) {
            const w=Math.ceil(hw), h=Math.ceil(hh);
            if (outside.width!==w || outside.height!==h) { outside.width=w; outside.height=h; }
            const ctx=outside.getContext('2d'); ctx.clearRect(0,0,w,h); ctx.save();
            ctx.beginPath();ctx.rect(0,0,w,h);
            if (cropping) {
                const polygon=picturePolygon(p).map(([x,y])=>point(m,x,y));
                if (polygon.length) { ctx.moveTo(polygon[0][0]+left,polygon[0][1]+top); for (const [x,y] of polygon.slice(1)) ctx.lineTo(x+left,y+top); ctx.closePath(); }
            } else ctx.rect(left,top,bw,bh);
            ctx.clip('evenodd');
            const transform=[...m];transform[4]+=left;transform[5]+=top;
            sourceDraw(ctx,transform,video,cropping ? {...p,crop:{x:0,y:0,...p.source}} : p);ctx.restore();
        }
        recordTime(samples, start);
    }
    function cropFields(base, edge, dx, dy, size) {
        const f={...PICTURE_DEFAULTS,...base};
        if(edge==='move') {
            dx=clamp(dx,-f.crop_left,f.crop_right);dy=clamp(dy,-f.crop_top,f.crop_bottom);
            f.crop_left+=dx;f.crop_right-=dx;f.crop_top+=dy;f.crop_bottom-=dy;
        } else {
            const xMax=Math.min(.9,1-1/size.width), yMax=Math.min(.9,1-1/size.height);
            if(edge.includes('w')) f.crop_left=clamp(f.crop_left+dx,0,xMax-f.crop_right);
            if(edge.includes('e')) f.crop_right=clamp(f.crop_right-dx,0,xMax-f.crop_left);
            if(edge.includes('n')) f.crop_top=clamp(f.crop_top+dy,0,yMax-f.crop_bottom);
            if(edge.includes('s')) f.crop_bottom=clamp(f.crop_bottom-dy,0,yMax-f.crop_top);
        }
        return Object.fromEntries(['left','right','top','bottom'].map(k=>['crop_'+k,f['crop_'+k]]));
    }
    function start(event, mode, edge) {
        if (event.button!==0 || !current || current.playing || !editable(current.clip)) return;
        event.preventDefault();event.stopPropagation();
        const base={...PICTURE_DEFAULTS,...session.clip(current.clip)};
        session.begin(current.clip.id,mode); update(current,true);
        const r=frame.getBoundingClientRect();
        gesture={mode,edge,base,x:event.clientX,y:event.clientY,rect:r,m:layout.m,p:layout.p,pointer:event.pointerId,target:event.currentTarget};
        gesture.target.setPointerCapture?.(event.pointerId);
    }
    const down = event => { if(!event.target.closest('button') && session.draft?.mode!=='crop') start(event,'move'); };
    const move = event => {
        if(!gesture || event.pointerId!==gesture.pointer) return;
        const g=gesture, dx=(event.clientX-g.x)/g.rect.width, dy=(event.clientY-g.y)/g.rect.height;
        if(g.mode==='crop') {
            const [a,b,c,d]=g.m, det=a*d-b*c, px=event.clientX-g.x, py=event.clientY-g.y;
            session.preview(cropFields(g.base,g.edge,(d*px-c*py)/det/current.size.width,(-b*px+a*py)/det/current.size.height,current.size));
        }
        else if(g.mode==='move') {
            const x=snapPicturePosition(g.base.offset_x+dx,g.p.size.width,g.p.output.width,g.rect.width);
            const y=snapPicturePosition(g.base.offset_y+dy,g.p.size.height,g.p.output.height,g.rect.height);
            session.preview({offset_x:x.value,offset_y:y.value});
            lines[0].hidden=x.line===null; lines[1].hidden=y.line===null;
            lines[0].style.left=`${x.line*100}%`;lines[1].style.top=`${y.line*100}%`;
        } else {
            const cx=g.rect.left+g.rect.width*(.5+g.base.offset_x),cy=g.rect.top+g.rect.height*(.5+g.base.offset_y);
            const initial=Math.hypot(g.x-cx,g.y-cy), now=Math.hypot(event.clientX-cx,event.clientY-cy);
            let scale=clamp(g.base.scale*now/Math.max(1,initial),.1,10);if(Math.abs(scale-1)<.025) scale=1;
            session.preview({scale});
        }
    };
    const end = event => {
        if(!gesture || event.pointerId!==gesture.pointer) return;
        const mode=gesture.mode;gesture=null;lines.forEach(l=>l.hidden=true);
        if(event.type==='pointercancel') session.cancel();else if(mode!=='crop') session.commit();
    };
    window.addEventListener('blur', session.cancel);
    frame.addEventListener('pointerdown',down);window.addEventListener('pointermove',move, true);window.addEventListener('pointerup',end, true);window.addEventListener('pointercancel',end, true);
    const unsubscribe=session.subscribe(()=>{if(!session.draft){gesture=null;lines.forEach(l=>l.hidden=true);}update(current,true);});
    const observer=typeof ResizeObserver==='function'?new ResizeObserver(()=>update(current,true)):null;observer?.observe(frame);observer?.observe(host);
    reset();
    return {update, get timings(){return [...samples];}, dispose(){disposed=true;cancelAnimationFrame(followFrame);window.removeEventListener('blur',session.cancel);metrics.delete(state);watch(null);unsubscribe();observer?.disconnect();frame.removeEventListener('pointerdown',down);window.removeEventListener('pointermove',move, true);window.removeEventListener('pointerup',end, true);window.removeEventListener('pointercancel',end, true);[bed,outside,shade,handles,cropBox,...lines].forEach(n=>n.remove());}};
}
