"""Manual text/subtitles: bounded records and pixel-only Pillow compositing.

Only shipped WOFF2s are opened. No subtitle streams, attachments, system font
lookup, speech recognition or network access. Missing glyph checks use exactly
web/editor_text_coverage.js, also consumed by the page.
"""
from functools import lru_cache
from bisect import bisect_right
import hashlib
import json
import logging
import math
from pathlib import Path
import re

TEXT_DEFAULTS = dict(font='sans', size=64/1080, color='#ffffff', backing='outline', bold=True,
                     align='center', x=.5, y=.5, fade=False)
SUBTITLE_DEFAULTS = dict(font='sans', size=44/1080, color='#ffffff', backing='outline', position='bottom', margin=.08)
WEB = Path(__file__).resolve().parents[1] / 'web'


def is_text_track(track):
    return isinstance(track, dict) and track.get('kind') in ('text', 'subtitle')


def has_text_data(record):
    return isinstance(record, dict) and ('subtitle_style' in record or isinstance(record.get('tracks'), list)
                                        and any(is_text_track(t) for t in record['tracks']))


def _check(ok):
    if not ok:
        raise ValueError('Invalid text or subtitle')


def clean_style(raw, kind='text'):
    _check(isinstance(raw, dict))
    base = SUBTITLE_DEFAULTS if kind == 'subtitle' else TEXT_DEFAULTS
    result = {}
    for key, value in raw.items():
        _check(key in base)
        if key == 'font': _check(value in ('sans', 'serif', 'kai', 'mono'))
        elif key == 'size': _check(type(value) in (int, float) and math.isfinite(value) and 16/1080 <= value <= 200/1080)
        elif key == 'color':
            _check(isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value)); value = value.lower()
        elif key == 'backing': _check(value in ('none', 'outline', 'box'))
        elif key == 'align': _check(value in ('left', 'center', 'right'))
        elif key == 'position': _check(value in ('top', 'bottom'))
        elif key in ('bold', 'fade'): _check(type(value) is bool)
        else: _check(type(value) in (int,float) and math.isfinite(value) and 0 <= value <= 1)
        if value != base[key]: result[key] = value
    return result


def validate_text_project(raw, validate_media):
    _check(isinstance(raw.get('tracks'), list) and len(raw['tracks']) <= 9)
    media = {k:v for k,v in raw.items() if k != 'subtitle_style'}
    media['tracks'] = [t for t in raw['tracks'] if not is_text_track(t)]
    result = validate_media(media)
    ids = {c['id'] for t in result['tracks'] for c in t['clips']}
    kinds = set()
    for track in filter(is_text_track, raw['tracks']):
        kind = track['kind']
        _check(kind not in kinds and track.get('id') == ('t1' if kind == 'text' else 's1')); kinds.add(kind)
        _check(isinstance(track.get('clips'), list) and len(track['clips']) <= 500)
        row = dict(id=track['id'], kind=kind, clips=[])
        for flag in ('hidden','locked'):
            _check(flag not in track or type(track[flag]) is bool)
            if track.get(flag): row[flag] = True
        end = 0
        for c in track['clips']:
            _check(isinstance(c,dict))
            identity = c.get('id')
            _check(isinstance(identity,str) and re.fullmatch(r'c[1-9]\d{0,8}', identity) and identity not in ids and int(identity[1:]) <= raw['serial']); ids.add(identity)
            _check(all(type(c.get(k)) is int and low <= c[k] <= 10**7 for k,low in (('start',0),('frames',1))))
            _check(c['start'] >= end and c['start']+c['frames'] <= 10**7); end=c['start']+c['frames']
            _check(c.get('text_kind',kind) == kind)
            text = c.get('text')
            _check(isinstance(text,str) and len(text) <= 8000 and not re.search(r'[\x00-\x08\x0b-\x1f\x7f\ud800-\udfff]',text))
            _check(all(c.get(k) is None for k in ('source','pending','link','part','job','reason')) and c.get('extend',False) is False
                   and ('versions' not in c or isinstance(c['versions'],list) and not c['versions']))
            _check(type(c.get('in',0)) is int and c.get('in',0) == 0 and type(c.get('length',c['frames'])) is int and c.get('length',c['frames']) == c['frames'])
            _check(not any(k in c for k in ('volume','fit','splice','target_frames','text_by','ref','note','take','made_text')))
            clip = dict(id=identity, start=c['start'], frames=c['frames'], length=c['frames'], source=None,
                        link=None, text=text, part=None, pending=None, reason=None, job=None, extend=False, versions=[], text_kind=kind, **{'in':0})
            _check(kind != 'subtitle' or 'text_style' not in c)
            if kind == 'text':
                style = clean_style(c.get('text_style',{}))
                if style: clip['text_style'] = style
            row['clips'].append(clip)
        if row['clips']: result['tracks'].append(row)
    style = clean_style(raw.get('subtitle_style',{}),'subtitle')
    if style and any(t['kind'] == 'subtitle' for t in result['tracks']): result['subtitle_style'] = style
    result['tracks'].sort(key=lambda t: ['v1','a1','a2','a3','a4','a5','a6','t1','s1'].index(t['id']))
    return result


@lru_cache(maxsize=1)
def coverage():
    return json.loads((WEB/'editor_text_coverage.js').read_text().removeprefix('export default ').removesuffix(';\n'))


def missing_glyphs(text, font, bold, table):
    """Pure, stable-order, unique missing characters for this exact face."""
    ranges = table[font]['700' if bold else '400']['ranges']
    def contains(cp):
        index = bisect_right(ranges, [cp, 0x10ffff]) - 1
        return index >= 0 and cp <= ranges[index][1]
    return [c for c in dict.fromkeys(text) if c not in '\n\t' and not contains(ord(c))]


