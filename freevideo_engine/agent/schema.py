"""The sole request contract for runtime, OpenAPI, MCP and CLI (stdlib only).

spec.py only normalizes validated values and derives geometry/dependencies.
Transport-only arguments (local paths, output files, client waits) live here too.
"""
from copy import deepcopy
import json
import math
import re

from . import API_VERSION
from .. import tiers

S = 'https://json-schema.org/draft/2020-12/schema'
PREFIX = '/freevideo/agent/v1'
# (two_pass, base_steps, refine_steps) of each level on this platform; the step
# cache a level adds is tiers.step_cache(name, mac) (high and max use easycache-av),
# its two-pass upscale factor tiers.upscale_factor(name, mac) (medium samples a larger first pass).
QUALITY = {name: tiers.plan(name, mac=tiers.on_mac()) for name in tiers.TIERS}
ASPECTS = {'16:9': (16, 9), '9:16': (9, 16), '1:1': (1, 1), '4:3': (4, 3), '3:4': (3, 4),
           '21:9': (21, 9), '2:3': (2, 3), '3:2': (3, 2)}
IMAGE_ASPECTS = {'1:1': (1024, 1024), '16:9': (1344, 768), '9:16': (768, 1344),
                 '4:3': (1152, 896), '3:4': (896, 1152)}
MAX_UPLOAD_BYTES = 2 * 1024 ** 3
MAX_JSON_UPLOAD_BYTES = 96 * 1024 ** 2
MAX_BODY_BYTES = 1024 * 1024
MAX_CHAT_ATTACHMENT_BYTES = 10 * 1024 ** 2
MAX_CHAT_ATTACHMENTS = 4


def obj(properties=None, required=(), **kw):
    return dict(type='object', additionalProperties=False, properties=properties or {},
                **({'required': list(required)} if required else {}), **kw)


def string(limit=1024, **kw):
    return dict(type='string', minLength=1, maxLength=limit, **({'pattern': r'^(?=[\s\S]*\S)[^\x00]*$'} if 'pattern' not in kw else {}), **kw)


def number(low, high=None, *, integer=False, **kw):
    return dict(type='integer' if integer else 'number', minimum=low,
                **({'maximum': high} if high is not None else {}), **kw)


def array(items, low=0, high=32, **kw):
    return dict(type='array', items=items, minItems=low, maxItems=high, **kw)


def enum(values, **kw):
    return dict(type='string', enum=list(values), **kw)


