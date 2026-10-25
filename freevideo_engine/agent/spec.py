"""Normalize schema-validated job values; derive geometry, sampling and dependencies."""
import json
import math
import re
import secrets
from . import schema as contract
from .length import single_frame_limit

QUALITY, ASPECTS = contract.QUALITY, contract.ASPECTS
KINDS = tuple(contract.KINDS)
FRAMES = ('first', 'last')
# A part that continues a video carries at most this many reference pictures, and no other reference media.
CONTINUED_PICTURES = 3
MAX_SEED = contract.generate()['properties']['seed']['maximum']
JOB_ID = re.compile(contract.JOB['pattern'].strip('^$'))
LIBRARY_ID = re.compile(contract.SAVED['pattern'].strip('^$'))
SLUG = re.compile(contract.SLUG['pattern'].strip('^$'))


class SpecError(ValueError):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__('; '.join('%s: %s' % (e['path'] or '/', e['message']) for e in self.errors))


def fail(path, message):
    raise SpecError(contract.diagnostic(path, message, None))


def import_name(name):
    """Timeline imports accept video/audio content, with no HTTP filesystem paths."""
    from ..comfy_assets import media_kind
    problems = contract.issues(contract.import_upload()['properties']['name'], name, '/name')
    if problems:
        raise SpecError(problems)
    try:
        if media_kind(name) not in ('video', 'audio'):
            raise ValueError
    except ValueError:
        error = SpecError(contract.diagnostic('/name', 'a supported video or audio filename', None))
        error.code = 'invalid_media'
        raise error from None
    return name


# Field checks for values no route schema describes: screenplays and filming records.
def _bool(value, path):
    if type(value) is not bool:
        fail(path, 'must be true or false')
    return value


def _int(value, path, low, high):
    if type(value) is not int or not low <= value <= high:
        fail(path, 'must be an integer from %d to %d' % (low, high))
    return value