def style_of(project, clip):
    subtitle = clip['text_kind'] == 'subtitle'
    return {**(SUBTITLE_DEFAULTS if subtitle else TEXT_DEFAULTS), **(project.get('subtitle_style',{}) if subtitle else clip.get('text_style',{}))}


def missing_project(project):
    result = []
    for track in project['tracks']:
        if not is_text_track(track) or track.get('hidden'): continue
        for clip in track['clips']:
            style = style_of(project,clip)
            chars = missing_glyphs(clip['text'],style['font'],style.get('bold',False),coverage())
            if chars: result.append(dict(id=clip['id'],chars=chars))
    return result


@lru_cache(maxsize=64)
def _font(name, bold, size):
    from PIL import ImageFont
    entry = coverage()[name]['700' if bold else '400']
    path = WEB/'fonts'/entry['file']
    _verify_font(str(path),entry['sha256'])
    return ImageFont.truetype(str(path), size)


@lru_cache(maxsize=8)
def _verify_font(path, digest):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
        # A replaced font can still render; the cached check warns once per face.
        logging.getLogger(__name__).warning('Text font coverage is out of date; using font: %s', path)


def wrap_text(text, measure, width):
    lines = []
    for paragraph in text.replace('\t','    ').split('\n'):
        line = ''; advance = 0
        for char in paragraph:
            amount = measure(char)
            if line and advance + amount > width:
                lines.append(line); line = ''; advance = 0
            line += char; advance += amount
        lines.append(line)
    return lines


def text_layout(text, style, kind, width, height):
    size = max(1, math.floor(style['size']*height+.5))
    font = _font(style['font'],style.get('bold',False),size)
    measure = lambda c: font.getlength(c)
    all_lines = wrap_text(text, measure, width*.86)
    lines = all_lines[:2] if kind == 'subtitle' else all_lines
    advances = [sum(measure(c) for c in line) for line in lines]
    boxes = []
    for line in lines:
        x = 0; box = None
        for char in line:
            b = font.getbbox(char,anchor='ls')
            if b[2] > b[0] and b[3] > b[1]:
                b = (b[0]+x,b[1],b[2]+x,b[3])
                box = b if box is None else (min(box[0],b[0]),min(box[1],b[1]),max(box[2],b[2]),max(box[3],b[3]))
            x += measure(char)
        boxes.append(box or (0,-size*.8,0,0))
    line_height = size*1.2
    top = min(b[1]+i*line_height for i,b in enumerate(boxes))
    bottom = max(b[3]+i*line_height for i,b in enumerate(boxes))
    stroke = max(1,math.floor(size*.06+.5)) if style['backing'] == 'outline' else 0
    w = max(advances, default=0); h = bottom-top+2*stroke
    x = width*(style.get('x',.5)) - w/2
    if kind == 'subtitle':
        y = height*style['margin'] if style['position'] == 'top' else height*(1-style['margin'])-h
    else: y=height*style['y']-h/2
    align=style.get('align','center')
    positions=[(x+(w-a)*(0 if align == 'left' else 1 if align == 'right' else .5),y+stroke-top+i*line_height) for i,a in enumerate(advances)]
    return dict(lines=lines, positions=positions, bounds=[x-stroke,y,w+2*stroke,h], over_two=len(all_lines)>2, size=size, stroke=stroke, font=font)


def active_text(project, number):
    result=[]
    for kind in ('text','subtitle'):
        for track in project['tracks']:
            if track['kind'] != kind or track.get('hidden'): continue
            for clip in track['clips']:
                if clip['start'] <= number < clip['start']+clip['frames']:
                    style=style_of(project,clip)
                    opacity = min(1,(number-clip['start']+.5)/7.2,(clip['start']+clip['frames']-number-.5)/7.2) if style.get('fade') else 1
                    result.append(dict(clip,style=style,opacity=opacity))
    return result


def paint(pixels, entries, width, height):
    """Pure RGB -> RGB. No entries returns the original array, byte for byte."""
    if not entries: return pixels
    from PIL import Image, ImageDraw
    import numpy as np
    result=Image.fromarray(pixels, 'RGB').convert('RGBA')
    for clip in entries:
        style=clip['style']; layout=text_layout(clip['text'],style,clip['text_kind'],width,height)
        layer=Image.new('RGBA',(width,height)); draw=ImageDraw.Draw(layer)
        x,y,w,h=layout['bounds']; size=layout['size']; opacity=clip.get('opacity',1)
        if style['backing'] == 'box':
            draw.rounded_rectangle((x-size*.5,y-size*.15,x+w+size*.5,y+h+size*.15),radius=size*.15,fill=(0,0,0,153))
        # Per-character placement disables platform-specific kerning and ligatures.
        # Outline is one translucent black layer; the opaque fill is drawn over it.
        for line,(x,y) in zip(layout['lines'],layout['positions']):
            for char in line:
                if layout['stroke']:
                    draw.text((x,y),char,font=layout['font'],anchor='ls',fill=(0,0,0,217),stroke_width=layout['stroke'],stroke_fill=(0,0,0,217))
                draw.text((x,y),char,font=layout['font'],anchor='ls',fill=style['color'])
                x+=layout['font'].getlength(char)
        if opacity < 1: layer.putalpha(layer.getchannel('A').point(lambda p: round(p*opacity)))
        result=Image.alpha_composite(result,layer)
    return np.array(result.convert('RGB'))


def paint_project(pixels, project, number, width, height):
    return paint(pixels,active_text(project,number),width,height)


class MissingGlyphs(ValueError):
    def __init__(self, items):
        self.items = items
        super().__init__('Text has missing glyphs')


def check_export_text(project):
    items = missing_project(project)
    if items:
        raise MissingGlyphs(items)