JOB = string(13, pattern=r'^j[0-9a-f]{12}$')
SESSION = string(13, pattern=r'^s[0-9a-f]{12}$')
# What an agent may say it is doing before it answers. The page owns the words for each key.
STAGES = ('reading', 'scripting', 'storyboarding', 'prompting', 'checking', 'assembling')
STAGE = enum(STAGES, description='The stage of your answer. The page shows its own wording for it.')
PRODUCTION = string(13, pattern=r'^d[0-9a-f]{12}$')
CAST = string(13, pattern=r'^c[0-9a-f]{12}$')
PROJECT = string(34, pattern=r'^e-[0-9a-f]{32}$')
CLIP = string(10, pattern=r'^c[1-9]\d{0,8}$')
EDITOR_FRAME = number(0, 10**7, integer=True, description='Whole timeline frames at 24 fps.')
EDITOR_SOURCE = string(1034, pattern=r'^(?:work:\d{4}-\d{2}-\d{2}/[0-9a-f]{32}|generated:(?!/)(?!.*(?:\\|:))(?!\.{1,2}/|.*/\.{1,2}/)(?!\.editor/|.*/\.editor/)(?!.*//)[^\x00-\x1f]+\.[mM][pP]4|import:[A-Za-z0-9_-]{1,80})$')
SAVED = string(43, pattern=r'^\d{4}-\d{2}-\d{2}/[0-9a-f]{32}$')
SLUG = string(64, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
REF = string(128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$',
             description='Idempotency key; reuse only with the same request content. Batch row refs also name dependencies.')
FILE = string(1024, pattern=r'^(?!/)(?!.*(?:\\|:))(?!.*(?:^|/)\.\.(?:/|$)).+$')
FRAME = {'oneOf': [enum(('first', 'last')), number(0, 86400)]}  # Seconds into the video; the bound keeps it a float.
WAIT = number(0, default=0, description='x-clamp caps accepted wait values at 25 seconds without rejecting larger values.', **{'x-clamp': 25})
CLIENT_WAIT = number(0, 25, default=25)
# Waiting for the user's words is longer: a person types slowly, and each wait is one tool call.
INBOX_WAIT = number(0, default=0, description='x-clamp caps accepted wait values at 50 seconds without rejecting larger values.', **{'x-clamp': 50})
CHAT_ID = string(64, pattern=r'^[A-Za-z0-9_-]{1,64}$')
CHAT_ATTACHMENT_NAME = string(69, pattern=r'^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$')
CHAT_ATTACHMENT = obj(dict(name=CHAT_ATTACHMENT_NAME, original_name=string(255),
                           width=number(1, integer=True), height=number(1, integer=True)),
                      ('name', 'original_name', 'width', 'height'))


def chat_request():
    # Upload metadata supplied by the page is ignored; only the stored name is authoritative.
    attachment = obj(dict(name=CHAT_ATTACHMENT_NAME, original_name={}, width={}, height={}, bytes={}), ('name',))
    return obj(dict(text=dict(type='string', minLength=0, maxLength=8000), id=CHAT_ID,
                    session=SESSION, whitebox={'type': 'boolean'},
                    attachments=array(attachment, 0, MAX_CHAT_ATTACHMENTS)), ('text', 'id'),
               **{'if': {'required': ['attachments'], 'properties': {'attachments': {'minItems': 1}}},
                  'else': {'properties': {'text': string(8000)}}})


EMPTY = obj()


def nullable(rule):
    return {'anyOf': [rule, {'type': 'null'}]}


def pointer(key):
    return str(key).replace('~', '~0').replace('/', '~1')


def edit_recipe():
    from .edit import describe, MAX_OPERATIONS
    operations = []
    required = {'rotate': ['degrees'], 'fit': ['width', 'height'], 'overlay': ['image'], 'text': ['text']}
    for name, description in describe()['operations'].items():
        props = {k: {a: b for a, b in rule.items() if a != 'about'} for k, rule in description['keys'].items()}
        props['op'] = {'const': name}
        if name == 'overlay':
            props['image'] = FILE
        rules = []
        if name in ('pad', 'extend', 'overlay', 'crop'):
            first, other = (('to',), ('left', 'top', 'right', 'bottom')) if name in ('pad', 'extend') else (
                (('scale',), ('width', 'height')) if name == 'overlay' else (('x', 'y', 'width', 'height'), ('anchor', 'zoom')))
            rules.append({'if': {'anyOf': [{'required': [k]} for k in first]},
                          'then': {'not': {'anyOf': [{'required': [k]} for k in other]}}})
        if name == 'shape':
            props['from'] = props['to'] = array({'type': 'number'}, 2, 2)
            rules.append({'if': {'required': ['type'], 'properties': {'type': {'const': 'line'}}},
                          'then': {'required': ['from', 'to']}, 'else': {'required': ['width', 'height']}})
        operations.append(obj(props, ['op'] + required.get(name, []), **({'allOf': rules} if rules else {})))
    return obj({'operations': array({'oneOf': operations}, 1, MAX_OPERATIONS)}, ('operations',),
               description='x-editor-recipe uses the shared CSS colour/ratio parsers. Source geometry and layer references also require editor preflight.',
               **{'x-editor-recipe': True})


def edit_document():
    return {'type': 'object', 'required': ['version', 'layers'], 'properties': {'version': {'const': 2}}, 'description': 'Editor document version 2; x-editor-document uses the renderer pure document validator (geometry and layer relationships).',
            'x-editor-document': True}


def media(image=False, *, local=False, clip=False, video=False, early=False):
    properties = dict(file=FILE, from_job=JOB, library=SAVED, frame=FRAME,
                      edit={'oneOf': [edit_recipe(), edit_document()]}, label=string(80))
    if local:
        properties['path'] = string(4096)
    if early:
        properties['early_steps'] = number(1, 32, integer=True, description=
            'A reference video only: it guides the first pass for this many steps and is dropped afterwards, '
            'including from the second pass. Omitted: the video guides every step.')
    if clip:
        properties.pop('frame')
        properties.pop('edit')
        properties.update(start=number(0, 3600), end=dict(number(0, 3600), type=['number', 'null']))
    if video:
        properties.pop('frame')
        properties.pop('edit')
        properties['frames'] = number(2, 12, integer=True, default=6)
    sources = [k for k in ('file', 'path', 'from_job', 'library') if k in properties]
    rules = [dict(oneOf=[{'required': [key]} for key in sources])]
    if not clip and not video:
        rules.append({'if': {'anyOf': [{'required': [k]} for k in ('file', 'path')]},
                      'then': {'not': {'required': ['frame']}}})
        if image:
            rules.append({'if': {'anyOf': [{'required': [k]} for k in ('from_job', 'library')]},
                          'then': {'required': ['frame']}})
    return {'oneOf': [FILE, obj(properties, allOf=rules)], 'x-media': dict(image=image, clip=clip, video=video),
            'description': 'Exactly one source. x-media additionally checks clip end > start, a comparison JSON Schema cannot express.'}


def common():
    return dict(label=string(120), project=SLUG, metadata={'x-json-bytes': 16 * 1024, 'description': 'x-json-bytes limits serialized finite JSON to 16384 bytes, which JSON Schema cannot measure.'},
                after=array(JOB), ref=REF)


def generation_media(*, local=False, enhance=False):
    return obj({'first': nullable(media(True, local=local)), 'last': nullable(media(True, local=local)),
                'references': nullable(array(media(enhance, local=local, early=not enhance), high=8 if enhance else 32))},
               allOf=[{'if': {'required': ['references'], 'properties': {'references': {'type': 'array', 'minItems': 1}}},
                       'then': {'properties': {'first': {'type': 'null'}, 'last': {'type': 'null'}}}}])


def generate(*, local=False):
    return obj(dict(kind={'const': 'generate', 'type': 'string', 'default': 'generate'},
        prompt={'oneOf': [string(20000), obj({'from_job': JOB}, ('from_job',))]},
        width=number(256, 4096, integer=True, multipleOf=32, default=1344),
        height=number(256, 4096, integer=True, multipleOf=32, default=768),
        aspect=string(7, pattern=r'^[1-9][0-9]{0,2}:[1-9][0-9]{0,2}$', examples=sorted(ASPECTS)),
        megapixels=number(.1, 16, default=.98), seconds=number(1.625, 60, default=10),
        seed=dict(number(0, 2**53 - 1, integer=True), type=['integer', 'null'], default=None),
        quality=enum(QUALITY, default='light'),
        sampling=obj(dict(two_pass={'type': 'boolean'}, base_steps=number(1, 32, integer=True),
                          refine_steps=number(1, 31, integer=True))),
        media=nullable(generation_media(local=local)),
        loras=array({'oneOf': [string(512, pattern=r'^.+\.[sS][aA][fF][eE][tT][eE][nN][sS][oO][rR][sS]$'),
                    obj(dict(name=string(512, pattern=r'^.+\.[sS][aA][fF][eE][tT][eE][nN][sS][oO][rR][sS]$'),
                             strength=number(-4, 4, default=1)), ('name',))]}, default=[]),
        preview={'type': 'boolean', 'default': False},
        release_gpu_after={'type': 'boolean', 'default': False},
        refine_from=nullable({'oneOf': [JOB, SAVED]}), force_regenerate={'type': 'boolean', 'default': False},
        continue_from={'oneOf': [obj({'from_job': JOB}, ('from_job',)), obj({'library': SAVED}, ('library',))]},
        first_frame_reference={'type': 'boolean', 'default': False, 'description':
            'Reduces drift over a continued video: every continuing part also sees the first frame of the whole '
            'video. It holds framing and motion close to that frame.'},
        part_prompts=array(string(20000), 1, 5, description='One description per part of a video made in several '
            'parts, in order, so each part plays its own share of the story. The count must equal the '
            'chain_plan.blocks POST /plan gives for this length. Omitted: FreeVideo splits prompt into parts before the first part.'),
        takes=number(1, 8, integer=True, default=1), **common()),
        allOf=[{'if': {'required': ['refine_from'], 'properties': {'refine_from': {'type': 'string'}}},
                'then': {'properties': {k: False for k in ('prompt', 'width', 'height', 'aspect', 'megapixels',
                    'seconds', 'seed', 'quality', 'sampling', 'media', 'loras', 'preview', 'continue_from',
                    'first_frame_reference', 'part_prompts')}},
                'else': {'required': ['prompt']}},
               {'if': {'anyOf': [{'required': [k]} for k in ('aspect', 'megapixels')]},
                'then': {'not': {'anyOf': [{'required': [k]} for k in ('width', 'height')]}}},
               {'if': {'if': {'required': ['sampling'], 'properties': {'sampling': {'required': ['two_pass']}}},
                       'then': {'properties': {'sampling': {'properties': {'two_pass': {'const': False}}}}},
                       'else': {'required': ['quality'], 'properties': {'quality': {'not': {'enum': [
                           name for name, plan in QUALITY.items() if plan[0]]}}}}},
                'then': {'properties': {'preview': {'const': False}}}},
               # A preview is two-pass, which runs only with 8 first-pass steps: refused when the effective
               # first-pass count is another number, given in sampling or inherited from the quality.
               {'if': {'properties': {'preview': {'const': True}}, 'required': ['preview']},
                # x-derived: issues() names this case at /sampling with the derived sampling checks.
                'then': {'x-derived': True, 'not': {'anyOf': [
                    {'required': ['sampling'], 'properties': {'sampling': {'required': ['two_pass', 'base_steps'],
                        'properties': {'two_pass': {'const': True}, 'base_steps': {'not': {'const': 8}}}}}},
                    {'required': ['sampling'], 'properties': {
                        'sampling': {'required': ['base_steps'], 'not': {'required': ['two_pass']},
                                     'properties': {'base_steps': {'not': {'const': 8}}}},
                        'quality': {'enum': [name for name, plan in QUALITY.items() if plan[0]]}}},
                    {'required': ['sampling', 'quality'], 'properties': {
                        'sampling': {'required': ['two_pass'], 'not': {'required': ['base_steps']},
                                     'properties': {'two_pass': {'const': True}}},
                        'quality': {'enum': [name for name, plan in QUALITY.items() if plan[1] != 8]}}}]}}}],
        description='x-job-kind selects normalization and checks derived geometry and sampling step comparisons; these calculations are not JSON Schema constraints.',
        **{'x-job-kind': 'generate'})


def assemble(*, local=False):
    return obj(dict(kind={'const': 'assemble', 'type': 'string'},
        clips=array({'anyOf': [JOB, SAVED, media(local=local, clip=True)]}, 1, 200),
        transition=enum(('cut', 'crossfade'), default='cut'), transition_seconds=number(.04, 5, default=.5),
        width=nullable(number(64, 4096, integer=True, multipleOf=2)), height=nullable(number(64, 4096, integer=True, multipleOf=2)),
        audio={'type': 'boolean', 'default': True}, name=SLUG, **common()), ('kind', 'clips'),
        **{'x-job-kind': 'assemble'})


def vlm(*, local=False):
    question = {'oneOf': [string(8000), obj(dict(text=string(8000),
        images=array(media(True, local=local), high=16), video=nullable(media(local=local, video=True))), ('text',))]}
    return obj(dict(kind={'const': 'vlm', 'type': 'string'},
        questions={'oneOf': [question, array(question, 1, 8)]}, format=enum(('text', 'json'), default='text'),
        max_tokens=number(16, 1500, integer=True, default=600), system=string(4000), **common()),
        ('kind', 'questions'), description='x-job-kind also checks the sum of images and sampled video frames across questions (at most 24).', **{'x-job-kind': 'vlm'})


def image(*, local=False):
    return obj(dict(kind={'const': 'image', 'type': 'string'},
        prompt=dict(type='string', minLength=1, maxLength=2000),
        width=number(256, 2048, integer=True), height=number(256, 2048, integer=True),
        aspect=enum(IMAGE_ASPECTS, description='Instead of width and height; 1:1 when all three are omitted.'),
        seed=nullable(number(-2**63, 2**64 - 1, integer=True)),
        images=array({'oneOf': [FILE, obj(dict(file=FILE), ('file',))]}, high=4, description='Pictures to change '
            'or combine: input files, such as result.file of an earlier picture job. The prompt calls them image 1, image 2.'),
        people=array(obj(dict(image=FILE, cast=CAST, name=string(40), x=number(0, 1), height=number(.05, 1),
                              feet=number(0, 1), facing=enum(('left', 'right')),
                              framing=enum(('full', 'half'), default='full')),
                         oneOf=[{'required': ['image']}, {'required': ['cast']}]), 1, 3,
            description="First frame with people near the places you say; faces may not match the pictures exactly. "
            "x, height and feet run 0 to 1: x the centre across the width (0.5), height the visible figure's share of the frame "
            "height (0.62; head to waist for half), feet the figure's lower edge from the top (the larger of 0.9 and height). "
            "facing is left or right on screen. Not with images."),
        scene_image=FILE,
        **common()),
        ('kind',), allOf=[{'if': {'not': {'required': ['scene_image']}}, 'then': {'required': ['prompt']}},
               {'if': {'required': ['people']}, 'then': {'properties': {
                   key: number(256, 2048, integer=True, multipleOf=32) for key in ('width', 'height')}}},
               {'if': {'required': ['width']}, 'then': {'required': ['height']}},
               {'if': {'required': ['height']}, 'then': {'required': ['width']}},
               {'if': {'required': ['aspect']},
                'then': {'not': {'anyOf': [{'required': ['width']}, {'required': ['height']}]}}}],
        **{'x-job-kind': 'image'})


def image_result():
    return obj(dict(file=FILE, url=string(), width=number(256, 2048, integer=True),
        height=number(256, 2048, integer=True), seed=number(-2**63, 2**64 - 1, integer=True),
        thumbnail=string()), ('file', 'url', 'width', 'height', 'thumbnail'))


def enhance(*, local=False):
    return obj(dict(kind={'const': 'enhance', 'type': 'string'}, text=string(12000),
        seconds=number(1, 60, default=10), media=nullable(generation_media(local=local, enhance=True)), **common()),
        ('kind', 'text'), **{'x-job-kind': 'enhance'})


def export(*, local=False):
    return obj(dict(kind={'const': 'export', 'type': 'string'},
        editor_project=string(34, pattern=r'^e-[0-9a-f]{32}$', description='The editing project to render (GET /projects).'),
        revision=number(1, 2**53 - 1, integer=True, description='The saved revision you read; a later save fails the job with project_changed.'),
        **common()), ('kind', 'editor_project', 'revision'),
        description='Render one saved revision of an editing project to an MP4 in the saved videos, in turn with the page\'s exports.',
        **{'x-job-kind': 'export'})


LISTEN_FIELDS = dict(
    mode=enum(('align', 'transcribe'), description='align: time the lines you give against the sound; transcribe: write down what is said.'),
    language=string(8, pattern=r'^(auto|[a-z]{2,3})$', default='auto', description='Spoken language, e.g. zh or en; auto detects it.'),
    lines=array(string(2000), 1, 1000, description='align only: the lines to time, in order.'),
    range=obj(dict(start=EDITOR_FRAME, frames=number(1, 10**7, integer=True)), ('start', 'frames'),
              description='Timeline frames to listen to; omit for the whole timeline.'))


def listen(*, local=False):
    return obj(dict(kind={'const': 'listen', 'type': 'string'},
        editor_project=string(34, pattern=r'^e-[0-9a-f]{32}$', description='The editing project whose sound to listen to (GET /projects).'),
        revision=number(1, 2**53 - 1, integer=True, description='The saved revision you read; a later save fails the job with project_changed.'),
        **LISTEN_FIELDS, **common()), ('kind', 'editor_project', 'revision', 'mode'),
        description='Listen to the timeline sound with the local speech model. The result lists each line with its start and end frame; nothing is saved.',
        **{'x-job-kind': 'listen'})


def image_edit(*, local=False):
    return obj(dict(kind={'const': 'image_edit', 'type': 'string'},
        image=dict(IMAGE, description='The image session to render (POST /images).'),
        revision=number(1, 2**53 - 1, integer=True, description='The saved revision you read; a later save fails the job with image_changed.'),
        **common()), ('kind', 'image', 'revision'),
        description='Render one saved revision of an image session to a PNG input file.',
        **{'x-job-kind': 'image_edit'})


KINDS = dict(generate=generate, assemble=assemble, vlm=vlm, enhance=enhance, image=image,
             export=export, listen=listen, image_edit=image_edit)


def job_schema(*, local=False, refs=False):
    value = {'oneOf': [f(local=local) for f in KINDS.values()], 'x-discriminator': 'kind',
             'description': 'x-discriminator selects field diagnostics by kind; all accepted branches use standard oneOf.'}
    return batch_references(value) if refs else value


def batch_references(value):
    if value == JOB:
        return {'anyOf': [JOB, string(129, pattern=r'^@[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]}
    if isinstance(value, dict):
        return {k: batch_references(v) for k, v in value.items()}
    if isinstance(value, list):
        return [batch_references(v) for v in value]
    return value


def batch_schema(*, local=False):
    return obj(dict(jobs=array(job_schema(local=local, refs=True), 1, 64), ref=REF), ('jobs',), description='x-batch additionally checks earlier-row references and at most 64 jobs after takes expansion.', **{'x-batch': True})


def import_upload():
    return obj(dict(name=string(255, pattern=r'^[^/\\:\x00-\x1f]+$'),
                    data_base64=string(MAX_JSON_UPLOAD_BYTES), ref=REF), ('name', 'data_base64'))


def import_view():
    return obj(dict(id=string(80), source=EDITOR_SOURCE, name=string(255), kind=enum(('video', 'audio')),
        state=enum(('preparing', 'ready', 'failed')), fraction=number(0, 1), reason=nullable(string()),
        frames=nullable(number(1, integer=True)), fps=number(1), width=nullable(number(0, integer=True)),
        height=nullable(number(0, integer=True)), preview_width=nullable(number(0, integer=True)),
        preview_height=nullable(number(0, integer=True)), has_audio=nullable({'type': 'boolean'}),
        preview=nullable(string()), thumbnail=nullable(string())), ('id', 'source', 'name', 'kind', 'state', 'fraction'))


def contracts(*, local=False):
    """Every registered route, including the UI control plane, has a contract."""
    def route(summary, body=None, query=None, **kw):
        return dict(summary=summary, body=body, query=query or EMPTY, **kw)
    job = job_schema(local=local)
    source = {'source': media(local=local)}
    routes = {
        ('GET', ''): route('Read available features, readiness and queue'),
        ('GET', '/guide'): route('Read the prompt and API guide'),
        ('GET', '/schema'): route('Read job schemas'),
        ('GET', '/openapi.json'): route('Read the API contract'),
        ('GET', '/capabilities'): route('Read device capabilities', query=obj(dict(
            aspect=dict(generate()['properties']['aspect'], default='16:9'),
            width={k: v for k, v in generate()['properties']['width'].items() if k != 'default'},
            height={k: v for k, v in generate()['properties']['height'].items() if k != 'default'}, task=enum(('t2va', 'i2va', 'l2va', 'fl2va', 'ref2va'), default='t2va'),
            adapters=number(0, 1, integer=True, default=0), references=string(16384)))),
        ('POST', '/plan'): route('Validate and plan a generation', body=job),
        ('POST', '/jobs'): route('Submit generation, prompt enhancement, visual questions or clip assembly',
            body={'oneOf': [job, batch_schema(local=local)], 'x-submission': True, 'description': 'x-submission selects diagnostics for the single job or batch branch.'}, status=201, idempotent=True),
        ('GET', '/jobs'): route('List jobs', query=obj(dict(status=string(128,
            pattern=r'^(waiting|queued|running|cancelling|succeeded|failed|cancelled|interrupted)(,(waiting|queued|running|cancelling|succeeded|failed|cancelled|interrupted))*$'),
            project=SLUG, kind=enum(KINDS), limit=number(1, 200, integer=True, default=50)))),
        ('GET', '/jobs/{id}'): route('Read job progress and result', query=obj({'wait': WAIT})),
        ('GET', '/jobs/{id}/events'): route('Watch job progress'),
        ('POST', '/jobs/{id}/cancel'): route('Cancel a job', body=EMPTY, optional_body=True),
        ('POST', '/gpu/release'): route('Release the video model from the graphics card and memory when idle',
            body=EMPTY, optional_body=True),
        ('POST', '/jobs/{id}/resume'): route('Resume unfinished generation blocks', body=obj({'ref': REF}),
            optional_body=True, idempotent=True),
        ('POST', '/media'): route('Upload image, video or audio content',
            body=obj(dict(name=string(255, pattern=r'^[^/\\:\x00]+$'), data_base64=string(MAX_JSON_UPLOAD_BYTES)),
                     ('name', 'data_base64')), status=201, multipart=True),
        ('POST', '/imports'): route('Import video or audio into the editing timeline; poll GET /imports until ready',
            body=import_upload(), status=201, multipart=True, idempotent=True),
        ('GET', '/imports'): route('List editor imports and preparation progress'),
        ('GET', '/media/{kind}/{file}/file'): route('Download uploaded or derived media'),
        ('POST', '/media/inspect'): route('Inspect media', body=obj(source, ('source',))),
        ('POST', '/media/frame'): route('Extract a video frame', body=obj(dict(source, at=dict(FRAME, default='last')), ('source',)), status=201),
        ('POST', '/media/contact-sheet'): route('Review a grid of video frames', body=obj(dict(source,
            count=number(2, 24, integer=True, default=8), columns=number(1, 8, integer=True, default=4)), ('source',)), status=201),
        ('POST', '/media/edit'): route('Apply an image edit recipe', body=obj(dict(source=media(True, local=local),
            recipe=edit_recipe(), document=edit_document(), target=array(number(16, 8192, integer=True), 2, 2)),
            ('source',), oneOf=[{'required': ['recipe']}, {'required': ['document']}]), status=201),
        ('GET', '/library'): route('Search saved videos', query=obj(dict(limit=number(1, 48, integer=True, default=24),
            before=string(256), query=string(20000), project=SLUG))),
        ('GET', '/library/{day}/{run}'): route('Read a saved video'),
        ('GET', '/library/{day}/{run}/file'): route('Download a saved video'),
        ('GET', '/library/{day}/{run}/thumbnail'): route('Read a saved thumbnail'),
        ('GET', '/queue'): route('Read queue ownership and order'),
        ('GET', '/editor/sources/content'): route(
            'Download project source media or a thumbnail. Use a source key from GET /projects/{id}.',
            query=obj(dict(key=EDITOR_SOURCE, file=enum(('preview', 'thumbnail'))), ('key', 'file'))),
        ('GET', '/connect'): route('Read installation-specific connection instructions',
            query=obj({'format': enum(('text', 'json'), default='text'), 'lang': enum(('zh', 'en'))}),
            auth=False, local=True),
        ('POST', '/connect/hello'): route('Report a connected client', body=obj(dict(client=string(80),
            via=enum(('mcp', 'http'))), ('client', 'via'))),
        ('GET', '/connect/status'): route('Read recent connection status', auth=False, local=True),
        ('GET', '/inbox'): route("Wait at most 50 seconds for the user's words or answers in this session; "
            "again=true means nothing new yet, call it again", query=obj({'wait': INBOX_WAIT})),
    }
    for name, (method, suffix, summary) in DRAMA_OPERATIONS.items():
        shape = drama_schema(name)
        path_fields = {k: PRODUCTION if k == 'id' else SLUG for k in re.findall(r'\{(.*?)\}', suffix)}
        routes[method, suffix] = route(summary, path=obj(path_fields, path_fields), **({'query': shape} if method == 'GET' else
            dict(body=shape, idempotent=True, optional_body=False)))
    for name, (method, suffix, summary) in CAST_OPERATIONS.items():
        shape = cast_schema(name, local=local)
        extra = dict(path=obj({'id': CAST}, ('id',))) if '{id}' in suffix else {}
        if method == 'GET':
            extra['query'] = shape
        else:
            extra.update(body=shape, idempotent=True, optional_body=name in ('delete', 'generate'),
                         **({'status': 201} if name in ('create', 'generate') else {}))
        routes[method, suffix] = route(summary, **extra)
        # The library page edits cards as the user: no token, page session for writes.
        routes[method, '/freevideo/agent' + suffix] = route('Studio cast library: ' + summary[0].lower() + summary[1:],
            auth=False, local=True, **extra)
    for name, (method, suffix, summary) in PROJECT_OPERATIONS.items():
        shape = project_schema(name)
        extra = dict(path=obj({'id': PROJECT}, ('id',))) if '{id}' in suffix else {}
        if method == 'GET':
            extra['query'] = shape
        else:
            extra.update(body=shape, idempotent=True, **({'status': 201} if name in ('create', 'generate', 'export', 'listen') else {}))
        routes[method, suffix] = route(summary, **extra)
    for name, (method, suffix, summary) in WHITEBOX_OPERATIONS.items():
        extra = dict(path=obj({'id': WHITEBOX}, ('id',))) if '{id}' in suffix else {}
        if method == 'GET':
            extra['query'] = whitebox_schema(name)
        else:
            extra.update(body=whitebox_schema(name), status=201)
        routes[method, suffix] = route(summary, **extra)
    for name, (method, suffix, summary) in IMAGE_OPERATIONS.items():
        shape = image_schema(name)
        extra = dict(path=obj({'id': IMAGE}, ('id',))) if '{id}' in suffix else {}
        if method == 'GET':
            extra['query'] = shape
        else:
            extra.update(body=shape, idempotent=True, **({'status': 201} if name in ('create', 'render') else {}))
        routes[method, suffix] = route(summary, **extra)
    for name in ('pause', 'resume'):
        routes['POST', '/freevideo/agent/drama/{id}/' + name] = route(
            'Only the user may stop or continue a film (local Studio control)',
            body=obj({'ref': REF}), path=obj({'id': PRODUCTION}, ('id',)), auth=False, local=True, idempotent=True)
    for suffix, method, body in (('', 'GET', None), ('/session', 'GET', None), ('/activity', 'GET', None),
            ('/activity/cancel', 'POST', obj({'id': JOB}, ('id',))), ('/connect', 'GET', None),
            ('/connect/reset', 'POST', EMPTY), ('/chat', 'GET', None), ('/chat/now', 'GET', None),
            ('/chat/video', 'GET', None), ('/chat/list', 'GET', None), ('/chat/new', 'POST', EMPTY),
            ('/chat/delete', 'POST', obj({'session': SESSION, 'stop': {'type': 'boolean'}}, ('session',))),
            ('/chat/rename', 'POST', obj({'session': SESSION, 'title': {'type': 'string', 'maxLength': 60}}, ('session', 'title'))),
            ('/chat', 'POST', chat_request()),
            ('/chat/abandon', 'POST', obj({'session': SESSION})),
            ('/chat/confirm', 'POST', obj({'id': string(12, pattern=r'^cf[0-9a-f]{10}$'), 'approve': {'type': 'boolean'},
                                           'session': SESSION}, ('id', 'approve')))):
        routes[(method, '/freevideo/agent' + suffix)] = route('Studio Agent controls', body=body, auth=False, local=True,
            lan=suffix in ('/chat', '/chat/now', '/chat/confirm', '/chat/abandon', '/chat/list', '/chat/new', '/chat/video',
                           '/activity', '/activity/cancel'))
    # These existing ComfyUI handlers are registered outside the Agent dispatcher.
    # /chain is a creation POST; monitoring reads /chains and /chain/{id}.
    for suffix in ('/freevideo/progress', '/freevideo/chains', '/freevideo/chain/{id}'):
        routes['GET', suffix] = route('Read generation monitoring', auth=False, lan=True, external=True,
            query=obj({'client_id': string()}) if suffix != '/freevideo/progress' else EMPTY,
            **({'path': obj({'id': string()}, ('id',))} if '{id}' in suffix else {}))
    routes['GET', '/freevideo/agent/activity']['query'] = obj({'cursor': string(80)})
    routes['POST', '/freevideo/agent/chat/attach'] = route(
        'Upload one chat reference image as raw bytes (at most 10 MiB); requires a page cookie and CSRF.',
        query=obj({'name': string(255, pattern=r'^[^/\\:\x00-\x1f]+$')}, ('name',)),
        auth=False, local=True, raw_body=True, status=201)
    routes['GET', '/freevideo/agent/chat/attachment'] = route(
        'Read a saved chat image using the page cookie.', query=obj({'name': CHAT_ATTACHMENT_NAME}, ('name',)),
        auth=False, local=True, page_cookie=True)
    routes['GET', '/freevideo/agent/chat']['query'] = obj({'after': number(0, integer=True), 'session': SESSION})
    routes['GET', '/freevideo/agent/chat/video']['query'] = obj({'id': string(43, pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9a-f]{32}$')}, ('id',))
    for method, suffix, summary, extra in (
            ('GET', '/next', 'The page asks for a white-model scene to render', {}),
            ('GET', '/presets', 'The ready-made white-model scenes the page can try', {}),
            ('POST', '/try', 'The page renders one ready-made white-model scene, named (preset) or picked by the built-in AI for a shot (shot); no model writes code',
             dict(body=obj(dict(preset=string(32, pattern=r'^[a-z0-9-]{1,32}$'), shot=string(4000), session=SESSION)))),
            ('POST', '/{id}/video', 'The page sends the rendered white-model video (the raw mp4 body)',
             dict(path=obj({'id': WHITEBOX}, ('id',)))),
            ('POST', '/{id}/failed', 'The page reports that a white-model scene could not be rendered',
             dict(path=obj({'id': WHITEBOX}, ('id',)), body=obj(dict(message=string(500)), ('message',))))):
        routes[method, '/freevideo/agent/whitebox' + suffix] = route(summary, auth=False, local=True, **extra)
    for suffix in ('', '/connect'):
        routes['GET', '/freevideo/agent' + suffix]['query'] = obj({'lang': enum(('zh', 'en'))})
    for name, (method, suffix, summary) in SESSION_OPERATIONS.items():
        extra = dict(auth=False, page_or_token=True)
        if '{id}' in suffix:
            extra['path'] = obj({'id': SESSION}, ('id',))
        if method == 'GET':
            extra['query'] = session_schema(name)
        else:
            extra.update(body=session_schema(name), optional_body=name in ('stop', 'resume'),
                         **({'status': 201} if name in ('create', 'post') else {}))
        routes[method, suffix] = route(summary, **extra)
    routes['GET', '/confirmations'] = route('Read which agent operations wait for the user\'s approval.',
                                            auth=False, page_or_token=True)
    routes['PUT', '/confirmations'] = route('Only the page user may change which operations need approval.',
                                            body=confirm_settings(), auth=False, page_or_token=True)
    return routes


DRAMA_OPERATIONS = {
    'plan': ('POST', '/drama/plan', 'For a film of several shots with takes to choose from (one video of up to 60 seconds is one generate job): first preview a screenplay and its take counts without saving or submitting'),
    'start': ('POST', '/drama', 'After planning, start a durable film; review sheets and choose each shot'),
    'list': ('GET', '/drama', 'List films; status=active returns unfinished summaries'),
    'get': ('GET', '/drama/{id}', 'Read todo and revision; use since and wait=25. If paused, stop acting and long-wait until the user continues'),
    'sheet': ('GET', '/drama/{id}/shots/{shot}/sheet', 'After takes succeed, review their timed images and the previous chosen last frame'),
    'choose': ('POST', '/drama/{id}/shots/{shot}/choose', 'After reviewing the sheet, choose a take; only then can the next continuation shoot'),
    'reshoot': ('POST', '/drama/{id}/shots/{shot}/reshoot', 'Request corrected takes, then review the sheet and choose again'),
    'finish': ('POST', '/drama/{id}/finish', 'After every shot is chosen, accept assembly; get the film until its output is ready'),
    'cancel': ('POST', '/drama/{id}/cancel', 'Cancel unfinished takes; completed takes remain available'),
    'pause': ('POST', '/drama/{id}/pause', 'Only the user may stop a film; external agents receive not_allowed'),
    'resume': ('POST', '/drama/{id}/resume', 'Only the user may continue a film; external agents receive not_allowed'),
}


# The fields are checked when planning; the shape is told here so an agent does not guess it.
SCREENPLAY = {'type': 'object', 'description': (
    'characters: {key: {card: cast card ID, or name, look, voice, language}}; '
    'shots: [{id, seconds, action or prompt, camera, framing, sound, music (N/A unless the user asks for music in this shot), takes, seed, '
    'cast: [character keys], lines: [{who: character key, line, how}], '
    'continue: true to carry on from the previous shot}]; also title, style, aspect, quality; a whole-film score is not written here but imported onto a sound track.')}


def drama_schema(name):
    if name in ('plan', 'start'):
        return obj(dict(screenplay=SCREENPLAY, takes_per_shot=number(1, 8, integer=True, default=1),
                        director=enum(('agent', 'builtin'), default='agent'), ref=REF), ('screenplay',))
    if name == 'list':
        return obj(dict(status=enum(('active', 'all'), default='all'), limit=number(1, 200, integer=True, default=50)))
    if name == 'get':
        return obj(dict(since=number(1, integer=True), wait=WAIT))
    if name == 'sheet':
        return obj(dict(frames=number(2, 24, integer=True, description='Frames per take; default one per second of the take (6 to 24)')))
    if name == 'choose':
        return obj(dict(take=number(1, integer=True), reason=string(20000), cascade={'type': 'boolean'}, ref=REF), ('take',))
    if name == 'reshoot':
        changes = {key: dict(type='string', maxLength=20000) for key in ('action', 'camera', 'framing', 'prompt')}
        changes.update(seconds=number(1.625, 60), lines=dict(type='array', items={'type': 'object'}))
        return obj(dict(note=string(20000), changes=obj(changes), takes=number(1, 8, integer=True, default=1),
                        reason=string(20000), ref=REF))
    return obj(dict(ref=REF, **({'transition': enum(('cut', 'crossfade'))} if name == 'finish' else {})))


# A card's pictures; each may carry a one-line caption that prompts write beside its <Picture N> tag.
MAX_CAST_IMAGES = 24
MAX_CAST_CAPTION = 200

CAST_OPERATIONS = {
    'create': ('POST', '/cast', 'Create a character or setting card when no existing card fits'),
    'list': ('GET', '/cast', 'Search the cast library before casting; reuse a matching card'),
    'get': ('GET', '/cast/{id}', 'Read a card with its pictures, voice and revision'),
    'update': ('PATCH', '/cast/{id}', 'Change a card; send the revision you read; fields the user changed stay theirs'),
    'delete': ('DELETE', '/cast/{id}', 'Delete a card you made that no unfinished film uses; ask the user otherwise'),
    'generate': ('POST', '/cast/{id}/generate', 'Make reference pictures from the description when the user gave none; get the card after the job ends'),
}


def cast_schema(name, *, local=False):
    """Card requests. Pictures and voices are media references; the server stores input copies."""
    picture = media(True, local=local)
    picture['oneOf'][1]['properties']['url'] = string(2048, description='Ignored; returned card media can be sent back unchanged.')
    sound = media(local=local)
    sound['x-media'] = dict(image=False, clip=False, video=False)
    sound['oneOf'][1]['properties'] = {k: v for k, v in sound['oneOf'][1]['properties'].items() if k not in ('frame', 'edit')}
    sound['oneOf'][1]['properties']['url'] = picture['oneOf'][1]['properties']['url']
    sound['oneOf'][1]['allOf'] = sound['oneOf'][1]['allOf'][:1]
    fields = dict(name=string(120), description=nullable(string(1000)),
                  images=array(obj(dict(media=picture, label=enum(LABELS), caption={'type': 'string', 'maxLength': MAX_CAST_CAPTION,
                      'description': 'Optional: what this picture shows, e.g. "dragon tattoo on her back"; written into prompts. '
                                     '"" removes it.'}),
                      ('media',)), 0, MAX_CAST_IMAGES),
                  voice=nullable(obj(dict(media=sound, start=number(0, 3600, default=0), end=number(0, 3600)), ('media',))))
    if name == 'create':
        return obj(dict(kind=enum(('character', 'setting'), default='character'), **fields, ref=REF), ('name',))
    if name == 'update':
        fields['images']['items']['properties']['label'] = {'type': 'string'}
        return obj(dict(fields, revision=number(1, integer=True), ref=REF))
    if name == 'delete':
        return obj(dict(revision=number(1, integer=True), ref=REF))
    if name == 'generate':
        return obj(dict(face=dict(type='boolean', default=False, description='Also make one face close-up (video; the image model always does).'),
                        source=enum(('image', 'video'), description='Defaults to image when the local image model is ready, otherwise video.'),
                        replace=dict(type='boolean', default=False,
                                     description='Replace pictures the user did not add; default fills empty slots.'),
                        revision=number(1, integer=True), ref=REF))
    if name == 'list':
        return obj(dict(q=string(200), kind=enum(('character', 'setting')), origin=enum(('user', 'agent', 'builtin')),
                        production=PRODUCTION, limit=number(1, 200, integer=True, default=50)))
    return EMPTY


def cast_view():
    picture = obj(dict(media=obj(dict(file=FILE, url=string(2048)), ('file', 'url')),
                       by=enum(('user', 'agent', 'builtin')), from_job=JOB, generated={'type': 'boolean'},
                       label={'type': 'string'}, label_guess={'type': 'boolean'}, caption=string(MAX_CAST_CAPTION)), ('media', 'by', 'label'))
    generating = obj(dict(job=JOB, face_job=nullable(JOB), jobs=array(JOB, 1, 5),
        source=enum(('image', 'video')), replace={'type': 'boolean'}, at=string(),
        by=enum(('user', 'agent', 'builtin'))), ('job', 'face_job', 'replace', 'at', 'by'))
    generated = obj(dict(job=JOB, face_job=nullable(JOB), source=enum(('image', 'video')),
        seed=number(0, 2**53 - 1, integer=True), prompt_version=number(1, integer=True)),
        ('job', 'face_job', 'seed', 'prompt_version'))
    return dict(obj(dict(id=CAST, kind=enum(('character', 'setting')), name=string(120),
        description=nullable(string(1000)), revision=number(1, integer=True),
        images=array(picture, 0, MAX_CAST_IMAGES), candidates=array(picture, 0, 6),
        generating=nullable(generating), generated=nullable(generated),
        generate_error=nullable(obj(dict(code=string(), job=JOB, message=string(), error_zh=string(), error_en=string()), ('code', 'job', 'message')))),
        ('id', 'kind', 'name', 'revision', 'images', 'candidates', 'generating')), additionalProperties=True)


# Image editing sessions: the image editor's version 2 document (web/image_editor_engine.js),
# changed with the page's commands (web/image_commands.js). Shapes follow the page's
# own fields (lineHeight, shape width), not the /media/edit recipe's.
IMAGE = string(34, pattern=r'^i-[0-9a-f]{32}$')
WHITEBOX = string(12, pattern=r'^wb[0-9a-f]{10}$')
LAYER_ID = string(128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
COLOR = string(32, pattern=r'^(?:#[0-9A-Fa-f]{3,4}|#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{8}|transparent)$')
IMAGE_SIDE = number(1, 8192, integer=True)
IMAGE_MAX_PIXELS = 64 * 2**20
IMAGE_LAYERS = 64
BLEND_MODES = ('source-over', 'multiply', 'screen', 'overlay', 'soft-light', 'darken', 'lighten',
               'color-dodge', 'color-burn', 'difference')
ADJUST_RANGES = dict({k: (-100, 100) for k in ('exposure', 'contrast', 'highlights', 'shadows', 'temperature', 'tint',
                     'saturation', 'vibrance', 'vignette')}, hue=(-180, 180), sharpen=(0, 100), blur=(0, 100), noise=(0, 100))
TEXT_FONTS = ('sans', 'serif', 'kai', 'mono')


def _point(low=None, high=None):
    value = number(-10**7 if low is None else low, 10**7 if high is None else high)
    return array(value, 2, 2)


def image_selection():
    rect = dict(x=number(-10**7, 10**7), y=number(-10**7, 10**7), w=number(0, 10**7), h=number(0, 10**7))
    mode = enum(('replace', 'add', 'subtract', 'intersect'), default='replace')
    rect.update(feather=number(0, 2000))
    parts = [obj(dict(shape={'const': 'rect'}, mode=mode, **rect), ('shape', 'x', 'y', 'w', 'h'), description='Rectangle in document pixels.'),
             obj(dict(shape={'const': 'ellipse'}, mode=mode, **rect), ('shape', 'x', 'y', 'w', 'h'), description='Ellipse inside the rectangle, document pixels.'),
             obj(dict(shape={'const': 'all'}, mode=mode), ('shape',), description='The whole document.'),
             obj(dict(shape={'const': 'poly'}, mode=mode, feather=number(0, 2000), pts=array(_point(), 3, 8192)), ('shape', 'pts'), description='Polygon of [x, y] document pixels.'),
             obj(dict(shape={'const': 'wand'}, mode=mode, layer=LAYER_ID, u=number(0, 1), v=number(0, 1),
                      tolerance=number(0, 255, default=0), contiguous={'type': 'boolean', 'default': True},
                      upto=number(0, 4096, integer=True), feather=number(0, 2000)),
                 ('shape', 'u', 'v'), description='Similar colours from a point of a layer, as fractions of the layer.')]
    return obj(dict(parts=array({'oneOf': parts, 'x-discriminator': 'shape'}, 0, 64),
                    feather=number(0, 2000, default=0), invert={'type': 'boolean', 'default': False}), ('parts',))


def image_pixel_op(*, mask=False):
    common_fields = dict(id=string(128), selection=nullable(image_selection()), opacity=number(0, 1, default=1))
    unit = _point(-1, 2)
    tools = ('hide', 'reveal') if mask else ('paint', 'erase', 'clone', 'heal', 'blur', 'hide', 'reveal')
    stroke = dict(tool=enum(tools), pts=array(unit, 1, 8192, description='[u, v] as fractions of the layer.'),
                  size=number(0, 1, exclusiveMinimum=0, description='Brush diameter as a fraction of the layer short side.'),
                  hardness=number(0, 1, default=1), strength=number(0, 1, default=.6), text={'type': 'boolean', 'default': False},
                  color=COLOR, offset=_point(-1, 1))
    ops = [obj(dict(type={'const': 'stroke'}, **common_fields, **stroke), ('type', 'tool', 'pts', 'size')),
           obj(dict(type={'const': 'fill'}, **common_fields, **({} if mask else dict(color=COLOR))), ('type',)),
           obj(dict(type={'const': 'clear'}, **common_fields), ('type',)),
           obj(dict(type={'const': 'gradient'}, **common_fields, **{'from': unit, 'to': unit},
                    shape=enum(('linear', 'radial'), default='linear'), c1=COLOR, c2=COLOR), ('type', 'from', 'to')),
           obj(dict(type={'const': 'fromSelection'}, **common_fields, invert={'type': 'boolean', 'default': False}), ('type',))]
    return {'oneOf': ops, 'x-discriminator': 'type'}


def image_text():
    return obj(dict(content=dict(type='string', maxLength=2000), size=number(6, 2000), color=COLOR,
                    font=enum(TEXT_FONTS, default='sans'), weight={'enum': [400, 700], 'type': 'integer', 'default': 700},
                    align=enum(('left', 'center', 'right'), default='left'), shadow={'type': 'boolean', 'default': True},
                    outline={'anyOf': [{'type': 'boolean'}, COLOR], 'default': False}, lineHeight=number(.5, 4, default=1.25)),
               ('content', 'size'))


def image_shape():
    return obj(dict(type=enum(('rect', 'ellipse', 'line'), default='rect'), fill=nullable(COLOR), stroke=nullable(COLOR),
                    width=number(1, 400, default=6), radius=number(0, 2000, default=0),
                    **{'from': _point(), 'to': _point()}))


def image_layer():
    finite = number(-10**7, 10**7)
    size = number(0, 10**7, exclusiveMinimum=0)
    return obj(dict(
        id=LAYER_ID, kind=enum(('image', 'paint', 'text', 'shape')), name=dict(type='string', maxLength=120),
        visible={'type': 'boolean'}, locked={'type': 'boolean'}, cx=finite, cy=finite, w=size, h=size, nw=size, nh=size,
        rotation=number(-360, 360), flipX={'type': 'boolean'}, flipY={'type': 'boolean'}, opacity=number(0, 1),
        blend=enum(BLEND_MODES), src=nullable(FILE), ops=array(image_pixel_op(), 0, 4096),
        mask=nullable(obj(dict(ops=array(image_pixel_op(mask=True), 0, 4096)), ('ops',))),
        adjust=obj({k: number(low, high) for k, (low, high) in ADJUST_RANGES.items()}),
        copyOf=nullable(obj(dict(layer=LAYER_ID, upto=number(0, 4096, integer=True), selection=nullable(image_selection())), ('layer',))),
        text=image_text(), shape=image_shape()),
        ('id', 'kind', 'cx', 'cy', 'w', 'h', 'nw', 'nh'))


def image_document():
    return obj(dict(
        version={'const': 2, 'type': 'integer'}, width=IMAGE_SIDE, height=IMAGE_SIDE,
        rotate={'enum': [0, 90, 180, 270], 'type': 'integer', 'default': 0},
        flipX={'type': 'boolean', 'default': False}, flipY={'type': 'boolean', 'default': False},
        extend=obj(dict(l=number(0, 20000), t=number(0, 20000), r=number(0, 20000), b=number(0, 20000), color=COLOR), ('l', 't', 'r', 'b')),
        crop=obj(dict(x=number(-10**7, 10**7), y=number(-10**7, 10**7), w=number(0, 10**7, exclusiveMinimum=0),
                      h=number(0, 10**7, exclusiveMinimum=0)), ('x', 'y', 'w', 'h')),
        ratio=string(32, default='original'), layers=array(image_layer(), 1, IMAGE_LAYERS), active=LAYER_ID),
        ('version', 'width', 'height', 'extend', 'crop', 'layers'),
        description='The image editor document (version 2); the page reopens it with every layer and step.',
        **{'x-json-bytes': 8 * 2**20})


IMAGE_OPERATIONS = {
    'create': ('POST', '/images', 'Open a picture for editing as the image editor page does. Send source; the session keeps a frozen copy and a document of layers.'),
    'get': ('GET', '/images/{id}', 'Read an image session: its document of layers, selection and revision. Read the revision and layer IDs before editing.'),
    'write': ('PUT', '/images/{id}', 'Replace the whole document. First get the session and send its revision; on image_changed recompute from error.image.'),
    'operations': ('POST', '/images/{id}/operations', 'Apply 1-64 image edits atomically and save once, with the page\'s own commands. First get revision and layer IDs; on image_changed recompute from error.image.'),
    'render': ('POST', '/images/{id}/render', 'Render one saved revision to a PNG input file. Wait for the returned job, then use its result file as a picture source.'),
}
IMAGE_TOOLS = dict(create='freevideo_image_create', get='freevideo_image_read', write='freevideo_image_write',
                   operations='freevideo_image_operations', render='freevideo_image_render')


WHITEBOX_OPERATIONS = {
    'submit': ('POST', '/whitebox', 'Send the white-model scene code you wrote (build(THREE), see GET /guide). The user\'s open FreeVideo page renders it to a short video that joins the library.'),
    'get': ('GET', '/whitebox/{id}', 'Read a white-model scene: waiting, rendering, ready (work = library ID for media.references with early_steps) or failed (error).'),
}
WHITEBOX_TOOLS = dict(submit='freevideo_whitebox_submit', get='freevideo_whitebox_read')


def whitebox_schema(name):
    if name == 'submit':
        return obj(dict(code=string(65536, description='JavaScript that defines build(THREE) and returns {scene, camera, update(t)}.'),
                        summary=string(500, description='One sentence on what the scene shows, for the confirm card.')), ('code',))
    return obj({'wait': WAIT})


def image_operation_fields():
    """The image commands (IDC): op -> (fields, required, description)."""
    layer_id = LAYER_ID
    update = obj(dict(name=dict(type='string', maxLength=120), visible={'type': 'boolean'}, locked={'type': 'boolean'},
                      cx=number(-10**7, 10**7), cy=number(-10**7, 10**7), w=number(0, 10**7, exclusiveMinimum=0),
                      h=number(0, 10**7, exclusiveMinimum=0), rotation=number(-360, 360), flipX={'type': 'boolean'},
                      flipY={'type': 'boolean'}, opacity=number(0, 1), blend=enum(BLEND_MODES)))
    adjust = obj({k: number(low, high) for k, (low, high) in ADJUST_RANGES.items()})
    text = dict(image_text(), required=[])
    new_layer = obj(dict(kind=enum(('paint', 'image', 'text', 'shape')), name=dict(type='string', maxLength=120),
                         src=FILE, text=image_text(), position=_point(),
                         shape=dict(image_shape(), required=['from', 'to'])), ('kind',),
                    description=('A new layer above the active one; the server makes its id. image needs src (an input file), '
                                 'placed at the canvas centre; text needs text, at position (default the canvas centre); shape needs shape '
                                 'with from and to. Points are document pixels, as layer cx/cy and selections: the '
                                 'picture before turns and flips, its top left 0,0.'))
    shape_patch = obj({k: v for k, v in image_shape()['properties'].items() if k in ('fill', 'stroke', 'width', 'radius')})
    side = number(1, 20000, integer=True)
    return {
        'document.replace': (dict(document={'type': 'object', 'description': 'A version 2 image document, checked as PUT /images/{id} checks it.'}), ('document',), 'Replace the whole document; every layer and picture is checked.'),
        'canvas.resize': (dict(width=side, height=side, keep={'type': 'boolean', 'default': True}), ('width', 'height'), 'Resize the image as Image size does: at most 200% and 64 megapixels; keep keeps the aspect ratio.'),
        'canvas.rotate': (dict(direction=enum(('cw', 'ccw'))), ('direction',), 'Turn the canvas a quarter turn; the crop turns with it.'),
        'canvas.flip': (dict(axis=enum(('horizontal', 'vertical'))), ('axis',), 'Mirror the canvas.'),
        'canvas.crop': (dict(crop=obj(dict(x=number(-10**7, 10**7), y=number(-10**7, 10**7), w=number(0, 10**7, exclusiveMinimum=0), h=number(0, 10**7, exclusiveMinimum=0)), ('x', 'y', 'w', 'h'))), ('crop',), 'Set the crop rectangle in canvas pixels.'),
        'canvas.extend': (dict(extend=obj(dict(l=number(0, 20000), t=number(0, 20000), r=number(0, 20000), b=number(0, 20000), color=COLOR), ('l', 't', 'r', 'b'))), ('extend',), 'Add borders around the canvas, as Canvas size does.'),
        'canvas.ratio': (dict(ratio=enum(('original', 'free', 'video', '16:9', '9:16', '1:1', '4:3', '3:4'))), ('ratio',), 'Set the crop ratio; video uses the session target.'),
        'canvas.pad_to_video': (dict(color=COLOR), (), 'Add borders so the picture fills the session target ratio.'),
        'layer.add': (dict(layer=new_layer), ('layer',), 'Add a layer above the active one and make it active.'),
        'layer.copy': (dict(layer_id=layer_id, name=dict(type='string', maxLength=120)), ('layer_id',), 'Duplicate a layer above itself.'),
        'layer.delete': (dict(layer_id=layer_id), ('layer_id',), 'Delete a layer. The base picture and locked layers cannot be deleted.'),
        'layer.reorder': (dict(layer_id=layer_id, index=number(1, IMAGE_LAYERS - 1, integer=True)), ('layer_id', 'index'), 'Move a layer to an index above the base picture (index 0).'),
        'layer.select': (dict(layer_id=layer_id), ('layer_id',), 'Make a layer active.'),
        'layer.update': (dict(layer_id=layer_id, patch=update), ('layer_id', 'patch'), 'Change name, visibility, lock, place, size, rotation, mirror, opacity or blend.'),
        'layer.adjust': (dict(layer_id=layer_id, patch=adjust), ('layer_id', 'patch'), 'Change colour adjustments of an image or paint layer.'),
        'layer.text': (dict(layer_id=layer_id, patch=text), ('layer_id', 'patch'), 'Change the text, font, size, colour or style of a text layer.'),
        'layer.shape': (dict(layer_id=layer_id, patch=shape_patch), ('layer_id', 'patch'), 'Change the fill, outline or corners of a shape layer.'),
        'selection.set': (dict(selection=image_selection()), ('selection',), 'Replace the selection; it is kept with the session, not in the document.'),
        'selection.add': (dict(part=image_selection()['properties']['parts']['items']), ('part',), 'Add one part to the selection with its mode, as the selection tools do.'),
        'selection.clear': ({}, (), 'Remove the selection.'),
        'selection.all': ({}, (), 'Select the whole document.'),
        'selection.invert': ({}, (), 'Invert the selection.'),
        'selection.copy_to_layer': (dict(layer_id=layer_id, name=dict(type='string', maxLength=120)), ('layer_id',), 'Copy the selected part of a layer (the whole layer without a selection) to a new layer; the selection is cleared.'),
        'selection.fill': (dict(layer_id=layer_id, color=COLOR), ('layer_id',), 'Fill the selection on an image or paint layer.'),
        'selection.erase': (dict(layer_id=layer_id), ('layer_id',), 'Clear the selection on an image or paint layer.'),
        'mask.set': (dict(layer_id=layer_id), ('layer_id',), 'Add a mask to the layer, from the selection when there is one (the selection is then cleared).'),
        'mask.delete': (dict(layer_id=layer_id), ('layer_id',), 'Remove the layer mask.'),
        'mask.from_selection': (dict(layer_id=layer_id), ('layer_id',), 'Replace the layer mask with one that hides what is outside the selection; the selection is cleared.'),
        'pixels.apply': (dict(layer_id=layer_id, pixel_op=image_pixel_op(), target=enum(('layer', 'mask'), default='layer')), ('layer_id', 'pixel_op'), 'Apply a brush stroke, fill, clear or gradient to an image or paint layer. With target mask, a paint stroke hides and an erase stroke shows the layer again, as painting on a mask does in the page.'),
        'undo': ({}, (), 'Undo one step; nothing happens when there is none.'),
        'redo': ({}, (), 'Redo one step; nothing happens when there is none.'),
        'reset': ({}, (), 'Go back to the document the session started with and clear the selection; this is one step and can be undone.'),
    }


def image_schema(name):
    revision = number(1, integer=True, description='The saved revision you read; a later save returns image_changed.')
    if name == 'create':
        target = array(number(16, 8192, integer=True), 2, 2, description='[width, height] of the video the picture is for; ratio video uses it.')
        return obj(dict(source=media(image=True), document=image_document(), target=target, ref=REF), ('source',))
    if name == 'write':
        return obj(dict(revision=revision, document=image_document(), ref=REF), ('revision', 'document'))
    if name == 'render':
        return obj(dict(revision=revision, ref=REF), ('revision',))
    if name != 'operations':
        return EMPTY
    operations = [obj(dict(op={'const': op}, **props), ('op', *required), description=description)
                  for op, (props, required, description) in image_operation_fields().items()]
    return obj(dict(revision=revision, operations=array({'oneOf': operations, 'x-discriminator': 'op'}, 1, 64), ref=REF),
               ('revision', 'operations'), **{'x-json-bytes': 8 * 2**20})

PROJECT_OPERATIONS = {
    'list': ('GET', '/projects', 'List editing projects shared with the editor page. Then get one to read its revision and clip IDs.'),
    'get': ('GET', '/projects/{id}', 'Read a timeline and authenticated source URLs. Read its revision and clip IDs before editing.'),
    'create': ('POST', '/projects', 'Create an editor timeline. Send {"project": {"name": "..."}}; the rest of an empty timeline is filled in.'),
    'write': ('PUT', '/projects/{id}', 'Replace a timeline. First get the project and preserve its id and revision; recompute from error.project on project_changed.'),
    'operations': ('POST', '/projects/{id}/operations', 'Apply 1-64 timeline edits atomically and save once. First get revision and clip IDs; on project_changed recompute from error.project, and on clip_busy wait for generation to finish.'),
    'generate': ('POST', '/projects/{id}/generate', 'Make new picture and sound for selected clips or a frame range, as the page Generate does. First get revision and clip IDs; the parts are reserved at once and fill in as the returned job finishes.'),
    'export': ('POST', '/projects/{id}/export', 'Render one saved revision to an MP4 in the saved videos. First get the revision; wait for the returned job, then read its result video.'),
    'listen': ('POST', '/projects/{id}/listen', 'Listen to the timeline sound for subtitles: mode align times lines you give, transcribe writes down what is said. First get the revision; wait for the returned job, then add the lines with the subtitles edit. Nothing is saved; with no speech model for the language the call fails with no_speech_model, so write the lines by hand.'),
}
PROJECT_TOOLS = dict(list='freevideo_projects', get='freevideo_project_read', create='freevideo_project_create',
                     write='freevideo_project_write', operations='freevideo_editor_operations',
                     generate='freevideo_project_generate', export='freevideo_export',
                     listen='freevideo_project_listen')

SESSION_OPERATIONS = {
    'create': ('POST', '/sessions', 'Create a work conversation for messages and plans shared with the user.'),
    'list': ('GET', '/sessions', 'List work conversations, most recently updated first.'),
    'get': ('GET', '/sessions/{id}', 'Read conversation metadata and whether the user stopped it.'),
    'events': ('GET', '/sessions/{id}/events', 'Read events, including user image attachments (name, original_name, width, height); text may be empty with attachments. Wait up to 25 seconds or request an SSE stream.'),
    'post': ('POST', '/sessions/{id}/events', 'Append a message or plan for the user. Steps are recorded by the server.'),
    'stop': ('POST', '/sessions/{id}/stop', 'Only the page user may stop a work conversation.'),
    'resume': ('POST', '/sessions/{id}/resume', 'Only the page user may continue a work conversation.'),
    'stage': ('POST', '/sessions/{id}/stage', 'Say which stage of the answer you are working on; the page shows its own words for it. Clears when you speak or start a task.'),
}
SESSION_TOOLS = dict(create='freevideo_session_create', list='freevideo_sessions',
                     events='freevideo_session_events', post='freevideo_session_post', stage='freevideo_report_stage')


CONFIRM_DEFAULTS = dict(start=True, reshoot_seconds=300)


def confirm_settings():
    return obj(dict(start=dict(type='boolean', description='Ask before an agent starts shooting a film.'),
                    reshoot_seconds=nullable(number(0, 86400, description='Ask before an agent reshoot estimated to take '
                                                    'at least this long; null never asks.'))))


def session_metadata():
    return obj(dict(session=SESSION, title=nullable(dict(type='string', maxLength=120)),
                    created_at=string(40), client=nullable(string(80)),
                    by=enum(('user', 'agent', 'builtin', 'system'))),
               ('session', 'title', 'created_at', 'client', 'by'))


def session_view():
    fields = session_metadata()['properties'].copy()
    fields.pop('session')
    fields.pop('by')
    fields.update(id=SESSION, updated_at=string(40), last_seq=number(0, integer=True), stopped={'type': 'boolean'})
    return obj(fields, fields)


def session_event(*, stored=False, agent_only=False):
    """One event vocabulary for storage, HTTP, MCP and CLI; writers never supply envelope fields."""
    text = dict(type='string', maxLength=8000)
    variants = [obj(dict(type={'const': 'message'}, text=text), ('type', 'text')),
        obj(dict(type={'const': 'plan'}, shots=array(obj(dict(text=text, seconds=number(0)), ('text',)),
            1, 20000), total_seconds=number(0)), ('type', 'shots'))]
    if not agent_only:
        variants[0]['properties']['mid'] = CHAT_ID
        variants[0]['properties']['attachments'] = array(CHAT_ATTACHMENT, 1, MAX_CHAT_ATTACHMENTS)
        variants[0]['properties']['whitebox'] = {'type': 'boolean', 'description': 'The user chose a white-model preview for this request.'}
        from .errors import ERRORS
        variants += [obj(dict(type={'const': 'step'}, id=string(12, pattern=r'^st[0-9a-f]{10}$'),
            tool=string(200), summary=text, state=enum(('started', 'done', 'failed')),
            refs=obj(dict(drama=PRODUCTION, shot=SLUG, take=number(1, integer=True), job=JOB,
                          project=PROJECT, clip=CLIP, image=IMAGE, cast=CAST)), thumbs=array(SAVED, 0, 20000),
            why=text, error=enum(ERRORS)), ('type', 'tool', 'summary', 'state', *(['id'] if stored else []))),
            obj(dict(type={'const': 'confirm'}, id=string(12, pattern=r'^cf[0-9a-f]{10}$'),
                question=text, estimate_seconds=number(0), state=enum(('asked', 'approved', 'declined')),
                width=number(0, integer=True), height=number(0, integer=True), seconds=number(0),
                action=enum(('start', 'reshoot')), refs=obj(dict(drama=PRODUCTION, shot=SLUG)),
                whitebox=obj(dict(work=SAVED, seconds=number(0), summary=text), ('work',),
                             description='The rendered white-model preview the user is asked to approve.'),
                digest=string(64, pattern=r'^[0-9a-f]{64}$')),
                ('type', 'id', 'state'), **{'if': {'properties': {'state': {'const': 'asked'}}},
                                        'then': {'required': ['question']}}),
            obj(dict(type={'const': 'control'}, action=enum(('stopped', 'resumed', 'renamed', 'abandoned')),
                title=dict(type='string', maxLength=120)), ('type', 'action'),
                **{'if': {'properties': {'action': {'const': 'renamed'}}},
                   'then': {'required': ['title']}, 'else': {'not': {'required': ['title']}}})]
    if stored:
        for variant in variants:
            variant['properties'].update(seq=number(1, integer=True), at=string(40),
                by=enum(('user', 'agent', 'builtin', 'system')), client=string(80))
            variant['required'] += ['seq', 'at', 'by']
    return {'type': 'object', 'oneOf': variants, 'x-discriminator': 'type'}


def session_schema(name):
    if name == 'create':
        return obj(dict(title=nullable(dict(type='string', maxLength=120))))
    if name == 'list':
        return obj(dict(limit=number(1, 200, integer=True, default=50), before=SESSION))
    if name == 'events':
        return obj(dict(after=number(0, integer=True, default=0), wait=number(0, 25, default=0),
                        limit=number(1, 500, integer=True, default=500)))
    if name == 'post':
        return session_event()
    if name == 'stage':
        return obj(dict(stage=STAGE), ('stage',))
    return EMPTY


def project_schema(name):
    if name in ('create', 'write'):
        text = ('A new timeline: {"name": "..."} is enough (width and height if not 1344x768); the rest is filled in. Add clips with insert operations afterwards.'
                if name == 'create' else 'The whole project record as you read it, with your changes; validated by the shared editor save.')
        return obj(dict(project={'type': 'object', 'description': text}, ref=REF), ('project',))
    ids = array(CLIP, 1, 500)
    revision = number(1, integer=True, description='The saved revision you read; a later save returns project_changed.')
    if name == 'generate':
        job = generate()['properties']
        frames = number(1, 10**7, integer=True)
        target = {'oneOf': [
            obj(dict(clip_ids=ids), ('clip_ids',), description='Regenerate the selected picture clips in place.'),
            obj(dict(start=EDITOR_FRAME, frames=frames, parts=array(frames, 1, 64, description='Part lengths that add up to frames; omitted parts follow what this device can make.')),
                ('start', 'frames'), description='Fill a timeline range, continuing from the picture before it.')]}
        settings = obj({k: job[k] for k in ('seed', 'quality', 'sampling', 'loras', 'force_regenerate')},
                       description='Generation settings; part i uses seed + i.')
        return obj(dict(revision=revision, text=dict(type='string', maxLength=8000), target=target,
                        settings=settings, ref=REF), ('revision', 'target'))
    if name == 'export':
        return obj(dict(revision=revision, ref=REF), ('revision',))
    if name == 'listen':
        return obj(dict(revision=revision, **LISTEN_FIELDS, ref=REF), ('revision', 'mode'))
    if name != 'operations':
        return EMPTY
    fields = {
        'insert': (dict(source=EDITOR_SOURCE, at=EDITOR_FRAME), ('source',), 'Insert picture and sound from a source. Omit at to append at the timeline end. A work brings the description it was generated from. Then write the new picture clip\'s note.'),
        'split': (dict(clip_ids=ids, frame=EDITOR_FRAME), ('clip_ids', 'frame'), 'Cut selected clips and their linked partners. The frame must be inside a selected segment.'),
        'trim': (dict(clip_ids=ids, frame=EDITOR_FRAME, edge=enum(('start', 'end'))), ('clip_ids', 'frame', 'edge'), 'Trim a selected edge at the frame. The frame must be inside a selected segment.'),
        'remove': (dict(clip_ids=ids), ('clip_ids',), 'Remove selected clips and linked partners. Use clip IDs from this revision.'),
        'remove_gaps': (dict(gap=nullable(obj(dict(track=enum(('v1', 'a1', 'a2')), start=EDITOR_FRAME), ('track', 'start')))), (), 'Close a gap or all gaps when gap is omitted or null. Get the timeline first to identify a gap by track and start.'),
        'duplicate': (dict(clip_ids=ids), ('clip_ids',), 'Duplicate selected clips after themselves. Use clip IDs from this revision.'),
        'copy': (dict(clip_ids=ids, at=EDITOR_FRAME), ('clip_ids', 'at'), 'Copy selected clips to a timeline frame. Use clip IDs from this revision.'),
        'move': (dict(clip_ids=ids, frames=number(-10**7, 10**7, integer=True)), ('clip_ids', 'frames'), 'Move selected clips by a signed relative frame offset. Use clip IDs from this revision; linked partners move together.'),
        'describe': (dict(clip_ids=ids, text=dict(type='string', maxLength=8000)), ('clip_ids', 'text'), 'Set the description of selected clips. Use clip IDs from this revision and one paragraph of text.'),
        'set_ref': (dict(clip_ids=ids, ref=dict(type='string', maxLength=128, pattern=r'^[A-Za-z0-9_-]{0,128}$')), ('clip_ids', 'ref'), 'Set an outside shot reference on selected clips. Use clip IDs from this revision; an empty ref removes it.'),
        'note': (dict(clip_ids=ids, text=dict(type='string', maxLength=400)), ('clip_ids', 'text'), 'Write the one line the user sees under selected picture clips; without it they see the start of the generation text. Do it after you add, generate or change a clip: in the user\'s language, what is in the picture, subject first ("Train through morning mist"); a short clip shows only the first few characters. Not the clip\'s position (opening, part 2) and not its progress. Up to 120 characters on one line; an empty text removes it. It never changes the description used for generation.'),
        'use_version': (dict(clip_id=CLIP, index=number(0, 49, integer=True)), ('clip_id', 'index'), 'Restore a stored clip version. First get the clip ID and its versions array; index is zero based.'),
        'subtitles': (dict(cues=array(obj(dict(start=EDITOR_FRAME, frames=number(1, 10**7, integer=True), text=dict(type='string', minLength=1, maxLength=400)),
                                         ('start', 'frames', 'text')), 1, 500), replace=dict(type='boolean')), ('cues',),
                      'Add subtitle lines as one undo step on the subtitle track (created if missing). Each cue is a start frame, a length in frames and one or two lines of text; cues may not overlap each other or lines already there. replace: true swaps the whole track.'),
    }
    operations = [obj(dict(op={'const': op}, **props), ('op', *required), description=description)
                  for op, (props, required, description) in fields.items()]
    return obj(dict(revision=number(1, integer=True), operations=array(
        {'oneOf': operations, 'x-discriminator': 'op'}, 1, 64), ref=REF), ('revision', 'operations'))


LABELS = ('front', 'three_quarter', 'side', 'back', 'face', 'sheet', 'other')


def cast_route_table():
    rows = ['| Operation | HTTP | Next step |', '|---|---|---|']
    for name, (method, suffix, summary) in CAST_OPERATIONS.items():
        rows.append('| %s | `%s P%s` | %s |' % (name, method, suffix, summary))
    return '\n'.join(rows)


def drama_route_table():
    rows = ['| Operation | HTTP | Next step |', '|---|---|---|']
    for name, (method, suffix, summary) in DRAMA_OPERATIONS.items():
        rows.append('| %s | `%s P%s` | %s |' % (name, method, suffix, summary))
    return '\n'.join(rows)


CAST_REFERENCE = {'enum': [None, 'cards', 'partial', 'missing', 'inherited', 'own'],
                  'description': 'How the shot carries cast cards: cards; partial (cast_omitted did not fit at all, or '
                                 'cast_voice_omitted came without their voice); missing '
                                 '(a card was deleted, cast_missing shoot from text); inherited (a film made before continuing '
                                 'shots carried cards); own (the shot names its own first frame or references); null.'}


def production_response():
    paused = {'type': ['object', 'null'], 'properties': dict(at=string(), by=enum(('user',)))}
    counts = obj(dict(completed=number(0, integer=True), total=number(0, integer=True)), ('completed', 'total'))
    progress = obj(dict(takes=counts, shots=counts,
        running=dict(type='array', items=obj(dict(shot=SLUG, take=number(1, integer=True), fraction=number(0, 1)),
                                           ('shot', 'take', 'fraction'))),
        join=obj(dict(completed={'type': 'boolean'}, fraction=number(0, 1)), ('completed', 'fraction')),
        fraction=number(0, 1, description='Time-weighted when every estimate is known; otherwise work-item counts.'),
        elapsed_seconds=number(0, description='Seconds since start excluding user pauses; stops at completion or cancellation.'),
        estimate_seconds=nullable(number(0)), remaining_seconds=nullable(number(0))),
        ('takes', 'shots', 'running', 'join', 'fraction', 'elapsed_seconds', 'estimate_seconds', 'remaining_seconds'))
    over_budget = nullable({'oneOf': [
        obj(dict(reason=enum(('elapsed',)), elapsed_seconds=number(0), estimate_seconds=number(0)),
            ('reason', 'elapsed_seconds', 'estimate_seconds')),
        obj(dict(reason=enum(('reshoot',)), shot=SLUG, estimate_seconds=number(600)),
            ('reason', 'shot', 'estimate_seconds'))]})
    return dict(type='object', properties=dict(id=PRODUCTION, title={'type': ['string', 'null']},
        revision=number(1, integer=True), changed={'type': 'boolean'}, paused=paused,
        progress=progress, over_budget=over_budget,
        fraction=number(0, 1, description='Only in unchanged compact replies.'),
        elapsed_seconds=number(0, description='Only in unchanged compact replies; excludes user pauses.'),
        stalled={'type': ['object', 'null'], 'properties': dict(code=string(), message=string(), at=string())},
        status=enum(('shooting', 'needs_choice', 'ready', 'joining', 'finished', 'cancelled', 'paused')),
        screenplay={'type': 'object'}, director=enum(('agent', 'builtin')), created_at=string(), updated_at=string(),
        shots=dict(type='array', items=dict(type='object', properties=dict(id=SLUG, index=number(1, integer=True),
            continues={'type': 'boolean'}, chosen={'type': ['integer', 'null']}, chosen_by={'type': ['string', 'null']},
            cast_reference=CAST_REFERENCE, cast_omitted=dict(type='array', items=string()),
            cast_voice_omitted=dict(type='array', items=string()),
            cast_missing=dict(type='array', items=string()),
            cast_pictures_each=dict(type=['integer', 'null'], description='Most pictures one person carries in this shot: every picture of the card (fewer only when the 32 references run out), or in a continuing shot the sheet and the face for one person, else one each.'),
            takes=dict(type='array', items=dict(type='object', properties=dict(take=number(1, integer=True), job=REF,
                task={'oneOf': [JOB, {'type': 'null'}]}, generation=number(0, integer=True), seed=number(0, integer=True),
                stale={'type': 'boolean'}, error={'type': ['object', 'null']},
                cast={'type': ['object', 'null'], 'additionalProperties': number(1, integer=True)})))))),
        todo=dict(type='array', items={'type': 'object'}), decisions=dict(type='array', items={'type': 'object'}),
        join={'type': ['object', 'null']}, outputs=dict(type='array', items={'type': 'object'})),
        required=['id', 'revision', 'status', 'paused'])


def safe_pointer(path):
    """A field pointer that never repeats supplied keys, names, tokens or prompts."""
    path = path if isinstance(path, str) else ''
    return '/'.join(p if not p or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', p) else '?' for p in path.split('/')) if path else '/'


def problem_diagnostic(path, message):
    """The shared diagnostic shape for a domain problem whose text a validator wrote."""
    path = safe_pointer(path)
    rule = message if isinstance(message, str) else 'valid'
    rule = rule.replace('\n', ' ')[:200]
    return dict(path=path, field=path, allowed=rule, got='invalid', message='%s %s.' % (path, rule.rstrip('.')),
                next='Correct %s and retry.' % path, _safe=True)


def diagnostic(path, allowed, got, *, nearest=None, missing=False):
    # Field pointers are not file paths. Never echo supplied text, names, tokens or prompts.
    path = path or '/'
    actual = 'missing' if missing else repr(got) if got is None or type(got) in (bool, int, float) else (
        '<string length %d>' % len(got) if isinstance(got, str) else '<%s>' % type(got).__name__)
    action = ('Use %s %s.' % (path, ' or '.join(map(str, nearest)))) if nearest else 'Set %s to %s.' % (path, allowed)
    return dict(path=path, field=path, allowed=allowed, got=actual,
                message='%s must be %s; received %s.' % (path, allowed, actual), next=action, _safe=True)


def prefix_diagnostic(error, prefix):
    if not prefix:
        return error
    path = error['path']
    return dict(error, path=prefix + path, field=prefix + path,
                message=error['message'].replace(path, prefix + path, 1),
                next=error.get('next', '').replace(path, prefix + path, 1))


def _type(value, kind):
    return {'object': isinstance(value, dict), 'array': isinstance(value, list), 'string': isinstance(value, str),
            'boolean': type(value) is bool, 'integer': type(value) is int,
            'number': type(value) is int or type(value) is float and math.isfinite(value), 'null': value is None}.get(kind, True)


def issues(schema, value, path='', *, refs=False):
    """Collect all violations, including independent cross-field rules. No mutation."""
    if refs:
        schema = batch_references(schema)
    if schema is True:
        return []
    if schema is False:
        return [diagnostic(path, 'absent in this combination', value)]
    errors = []
    if schema.get('x-media') is not None and isinstance(value, str):
        return issues(FILE, value, path + '/file', refs=refs)
    def bad(allowed, got=value, suffix='', **kw):
        errors.append(diagnostic(path + suffix, allowed, got, **kw))
    if schema.get('x-submission'):
        return issues(schema['oneOf'][1 if isinstance(value, dict) and 'jobs' in value else 0], value, path)
    if schema.get('x-discriminator') and isinstance(value, dict):
        if schema['x-discriminator'] != 'kind':
            field = schema['x-discriminator']
            variants = {s['properties'][field]['const']: s for s in schema['oneOf']}
            selected = value.get(field)
            if not isinstance(selected, str) or selected not in variants:
                return [diagnostic(path + '/' + field, ', '.join(variants), selected)]
            return issues(variants[selected], value, path, refs=refs)
        kind = value.get('kind', 'generate')
        if not isinstance(kind, str) or kind not in KINDS:
            return [diagnostic(path + '/kind', ', '.join(KINDS), kind)]
        chosen = next(s for s in schema['oneOf'] if s.get('x-job-kind') == kind)
        return issues(chosen, value, path, refs=refs)
    for rule in schema.get('allOf', []):
        errors += issues(rule, value, path)
    if 'if' in schema:
        rule = schema.get('then', True) if not issues(schema['if'], value) else schema.get('else', True)
        errors += issues(rule, value, path)
    if 'not' in schema and not schema.get('x-derived') and not issues(schema['not'], value):
        errors.append(diagnostic(path, 'a permitted field combination', value))
    if 'oneOf' in schema or 'anyOf' in schema:
        # Structural unions choose the most specific applicable branch, then
        # retain its field diagnostics instead of an opaque union error.
        def compatible(rule):
            if isinstance(rule, bool):
                return rule
            if 'oneOf' in rule or 'anyOf' in rule:
                return any(compatible(r) for r in rule.get('oneOf', rule.get('anyOf')))
            if isinstance(rule, bool):
                return rule
            if isinstance(value, dict) and rule.get('required'):
                if not all(k in value for k in rule['required']):
                    return False
            kinds = rule.get('type')
            return not kinds or any(_type(value, k) for k in (kinds if isinstance(kinds, list) else [kinds]))
        variants = schema.get('oneOf', schema.get('anyOf'))
        choices = [r for r in variants if compatible(r)] or variants
        branches = [issues(s, value, path, refs=refs) for s in choices]
        valid = sum(not e for e in branches)
        if valid > 1 and 'oneOf' in schema:
            errors.append(diagnostic(path, 'exactly one permitted alternative', value))
        if not valid:
            errors += min(branches, key=lambda e: (any('must be object' in r['message'] for r in e), len(e)))
            try:
                errors += cross_issues(schema, value, path)
            except (TypeError, KeyError, AttributeError, ValueError):
                pass
            return errors
    types = schema.get('type')
    if types and not any(_type(value, t) for t in (types if isinstance(types, list) else [types])):
        bad(str(types)); return errors
    if value is None:
        return errors
    if 'const' in schema and value != schema['const']:
        bad(repr(schema['const']))
    if 'enum' in schema and value not in schema['enum']:
        bad('one of ' + ', '.join(map(str, schema['enum'])))
    if type(value) in (int, float):
        low, high, step = schema.get('minimum'), schema.get('maximum'), schema.get('multipleOf')
        if 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']:
            bad('greater than %s' % schema['exclusiveMinimum'])
        if 'exclusiveMaximum' in schema and value >= schema['exclusiveMaximum']:
            bad('less than %s' % schema['exclusiveMaximum'])
        finite = type(value) is int or math.isfinite(value)
        if not finite or low is not None and value < low or high is not None and value > high or step and value % step:
            allowed = '%s from %s to %s' % ('a multiple of %s' % step if step else schema.get('type', 'number'), low, high)
            nearest = None
            if step and finite:
                base = math.floor(max(low, min(high, value)) / step) * step
                candidates = {v for v in (base - step, base, base + step, low, low + step, high - step, high)
                              if low <= v <= high}
                nearest = sorted(sorted(candidates, key=lambda v: (abs(v - value), v))[:2])
            bad(allowed, nearest=nearest)
    if isinstance(value, str):
        if (len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', float('inf'))
                or schema.get('minLength') and not value.strip() or '\x00' in value):
            bad('a nonempty string of at most %s characters' % schema.get('maxLength', 'the documented limit'))
        pattern = schema.get('pattern')
        if pattern:
            if not re.fullmatch(pattern, value):
                if schema == FILE:
                    bad('a relative input file returned by media upload', suffix='/file' if schema.get('x-media') else '')
                else:
                    bad('a string matching ' + pattern)
    if isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', float('inf')):
            bad('a list of %d to %s items' % (schema.get('minItems', 0), schema.get('maxItems', 'unlimited')))
        for i, child in enumerate(value):
            errors += issues(schema.get('items', {}), child, path + '/' + str(i), refs=refs)
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False:
            for key in sorted(value.keys() - props.keys()):
                bad('absent (unknown field)', value[key], '/<unknown>')
        for key in schema.get('required', []):
            if key not in value:
                bad('present', None, '/' + pointer(key), missing=True)
        for key, child in value.items():
            if key in props:
                errors += issues(props[key], child, path + '/' + pointer(key), refs=False)
    if schema.get('x-json-bytes'):
        try:
            if len(json.dumps(value, allow_nan=False)) > schema['x-json-bytes']:
                bad('JSON of at most %d bytes' % schema['x-json-bytes'])
        except (TypeError, ValueError):
            bad('finite JSON')
    # Cross-field constraints only run on structurally sound subtrees.
    try:
        errors += cross_issues(schema, value, path)
    except (TypeError, KeyError, AttributeError, ValueError):
        pass  # Ill-typed subtrees already have structural diagnostics.

    return errors


def cross_issues(schema, value, path):
    errors = []
    def bad(field, allowed, got=None):
        errors.append(diagnostic(path + '/' + field, allowed, got))
    flags = schema.get('x-media')
    if flags is not None:
        item = {'file': value} if isinstance(value, str) else value
        if flags['clip'] and item.get('end') is not None and item['end'] <= item.get('start', 0):
            bad('end', 'later than start', item['end'])
    kind = schema.get('x-job-kind')
    if kind == 'image':
        if value.get('people') and value.get('images'):
            bad('images', 'omitted when people is supplied', value['images'])
        if value.get('scene_image') and not value.get('people'):
            bad('scene_image', 'omitted unless people is supplied', value['scene_image'])
        for i, person in enumerate(value.get('people', [])):
            if 'feet' in person and person['feet'] < person.get('height', .62):
                bad('people/%d/feet' % i, 'at least the figure height, so the figure fits inside the frame', person['feet'])
    if kind == 'generate':
        if value.get('continue_from') and isinstance(value.get('media'), dict) and any(
                v is not None for k, v in value['media'].items() if k != 'references'):
            bad('media', 'only references when continue_from is supplied', value['media'])
        if value.get('refine_from') is None:
            custom = value.get('sampling', {})
            two, base, refine = QUALITY[value.get('quality', 'light')]
            two, base, refine = custom.get('two_pass', two), custom.get('base_steps', base), custom.get('refine_steps', refine)
            from ..two_pass import validate_steps
            try:
                validate_steps(base, refine, two)
            except ValueError:
                bad('sampling', 'two-pass refine_steps=3 or refine_steps below base_steps', custom)
            if value.get('preview') and two and base != 8:
                bad('sampling', 'base_steps=8 for a preview, the only first-pass count that runs two-pass', custom)
    if kind == 'vlm':
        rows = value['questions'] if isinstance(value['questions'], list) else [value['questions']]
        count = sum(len(r.get('images', [])) + (r.get('video', {}).get('frames', 6) if isinstance(r.get('video'), dict)
                   else 6 if r.get('video') else 0) for r in rows if isinstance(r, dict))
        if count > 24:
            bad('questions', 'at most 24 images and sampled frames', count)
    if schema.get('x-editor-document'):
        errors += editor_issues(value, path, document=True)
    if schema.get('x-editor-recipe'):
        from .edit import _color, _aspect
        for i, operation in enumerate(value.get('operations', [])):
            if not isinstance(operation, dict):
                continue
            for key in ('color', 'fill', 'stroke', 'outline'):
                color = operation.get(key)
                if color is not None and type(color) is not bool:
                    try:
                        _color(color, '')
                    except (ValueError, TypeError):
                        bad('operations/%d/%s' % (i, key), 'a supported CSS colour', color)
            for key in ('ratio', 'to') if operation.get('op') in ('crop', 'pad', 'extend') else ():
                if key in operation:
                    try:
                        _aspect(operation[key], {'w': 1, 'h': 1}, (1, 1), '')
                    except (ValueError, TypeError):
                        bad('operations/%d/%s' % (i, key), 'an editor ratio or positive W:H', operation[key])
    return errors


def validate(schema, value, *, refs=False):
    from .spec import SpecError
    errors = issues(schema, value, refs=refs)
    if errors:
        raise SpecError(errors)
    return value


def validate_job(value, *, local=False, refs=False):
    return validate(job_schema(local=local, refs=refs), value)


def request_contract(method, path, *, local=False):
    definitions = contracts(local=local)
    key = method, path
    if key not in definitions:
        path = re.sub(r'/jobs/j[0-9a-f]{12}/', '/jobs/{id}/', path)
        path = re.sub(r'/cast/c[0-9a-f]{12}(?=/|$)', '/cast/{id}', path)
    definition = definitions.get((method, path))
    return definition['body'] if definition else None


def editor_issues(value, path, *, document=False, source_size=None, target=None, resolve=None):
    """Reuse editor validation without rendering or writing any files."""
    from . import edit
    try:
        if document or isinstance(value, dict) and value.get('version') == 2:
            edit._check(value)
        elif source_size is not None:
            edit.compile_recipe(value, source='input', source_size=source_size, target=target, resolve=resolve)
        else:
            # Static operation fields are generated from describe(). Geometry
            # needs the actual image and is checked by ComfyPort before commit.
            return issues(edit_recipe(), value, path)
    except (ValueError, TypeError, KeyError, AttributeError):
        # Renderer errors may contain user-supplied field names or filenames.
        return [diagnostic(path, 'a valid editor document or recipe for the source dimensions', None)]
    return []


def preflight_submission(value):
    """Complete static client preflight before the first upload."""
    from .spec import SpecError
    problems = []
    batch = isinstance(value, dict) and 'jobs' in value
    rows = value['jobs'] if batch else [value]
    names, count = set(), 0
    def walk(item, path):
        if isinstance(item, list):
            for i, child in enumerate(item):
                walk(child, path + '/' + str(i))
        elif isinstance(item, dict):
            for key, child in item.items():
                if key == 'edit':
                    problems.extend(editor_issues(child, path + '/edit'))
                elif key != 'metadata':
                    walk(child, path + '/' + pointer(key))
        elif isinstance(item, str) and item.startswith('@') and (path.rsplit('/', 1)[-1] in ('from_job', 'refine_from') or re.search(r'/(after|clips)/[0-9]+$', path)):
            if item[1:] not in names:
                problems.append(diagnostic(path, 'an earlier batch ref', item))
    for i, row in enumerate(rows):
        path = '/jobs/%d' % i if batch else ''
        count += row.get('takes', 1)
        if row.get('kind', 'generate') == 'generate' and not row.get('refine_from'):
            from .spec import canvas_size
            try:
                canvas_size(row)
            except SpecError as error:
                problems.extend(prefix_diagnostic(e, path) for e in error.errors)
        walk(row, path)
        if batch and row.get('ref'):
            if row['ref'] in names:
                problems.append(diagnostic(path + '/ref', 'a unique batch ref', row['ref']))
            names.add(row['ref'])
    if count > 64:
        problems.append(diagnostic('/jobs', 'at most 64 jobs after takes expansion', count))
    if problems:
        raise SpecError(problems)


def defaults(schema, value):
    """Fill only defaults for fields on this object (not alternative representations)."""
    return {k: deepcopy(v['default']) for k, v in schema.get('properties', {}).items() if 'default' in v} | value


def query_values(schema, query):
    value = dict(query)
    for key, rule in schema.get('properties', {}).items():
        if key in value and rule.get('type') in ('number', 'integer'):
            try:
                value[key] = (int if rule['type'] == 'integer' else float)(value[key])
            except (TypeError, ValueError, OverflowError):
                pass
    validate(schema, value)
    return defaults(schema, value)


def schemas():
    return dict(api_version=API_VERSION, job=dict(job_schema(), **{'$schema': S, '$defs': {
        k: f() for k, f in KINDS.items()}}), batch=batch_schema(), production=drama_schema('start'),
        drama={name: drama_schema(name) for name in DRAMA_OPERATIONS}, production_view=production_response(),
        cast={name: cast_schema(name) for name in CAST_OPERATIONS}, cast_view=cast_view(),
        projects={name: project_schema(name) for name in PROJECT_OPERATIONS},
        imports=dict(upload=import_upload(), item=import_view()),
        images={name: image_schema(name) for name in IMAGE_OPERATIONS},
        whitebox={name: whitebox_schema(name) for name in WHITEBOX_OPERATIONS},
        sessions=dict(event=session_event(stored=True), meta=session_view(),
                      **{name: session_schema(name) for name in SESSION_OPERATIONS}),
        confirmations=confirm_settings(), image_result=image_result())


def openapi():
    from .errors import ERRORS
    paths = {}
    for (method, suffix), contract in contracts().items():
        path = suffix if suffix.startswith('/freevideo/') else PREFIX + suffix
        operation = dict(summary=contract['summary'], security=[] if contract.get('auth') is False else [{'bearer': []}],
            responses={str(contract.get('status', 200)): {'description': 'Success'},
                       **{str(status): {'description': 'Agent error', 'content': {'application/json': {'schema': {
                           'type': 'object', 'properties': {'error': {'$ref': '#/components/schemas/Error'}}}}}}
                          for status in sorted({r[0] for r in ERRORS.values() if r[0] >= 400})}})
        if contract.get('auth') is False and (method != 'GET' or contract.get('page_cookie')):
            operation['security'] = [{'page': []}]
        if contract.get('raw_body'):
            operation['requestBody'] = dict(required=True, content={'application/octet-stream': {'schema': {
                'type': 'string', 'format': 'binary', 'maxLength': MAX_CHAT_ATTACHMENT_BYTES}}})
        if contract.get('page_or_token'):
            operation['security'] = [{'page': []}] if suffix.endswith(('/stop', '/resume')) else [{'bearer': []}, {'page': []}]
        if contract['body'] is not None:
            content = {'application/json': {'schema': deepcopy(contract['body'])}}
            if contract.get('multipart'):
                content['multipart/form-data'] = {'schema': obj({'file': {'type': 'string', 'format': 'binary'}}, ('file',))}
                if suffix == '/imports':
                    content['multipart/form-data']['schema']['properties']['ref'] = REF
            operation['requestBody'] = dict(required=not contract.get('optional_body', False), content=content)
        operation['parameters'] = [dict(name=k, **{'in': 'query'}, required=k in contract['query'].get('required', []), schema=v)
                                   for k, v in contract['query']['properties'].items()]
        operation['parameters'] += [dict(name=k, **{'in': 'path'}, required=True,
                                        schema=contract.get('path', {}).get('properties', {}).get(k, JOB if k == 'id' else string()))
                                    for k in re.findall(r'\{(.*?)\}', path)]
        if contract.get('idempotent'):
            operation['parameters'].append(dict(name='Idempotency-Key', **{'in': 'header'}, required=False, schema=REF))
        if suffix == '/freevideo/agent/chat/attach':
            operation['parameters'].append(dict(name='X-FreeVideo-CSRF', **{'in': 'header'}, required=True,
                schema=string(128), description='The page session CSRF token.'))
        if contract.get('page_or_token'):
            operation['parameters'].append(dict(name='X-FreeVideo-CSRF', **{'in': 'header'}, required=False,
                schema=string(128), description='Required with the freevideo_agent_page cookie, including reads.'))
        if suffix == '/sessions/{id}/events' and method == 'GET':
            operation['parameters'].append(dict(name='Last-Event-ID', **{'in': 'header'}, required=False,
                schema=number(0, integer=True), description='SSE resume cursor; overrides after.'))
        content_type, response_schema = 'application/json', {'type': 'object'}
        if suffix == '/jobs' and method == 'POST':
            response_schema = {'$ref': '#/components/schemas/Submitted'}
        elif suffix == '/jobs' and method == 'GET':
            response_schema = obj({'jobs': {'type': 'array', 'items': {'$ref': '#/components/schemas/Job'}}})
        elif suffix in ('/jobs/{id}', '/jobs/{id}/cancel', '/jobs/{id}/resume'):
            response_schema = {'$ref': '#/components/schemas/JobAnswer'}
        elif '/drama/{id}' in suffix and not suffix.endswith('/sheet') or suffix == '/drama' and method == 'POST':
            response_schema = production_response()
        elif suffix == '/confirmations':
            response_schema = obj(confirm_settings()['properties'], ('start', 'reshoot_seconds'))
        elif suffix in ('/cast', '/freevideo/agent/cast') and method == 'POST' or (
                suffix in ('/cast/{id}', '/freevideo/agent/cast/{id}') and method in ('GET', 'PATCH')):
            response_schema = {'$ref': '#/components/schemas/CastCard'}
        elif suffix in ('/cast/{id}/generate', '/freevideo/agent/cast/{id}/generate'):
            response_schema = obj(dict(job=JOB, jobs=array(JOB, 1, 5),
                                       card={'$ref': '#/components/schemas/CastCard'}), ('job', 'jobs', 'card'))
        elif suffix.startswith('/sessions'):
            response_schema = (obj(dict(events=array(session_event(stored=True), 0, 500),
                last_seq=number(0, integer=True), stopped={'type': 'boolean'}), ('events', 'last_seq', 'stopped'))
                if suffix.endswith('/events') and method == 'GET' else session_event(stored=True)
                if suffix.endswith(('/events', '/stop', '/resume')) else
                obj(dict(sessions=array(session_view(), 0, 200), next_before=nullable(SESSION)), ('sessions', 'next_before'))
                if suffix == '/sessions' and method == 'GET' else session_view())
        elif suffix.endswith('/events'):
            content_type, response_schema = 'text/event-stream', {'type': 'string'}
        elif suffix == '/imports':
            response_schema = obj({'import': import_view()}, ('import',)) if method == 'POST' else obj(
                {'items': array(import_view())}, ('items',))
        elif suffix == '/freevideo/agent/chat/attach':
            response_schema = obj(dict(CHAT_ATTACHMENT['properties'], bytes=number(1, MAX_CHAT_ATTACHMENT_BYTES, integer=True)),
                                  (*CHAT_ATTACHMENT['required'], 'bytes'))
        elif suffix == '/freevideo/agent/chat/attachment':
            content_type, response_schema = 'image/png', {'type': 'string', 'format': 'binary'}
        elif suffix == '/editor/sources/content':
            content_type, response_schema = 'application/octet-stream', {'type': 'string', 'format': 'binary'}
        elif suffix.endswith('/file') or suffix.endswith('/thumbnail'):
            content_type = 'video/mp4' if suffix.endswith('/file') else 'image/jpeg'
            response_schema = {'type': 'string', 'format': 'binary'}
        success = operation['responses'][str(contract.get('status', 200))]
        success['content'] = {content_type: {'schema': response_schema}}
        if suffix == '/freevideo/agent/chat/attachment':
            success['content'].update({kind: {'schema': response_schema} for kind in ('image/jpeg', 'image/webp')})
        if suffix == '/sessions/{id}/events' and method == 'GET':
            success['content']['text/event-stream'] = {'schema': {'type': 'string'}}
        if contract.get('idempotent'):
            operation['responses']['200'] = deepcopy(success)
        if suffix.endswith('/file') or suffix == '/editor/sources/content':
            operation['responses']['206'] = deepcopy(success)
        if suffix == '/connect' and method == 'GET':
            success['content']['text/plain'] = {'schema': {'type': 'string'}}
        paths.setdefault(path, {})[method.lower()] = operation
    job_view = {'type': 'object', 'properties': dict(id=JOB, kind=enum(KINDS),
        status=enum(('waiting', 'queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled', 'interrupted')),
        chain_id=string(), last_heartbeat_at={'type': ['string', 'null']}, poll_after_ms={'type': ['integer', 'null']},
        cancelled_by=enum(('user',), description='Present when the user stopped this job on the page.'),
        next=string(description='Instructions for the agent after a user stop.'),
        spec={'type': 'object'}, progress={'type': 'object'}, result={'type': 'object'},
        waiting_for=array(string()), notice=string(),
        error={'$ref': '#/components/schemas/Error'})}
    job_view['allOf'] = [{'if': {'required': ['kind'], 'properties': {'kind': {'const': 'image'}}},
                         'then': {'properties': {'result': image_result()}}}]
    return dict(openapi='3.1.0', info=dict(title='FreeVideo Agent API', version=str(API_VERSION)), paths=paths,
        components=dict(securitySchemes={'bearer': {'type': 'http', 'scheme': 'bearer'},
            'page': {'type': 'apiKey', 'in': 'cookie', 'name': 'freevideo_agent_page'}}, schemas={
            'Session': session_view(), 'SessionEvent': session_event(stored=True),
            'JobSpec': schemas()['job'], 'ImageResult': image_result(), 'CastCard': cast_view(),
            'Batch': batch_schema(), 'Job': job_view,
            # One job answers the way a submission does: the same job is also under 'job'.
            'JobAnswer': dict(job_view, properties=dict(job_view['properties'], job={'$ref': '#/components/schemas/Job'})),
            'Submitted': obj(dict(job={'$ref': '#/components/schemas/Job'},
                jobs={'type': 'array', 'items': {'$ref': '#/components/schemas/Job'}})),
            'Error': obj(dict(code=enum(ERRORS),
                message=string(), details={'type': 'array'}, next=string(), retryable={'type': 'boolean'},
                reason=obj(dict(en=string(), zh=string()), ('en', 'zh'),
                           description='Fixed bilingual reason for an image failure.'),
                report_id={'type': ['string', 'null'], 'pattern': '^[0-9a-f]{32}$',
                           'description': 'Image diagnostic report ID, or null when no report exists.'},
                request_id=string(), docs=string(), retry_after_seconds=number(0), card={'type': 'object'}, job=JOB,
                project={'type': 'object'}, current_revision=number(1, integer=True),
                error_zh=string(), error_en=string()),
                ('code', 'message', 'details', 'retryable', 'request_id', 'docs'))}))


TOOL_ENDPOINTS = {
    'freevideo_status': ('GET', ''), 'freevideo_capabilities': ('GET', '/capabilities'),
    'freevideo_prompt_guide': ('GET', '/guide'), 'freevideo_schema': ('GET', '/schema'),
    'freevideo_plan': ('POST', '/plan'), 'freevideo_submit_batch': ('POST', '/jobs'),
    'freevideo_job': ('GET', '/jobs/{id}'), 'freevideo_wait': ('GET', '/jobs/{id}'),
    'freevideo_jobs': ('GET', '/jobs'), 'freevideo_cancel': ('POST', '/jobs/{id}/cancel'),
    'freevideo_resume': ('POST', '/jobs/{id}/resume'), 'freevideo_add_media': ('POST', '/media'),
    'freevideo_import_media': ('POST', '/imports'), 'freevideo_imports': ('GET', '/imports'),
    'freevideo_edit_image': ('POST', '/media/edit'), 'freevideo_library': ('GET', '/library'),
    'freevideo_release_gpu': ('POST', '/gpu/release'),
}
TOOL_ENDPOINTS.update({'freevideo_drama_' + name: (method, suffix)
    for name, (method, suffix, _) in DRAMA_OPERATIONS.items()})
TOOL_ENDPOINTS.update({'freevideo_cast_' + name: (method, suffix)
    for name, (method, suffix, _) in CAST_OPERATIONS.items()})
TOOL_ENDPOINTS.update({PROJECT_TOOLS[name]: (method, suffix)
    for name, (method, suffix, _) in PROJECT_OPERATIONS.items()})
TOOL_ENDPOINTS.update({WHITEBOX_TOOLS[name]: (method, suffix)
    for name, (method, suffix, _) in WHITEBOX_OPERATIONS.items()})
TOOL_ENDPOINTS.update({IMAGE_TOOLS[name]: (method, suffix)
    for name, (method, suffix, _) in IMAGE_OPERATIONS.items()})
TOOL_ENDPOINTS.update({SESSION_TOOLS[name]: SESSION_OPERATIONS[name][:2]
                       for name in SESSION_TOOLS if name != 'post'})
TOOL_KINDS = dict(freevideo_generate='generate', freevideo_assemble='assemble',
                  freevideo_ask='vlm', freevideo_enhance_prompt='enhance', freevideo_generate_image='image')


SESSION_ARGUMENT = dict(type='string', pattern='^s[0-9a-f]{12}$',
    description='Work session; omit for latest.')


SHAPE_MIN_BYTES = 400
RECIPE_TOOL = 'freevideo_edit_image'


def schema_children(node):
    """(keyword, key or index, subschema) for each child in a schema position."""
    for keyword, value in node.items():
        if keyword in ('additionalProperties', 'items', 'not', 'if', 'then', 'else', 'contains', 'propertyNames'):
            if isinstance(value, dict):
                yield keyword, None, value
        elif keyword in ('properties', 'patternProperties', 'dependentSchemas') and isinstance(value, dict):
            yield from ((keyword, key, child) for key, child in value.items() if isinstance(child, dict))
        elif keyword in ('anyOf', 'oneOf', 'allOf', 'prefixItems') and isinstance(value, list):
            yield from ((keyword, i, child) for i, child in enumerate(value) if isinstance(child, dict))


def shape_label(path):
    label = ''
    for keyword, key in path:
        if keyword == 'properties':
            label += ('.' if label else '') + key
        elif keyword == 'items':
            label += '[]'
        elif keyword == 'additionalProperties':
            label += '{}'
    return label or 'the arguments'


def mcp_tool_schema(name):
    """The schema an MCP client shows its model. Each client sends every tool schema
    with every request, so a repeated shape is written out once and later named; the
    edit recipe is spelled out on the edit tool only. Calls still validate against
    tool_schema(name) here and on the server."""
    value = tool_schema(name)
    if name != RECIPE_TOOL:
        def brief(node):
            if node.get('x-editor-recipe'):
                return {'type': 'object', 'required': ['operations'],
                        'description': 'Image edit recipe: the operations %s takes.' % RECIPE_TOOL}
            node = dict(node)
            for keyword, key, child in list(schema_children(node)):
                _replace(node, keyword, key, brief(child))
            return node
        value = brief(value)
    def key(node):
        return json.dumps(node, sort_keys=True, separators=(',', ':'))
    counts = {}
    def count(node):
        for _, _, child in schema_children(node):
            text = key(child)
            if len(text) >= SHAPE_MIN_BYTES:
                counts[text] = counts.get(text, 0) + 1
            count(child)
    count(value)
    first = {}
    def rebuild(node, path):
        node = dict(node)
        for keyword, index, child in list(schema_children(node)):
            here, text = path + [(keyword, index)], key(child)
            if counts.get(text, 0) > 1 and text in first:
                child = dict({'type': child['type']} if 'type' in child else {},
                             description='Same shape as %s.' % first[text])
            else:
                if counts.get(text, 0) > 1:
                    first[text] = shape_label(here)
                child = rebuild(child, here)
            _replace(node, keyword, index, child)
        return node
    return rebuild(value, [])


def _replace(node, keyword, index, child):
    if index is None:
        node[keyword] = child
    elif isinstance(node[keyword], dict):
        node[keyword] = dict(node[keyword], **{index: child})
    else:
        node[keyword] = node[keyword][:index] + [child] + node[keyword][index + 1:]


def tool_schema(name):
    value = _tool_schema(name)
    if value.get('type') == 'object' and isinstance(value.get('properties'), dict):
        value = dict(value, properties=dict(value['properties'], session=SESSION_ARGUMENT))
    return value


def _tool_schema(name):
    if name == 'freevideo_session_post':
        message, plan = session_event(agent_only=True)['oneOf']
        return obj(dict(id=SESSION, type=enum(('message', 'plan')), text=message['properties']['text'],
            shots=plan['properties']['shots'], total_seconds=plan['properties']['total_seconds']), ('id', 'type'),
            oneOf=[{'properties': {'type': {'const': 'message'}}, 'required': ['text'],
                    'not': {'anyOf': [{'required': ['shots']}, {'required': ['total_seconds']}]}},
                   {'properties': {'type': {'const': 'plan'}}, 'required': ['shots'],
                    'not': {'required': ['text']}}])
    if name in TOOL_KINDS or name == 'freevideo_plan':
        kind = TOOL_KINDS.get(name, 'generate')
        value = KINDS[kind](local=True)
        value['properties'].pop('kind')
        if kind == 'image':
            value.pop('allOf')  # Said in the field descriptions instead; the job is still checked when submitted.
        value['required'] = [k for k in value.get('required', []) if k != 'kind']
        if name != 'freevideo_plan':
            value['properties']['wait'] = dict(CLIENT_WAIT, default=0 if kind == 'generate' else 25)
        return value
    if name in TOOL_ENDPOINTS:
        method, suffix = TOOL_ENDPOINTS[name]
        contract = contracts(local=True)[method, suffix]
        value = deepcopy(contract['query'] if method == 'GET' else contract['body'])
        if name == 'freevideo_submit_batch':
            return batch_schema(local=True)
        if name == 'freevideo_add_media':
            return obj({'path': string(4096)}, ('path',))
        if name == 'freevideo_import_media':
            return obj(dict(path=string(4096), ref=REF), ('path',))
        if '{id}' in suffix:
            value['properties']['id'] = contract.get('path', {}).get('properties', {}).get('id', JOB)
            value['required'] = list(value.get('required', [])) + ['id']
        if '{shot}' in suffix:
            value['properties']['shot'] = SLUG
            value['required'].append('shot')
        if name == 'freevideo_wait':
            value['properties'].pop('wait')
            value['properties']['timeout'] = number(0, 50, default=45, description='With id up to 45 seconds; without id (the user) up to 50, default 50.')
            value['required'] = [k for k in value['required'] if k != 'id']
        return value
    if name == 'freevideo_asset':
        return obj(dict(id=SAVED, target=string(4096), thumbnail={'type': 'boolean', 'default': False}), ('id', 'target'))
    if name == 'freevideo_look':
        return obj(dict(source={'anyOf': [string(4096), media(local=True)]},
            mode=enum(('frame', 'sheet', 'image'), default='sheet'), at=dict(FRAME, default='last'),
            count=contracts()['POST', '/media/contact-sheet']['body']['properties']['count']), ('source',))
    raise KeyError(name)


def cli_arguments(parser, schema, *, exclude=()):
    """Generate real argparse options; validation uses the identical field rules."""
    import argparse
    parser.request_schema = deepcopy(schema)
    for key, rule in schema['properties'].items():
        if key in exclude or 'const' in rule:
            continue
        kind = rule.get('type')
        if isinstance(kind, list):
            kind = kind[0]
        def json_or_string(value):
            try:
                return json.loads(value)
            except ValueError:
                return value
        union = 'oneOf' in rule or 'anyOf' in rule
        convert = int if kind == 'integer' else float if kind == 'number' else json.loads if kind in (
            'object', 'array', 'boolean') or not kind and not union else json_or_string if union else str
        parser.add_argument('--' + key.replace('_', '-'), dest=key, type=convert,
                            default=argparse.SUPPRESS, help=json.dumps(rule, ensure_ascii=False))