def _number(value, path, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        fail(path, 'must be a number from %g to %g' % (low, high))
    return float(value)


def _text(value, path, limit, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit or '\x00' in value:
        fail(path, 'must be a non-empty string of at most %d characters' % limit if not empty
             else 'must be a string of at most %d characters' % limit)
    return value


def check(value, *, local=False):
    """One job as submission accepts it: checked against the shared definition, then normalized.

    local=True admits local file paths, exactly as Jobs.submit does for in-process callers;
    the default is the stricter content-only boundary of external HTTP.
    """
    contract.validate(contract.job_schema(local=local), value)
    return normalize(value)


def new_id():
    return 'j' + secrets.token_hex(6)


def media_item(value, path, *, image=False, frame_default=None, early=False):
    if isinstance(value, str):
        value = {'file': value}
    value = dict(value) if isinstance(value, dict) else value
    if isinstance(value, dict) and frame_default is not None and any(k in value for k in ('from_job', 'library')):
        value.setdefault('frame', frame_default)
    errors = contract.issues(contract.media(image, local=True, early=early), value, path)
    if errors:
        raise SpecError(errors)
    if 'file' in value and re.fullmatch(contract.CHAT_ATTACHMENT_NAME['pattern'], value['file']):
        value['file'] = 'freevideo/agent/assets/' + value['file']
    return value


CUT = re.compile(r'\[Shot\s+\d+\]\s*At\s+(\d{1,2}):(\d{1,2}(?:\.\d+)?)')


def cut_times(text):
    """Seconds of each "[Shot N] At MM:SS.mmm" cut in a prompt; none for a prompt taken from another job."""
    if not isinstance(text, str):
        return []
    return [int(minutes) * 60 + float(seconds) for minutes, seconds in CUT.findall(text)]


def dependencies(item):
    """Job ids a normalized spec reads from."""
    found = set(item.get('after', ()))
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('from_job'), str):
                found.add(value['from_job'])
            for key, child in value.items():
                if key != 'edit':
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk({key: value for key, value in item.items() if key not in ('after', 'metadata')})
    return sorted(found)


def _common(value):
    return {k: value[k] for k in ('label', 'project', 'metadata', 'after') if k in value}


def canvas_size(value):
    if 'aspect' in value or 'megapixels' in value:
        a, b = map(int, value.get('aspect', '16:9').split(':'))
        total = value.get('megapixels', contract.generate()['properties']['megapixels']['default']) * 1024 ** 2
        width = round(math.sqrt(total * a / b) / 32) * 32
        height = round(math.sqrt(total * b / a) / 32) * 32
        properties = contract.generate()['properties']
        if any(contract.issues(properties[k], v) for k, v in (('width', width), ('height', height))):
            fail('/aspect', 'an aspect and megapixels producing dimensions within the width/height contract')
        return width, height
    properties = contract.generate()['properties']
    return tuple(value.get(k, properties[k]['default']) for k in ('width', 'height'))


def sampling(value):
    from .. import tiers
    mac = tiers.on_mac()
    quality = value.get('quality', contract.generate()['properties']['quality']['default'])
    two, base, refine = QUALITY[quality]
    custom = value.get('sampling', {})
    if two and custom.get('two_pass') is False and 'base_steps' not in custom:
        # One pass always samples 20 steps unless the caller gives its own count.
        base = 20
    two, base, refine = custom.get('two_pass', two), custom.get('base_steps', base), custom.get('refine_steps', refine)
    note = None
    if two:
        from ..two_pass import one_pass_note
        # Only the 8-step first pass goes two-pass; any other count is one pass, said once in the answer.
        note = one_pass_note(base)
        two = two and note is None
    # A level's step cache and upscale factor stay only with that level's own plan; custom sampling
    # runs every step and upscales 2x.
    own = QUALITY[quality] == (two, base, refine)
    cache = tiers.step_cache(quality, mac) if own else None
    factor = tiers.upscale_factor(quality, mac) if own and two else 2
    if custom:
        quality = tiers.name_for(base, two, refine, cache, mac, factor) or 'custom'
    return dict(quality=quality, two_pass=two, base_steps=base, refine_steps=refine,
                **({'step_cache': cache} if cache else {}), **({'upscale_factor': factor} if factor != 2 else {}),
                **({'sampling_note': note} if note else {}))


def generation_media(value):
    if not value:
        return {}
    result = {role: media_item(value[role], '/media/' + role, image=True) for role in FRAMES if value.get(role) is not None}
    if value.get('references'):
        result['references'] = [media_item(v, '/media/references/%d' % i, early=True) for i, v in enumerate(value['references'])]
    return result


def still_picture(item):
    """A reference that is one picture: a frame of a video, or a picture file."""
    if isinstance(item, str):
        item = {'file': item}
    if not isinstance(item, dict):
        return False
    if 'frame' in item:
        return True
    from ..comfy_assets import EXTENSIONS
    source = item.get('file') or item.get('path')
    return isinstance(source, str) and source.lower().endswith(tuple(EXTENSIONS['image']))


def task(media):
    if media.get('references'):
        return 'ref2va'
    if 'first' in media and 'last' in media:
        return 'fl2va'
    return 'i2va' if 'first' in media else 'l2va' if 'last' in media else 't2va'


def saved_length(canvas, spec):
    """Saved length: one generation keeps its aligned canvas; multiple blocks are joined to the planned
    whole seconds. A continuation saves only what it adds."""
    from ..chain_plan import PlanError, plan
    continued = bool(spec.get('continue_from'))
    try:
        planned = plan(spec['seconds'], max_frames=single_frame_limit(spec), continued=continued)
    except PlanError:
        return canvas['frames'], canvas['seconds']
    if planned['single'] and not continued:
        return canvas['frames'], canvas['seconds']
    return planned['frames'], planned['frames'] / canvas['fps']


def normalize(value, *, port=None):
    # Local Python callers retain local media refs. HTTP validates its stricter
    # (content-only) schema first; the client adapts local refs before transport.
    contract.validate_job(value, local=True)
    kind = value.get('kind', 'generate')
    value = {k: v for k, v in value.items() if v is not None or k == 'seed'}
    original = value
    value = contract.defaults(contract.KINDS[kind](local=True), value)
    result = dict(kind=kind, **_common(value))
    if kind == 'generate':
        result['force_regenerate'] = value['force_regenerate']
        if value.get('release_gpu_after'):
            result['release_gpu_after'] = True
        if value.get('refine_from'):
            ref = value['refine_from']
            result['refine_from'] = {'from_job' if JOB_ID.fullmatch(ref) else 'library': ref}
        else:
            from ..geometry import geometry
            width, height = canvas_size(original)
            canvas = geometry(width, height, seconds=value['seconds'])
            result.update(prompt=value['prompt'], width=width, height=height, seconds=float(value['seconds']),
                          seed_random=value['seed'] is None,
                          seed=secrets.randbelow(MAX_SEED + 1) if value['seed'] is None else value['seed'],
                          preview=value['preview'], media=generation_media(value.get('media')), **sampling(value))
            result['task'] = task(result['media'])
            if value.get('continue_from'):
                result['continue_from'] = dict(value['continue_from'])
            if value['first_frame_reference']:
                result['first_frame_reference'] = True
            if value.get('part_prompts'):
                result['part_prompts'] = list(value['part_prompts'])
            result['loras'] = [dict(name=row, strength=1.) if isinstance(row, str) else {'strength': 1., **row}
                               for row in value['loras']]
            if port is not None:
                result['single_max_frames'] = single_frame_limit(result, port=port)
            result['frames'], result['output_seconds'] = saved_length(canvas, result)
    elif kind == 'assemble':
        result['clips'] = []
        for row in value['clips']:
            if isinstance(row, str):
                row = {'from_job' if JOB_ID.fullmatch(row) else 'library' if LIBRARY_ID.fullmatch(row) else 'file': row}
            result['clips'].append(dict(row))
        result.update({k: value[k] for k in ('transition', 'transition_seconds', 'width', 'height', 'audio', 'name') if k in value})
    elif kind == 'vlm':
        rows = value['questions'] if isinstance(value['questions'], list) else [value['questions']]
        result['questions'] = []
        for row in rows:
            row = {'text': row} if isinstance(row, str) else row
            item = dict(text=row['text'], images=[media_item(v, '/questions/images', image=True) for v in row.get('images', [])])
            if row.get('video'):
                video = row['video']
                item['frames'] = video.get('frames', 6) if isinstance(video, dict) else 6
                item['video'] = media_item({k: v for k, v in video.items() if k != 'frames'} if isinstance(video, dict) else video, '/questions/video')
            result['questions'].append(item)
        result.update({k: value[k] for k in ('format', 'max_tokens', 'system') if k in value})
    elif kind == 'image':
        width, height = ((value['width'], value['height']) if 'width' in value else
                         contract.IMAGE_ASPECTS[value.get('aspect', '1:1')])
        result.update(prompt=value.get('prompt', ''), width=width, height=height,
                      images=[item if isinstance(item, str) else item['file'] for item in value.get('images', [])])
        if value.get('seed') is not None:
            result['seed'] = value['seed']
        if value.get('people'):
            result['people'] = [dict({k: person[k] for k in ('image', 'cast', 'name', 'facing') if k in person},
                                     x=person.get('x', .5), height=person.get('height', .62),
                                     framing=person.get('framing', 'full'),
                                     feet=person.get('feet', max(.9, person.get('height', .62))))
                                for person in value['people']]
            if value.get('scene_image'):
                result['scene_image'] = value['scene_image']
    elif kind == 'export':
        result.update(editor_project=value['editor_project'], revision=value['revision'])
    elif kind == 'listen':
        result.update(editor_project=value['editor_project'], revision=value['revision'], mode=value['mode'],
                      language=value.get('language', 'auto'), **{k: value[k] for k in ('lines', 'range') if k in value})
    elif kind == 'image_edit':
        result.update(image=value['image'], revision=value['revision'])
    else:
        result.update(text=value['text'], seconds=float(value['seconds']), media=generation_media(value.get('media')))
    result['depends_on'] = dependencies(result)
    return result


def comfy_prompt(spec, node, *, prompt, assets, refine_from=''):
    """The ComfyUI API-format graph for one generate job; media are files in ComfyUI input."""
    from ..lora_triggers import apply_triggers
    prompt = apply_triggers(prompt, spec.get('loras', []))
    inputs = dict(text=prompt, width=spec['width'], height=spec['height'], seconds=spec['seconds'],
                  seed=spec['seed'], two_pass=spec['two_pass'], base_steps=spec['base_steps'],
                  refine_steps=spec['refine_steps'], force_regenerate=spec['force_regenerate'],
                  preview=spec['preview'], refine_from=refine_from)
    if spec.get('first_frame_reference'):
        # Every part of the video carries it; the part that continues nothing ignores it.
        inputs['first_frame_reference'] = True
    if spec.get('step_cache'):
        inputs['step_cache'] = spec['step_cache']
    if spec.get('upscale_factor', 2) != 2 and spec['two_pass'] and not spec['preview']:
        inputs['upscale_factor'] = '%g' % spec['upscale_factor']
    graph = {node: {'class_type': 'FreeVideoGenerate', 'inputs': inputs,
                    '_meta': {'title': 'FreeVideo · ' + (spec.get('label') or 'Agent job')}}}
    if assets:
        graph[node + 'm'] = {'class_type': 'FreeVideoMedia', 'inputs': {'assets': json.dumps(assets)},
                             '_meta': {'title': 'FreeVideo · Media'}}
        inputs['media'] = [node + 'm', 0]
    if spec.get('loras'):
        graph[node + 'l'] = {'class_type': 'FreeVideoLoRAStack',
                             'inputs': {'adapters': json.dumps([dict(r, enabled=True) for r in spec['loras']
                                                               if r.get('enabled', True)])},
                             '_meta': {'title': 'FreeVideo · LoRAs'}}
        inputs['loras'] = [node + 'l', 0]
    return graph


def upscale_prompt(node, preview_video):
    """A second pass of a finished preview; the bridge restores the preview's own request."""
    inputs = dict(text='(continues the preview request)', width=1344, height=768, seconds=10., seed=0,
                  two_pass=True, base_steps=8, refine_steps=3, force_regenerate=False, preview=False,
                  refine_from=preview_video)
    return {node: {'class_type': 'FreeVideoGenerate', 'inputs': inputs, '_meta': {'title': 'FreeVideo · Second pass'}}}
