"""MiniMax H3 rewrite contract, derived from the official writing guides.

https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing
The public guide describes a prompt format, not a released Context-IR model.
"""
import math
import re

BASE_FIELDS = ('integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music')
REF_FIELDS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
              'overall_soundscape', 'non_diegetic_music')


def request(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_request')
    prompt, seconds, media = value.get('text'), value.get('seconds'), value.get('media', [])
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError('invalid_prompt')
    if type(seconds) not in (float, int) or not math.isfinite(seconds) or not 1 <= seconds <= 60:
        raise ValueError('invalid_duration')
    if not isinstance(media, list) or len(media) > 8:
        raise ValueError('too_many_images')
    roles = []
    for row in media:
        if (not isinstance(row, dict) or set(row) != {'file', 'role'} or
                not isinstance(row['file'], str) or row['role'] not in ('first', 'last', 'reference')):
            raise ValueError('unsupported_media')
        roles.append(row['role'])
    if 'reference' in roles and any(r in roles for r in ('first', 'last')):
        raise ValueError('mixed_media')
    if roles.count('first') > 1 or roles.count('last') > 1:
        raise ValueError('mixed_media')
    mode = ('ref2va' if 'reference' in roles else 'fl2va' if 'first' in roles and 'last' in roles
            else 'i2va' if 'first' in roles else 'l2va' if 'last' in roles else 't2va')
    # Anchor numbering must match the encoder even when the UI added last first.
    if mode != 'ref2va':
        media = sorted(media, key=lambda r: r['role'] != 'first')
    return dict(text=prompt, seconds=float(seconds), media=media, mode=mode)


def instruction(value):
    rule = """Rewrite the user scene as an English MiniMax H3 video prompt. Follow the requested
actions and camera movement exactly. Image references describe appearance, not whether an
object moves. Keep dialogue, lyrics and visible writing verbatim in their original language.
Add concrete motion, framing, lighting and sounds without changing the intended scene.
Do not invent dialogue, captions or music. Return only the sections listed below as plain
headings followed by colons. No preface, commentary, markdown fences or JSON.
Use [Shot 1] for the opening; if the user wants cuts, add [Shot 2] At MM:SS.mmm, etc.
Keep unbroken shots unbroken. Literal dialogue uses (S1) <d>[Language] speech</d>.
"""
    if value['mode'] == 'ref2va':
        rule += """subject_definitions: Name each visible subject literally <Subject 1>, <Subject 2>, etc.,
with its appearance and source <Picture N>. Preserve the supplied picture numbering.
summary: [reference generation] State what happens in the requested video.
retention_analysis: Mark each subject fully_preserved, partially_preserved, attribute_transfer,
or weak_reference. This describes retained visual attributes, not motion restrictions.
detailed_description: [Shot 1] Describe the target video, using <Subject N> identifiers.
"""
    else:
        rule += 'integrated_multimodal_description: [Shot 1] Describe the target video.\n'
        for i, row in enumerate(value['media'], 1):
            timestamp = '0.00' if row['role'] == 'first' else f'{value["seconds"]:.2f}'
            rule += (f'Anchor <Picture {i}> to the {row["role"]} frame at {timestamp} seconds; '
                     'keep its pictured layout at that anchor.\n')
    rule += """overall_soundscape: Environmental sounds, or N/A.
non_diegetic_music: Music only if requested, otherwise N/A.
Use only supplied media labels; do not invent audio or video references.
"""
    fields = REF_FIELDS if value['mode'] == 'ref2va' else BASE_FIELDS
    return (rule + f'Cover {value["seconds"]:.3f} seconds. All sections are required, in this order: '
            + ', '.join(fields) + f'. Begin with "{fields[0]}:".')


def validate_output(text, value):
    if not isinstance(text, str) or not 100 <= len(text.strip()) <= 20000:
        raise ValueError('invalid_output')
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1]).strip()
    text = re.sub(r'(?m)^#{1,3} +([a-z_]+):', r'\1:', text)
    fields = REF_FIELDS if value['mode'] == 'ref2va' else BASE_FIELDS
    found = re.findall(r'(?m)^([a-z_]+):', text)
    if found != list(fields) or not text.startswith(fields[0] + ':'):
        raise ValueError('invalid_output')
    if not re.search(r'\[Shot \d+\]', text):
        name = 'detailed_description' if value['mode'] == 'ref2va' else fields[0]
        text = text.replace(name + ':', name + ': [Shot 1]', 1)
    if '[Shot 1]' not in text:
        raise ValueError('invalid_output')
    for marker in fields:
        section = text.split(marker + ':', 1)[1].split('\n\n', 1)[0].strip()
        if not section:
            raise ValueError('invalid_output')
    for minutes, seconds in re.findall(r'\bAt (\d{2}):(\d{2}\.\d{3})', text):
        if int(minutes) * 60 + float(seconds) >= value['seconds']:
            raise ValueError('invalid_output')
    for index in re.findall(r'<Picture (\d+)>', text):
        if not 1 <= int(index) <= len(value['media']):
            raise ValueError('invalid_output')
    if re.search(r'<(?:Audio|Video) \d+>', text):
        raise ValueError('invalid_output')
    for i in range(1, len(value['media']) + 1):
        if f'<Picture {i}>' not in text:
            raise ValueError('invalid_output')
    return text
