// Canvas/Pillow use the same wrapping, baseline and backing geometry.
import {fontCSS, fontStack, loadFonts, FONT_KEYS} from './fonts.js';
let tablePromise;
export function loadCoverage() {
    return tablePromise ||= import('./editor_text_coverage.js').then(module => module.default).catch(e => { tablePromise=null; throw e; });
}
export const loadTextFonts = () => loadFonts(FONT_KEYS.flatMap(k => [[k,400],[k,700]]));
// Text and subtitles never borrow another family: a missing character shows the box, as export refuses it.
export const textFontStack = name => fontStack(name, {alone: true});
export function missingGlyphs(text, font, bold, table) {
    const ranges=table[font][bold ? '700' : '400'].ranges;
    const contains=cp=>{let lo=0,hi=ranges.length;while(lo<hi){const mid=(lo+hi)>>>1;if(ranges[mid][0]<=cp)lo=mid+1;else hi=mid;}return lo>0&&cp<=ranges[lo-1][1];};
    return [...new Set(Array.from(text))].filter(c => !['\n','\t'].includes(c) && !contains(c.codePointAt(0)));
}
export function wrapText(text, measure, width) {
    const lines=[];
    for (const paragraph of text.replaceAll('\t','    ').split('\n')) {
        let line='', advance=0;
        for (const char of paragraph) {
            const amount=measure(char);
            if (line && advance+amount>width) { lines.push(line); line=''; advance=0; }
            line+=char; advance+=amount;
        }
        lines.push(line);
    }
    return lines;
}
export function textLayout(ctx,text,style,kind,width,height) {
    const size=Math.max(1,Math.round(style.size*height));
    ctx.font=fontCSS(style.font, style.bold ? 700 : 400, size, {alone: true});
    ctx.textBaseline='alphabetic'; ctx.textAlign='left'; ctx.fontKerning='none';
    const metric=c=>ctx.measureText(c), measure=c=>metric(c).width;
    const allLines=wrapText(text,measure,width*.86), lines=kind==='subtitle' ? allLines.slice(0,2) : allLines;
    const advances=lines.map(line=>Array.from(line).reduce((x,c)=>x+measure(c),0));
    const boxes=lines.map(line=>{
        let x=0,box=null;
        for(const char of line) {
            const m=metric(char), b=[x-m.actualBoundingBoxLeft,-m.actualBoundingBoxAscent,x+m.actualBoundingBoxRight,m.actualBoundingBoxDescent];
            if(b[2]>b[0] && b[3]>b[1]) box=box ? [Math.min(box[0],b[0]),Math.min(box[1],b[1]),Math.max(box[2],b[2]),Math.max(box[3],b[3])] : b;
            x+=m.width;
        }
        return box || [0,-size*.8,0,0];
    });
    const top=Math.min(...boxes.map((b,i)=>b[1]+i*size*1.2)), bottom=Math.max(...boxes.map((b,i)=>b[3]+i*size*1.2));
    const stroke=style.backing==='outline' ? Math.max(1,Math.round(size*.06)) : 0;
    const w=Math.max(...advances,0), h=bottom-top+stroke*2, x=width*(style.x??.5)-w/2;
    const y=kind==='subtitle' ? style.position==='top' ? height*style.margin : height*(1-style.margin)-h : height*style.y-h/2;
    const align=style.align||'center';
    return {lines,size,stroke,overTwo:allLines.length>2,bounds:[x-stroke,y,w+2*stroke,h],positions:advances.map((a,i)=>[x+(w-a)*(align==='left'?0:align==='right'?1:.5),y+stroke-top+i*size*1.2])};
}
export function drawText(ctx,entry,width,height) {
    const style=entry.style, layout=textLayout(ctx,entry.text,style,entry.text_kind,width,height);
    ctx.save(); ctx.globalAlpha=entry.opacity??1;
    const [x,y,w,h]=layout.bounds,size=layout.size;
    if(style.backing==='box') {
        ctx.fillStyle='rgba(0,0,0,.6)'; ctx.beginPath(); ctx.roundRect(x-size*.5,y-size*.15,w+size,h+size*.3,size*.15); ctx.fill();
    }
    ctx.lineWidth=layout.stroke*2; ctx.lineJoin='round';
    for(let i=0;i<layout.lines.length;i++) {
        let [x,y]=layout.positions[i];
        for(const char of layout.lines[i]) {
            if(layout.stroke) { ctx.strokeStyle='rgba(0,0,0,.85)'; ctx.strokeText(char,x,y); }
            ctx.fillStyle=style.color; ctx.fillText(char,x,y); x+=ctx.measureText(char).width;
        }
    }
    ctx.restore(); return layout;
}
export const overTwoHint=t=>t('Over two lines','超过两行');
export const missingHint=(chars,t)=>chars.length ? t('This font is missing {chars}. Choose another font.','这款字体缺少：{chars}，请换一种字体。', { chars: chars.join(' ') }) : '';
