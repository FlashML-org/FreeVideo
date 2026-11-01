"""First frames from a scene followed by character references and numeric positions.

The default uses a scene-first prompt (pos2), without writing a collage. It aims
for the scene, requested framing and approximate positions; face similarity and
exact coordinates are not guaranteed. Collage helpers remain available for experiments.
"""
from pathlib import Path

from PIL import Image
from .staging import position_prompt, scene_prompt

MAX_PEOPLE = 3  # the model takes 4 pictures: the people (or none) plus the layout or the scene
TOLERANCE = 9


def cut_out(picture, step=TOLERANCE):
    """The person on their plain backdrop as an RGBA picture.

    The backdrop is what the edge reaches by changing less than `step` between neighbouring pixels,
    so a vignette or gradient backdrop goes as well; it is found on a small copy and scaled back.
    """
    from collections import deque
    rgb = picture.convert('RGB')
    scale = min(1., 384 / max(rgb.size))
    small = rgb.resize((max(2, round(rgb.width * scale)), max(2, round(rgb.height * scale))), Image.BILINEAR)
    width, height = small.size
    pixels = list(small.getdata())
    gone = bytearray(width * height)
    queue = deque()
    for x in range(width):
        queue.extend((x, width * (height - 1) + x))
    for y in range(height):
        queue.extend((width * y, width * y + width - 1))
    for index in queue:
        gone[index] = 1
    while queue:
        index = queue.popleft()
        r, g, b = pixels[index]
        x = index % width
        for other in (index - 1 if x else -1, index + 1 if x < width - 1 else -1, index - width, index + width):
            if 0 <= other < len(pixels) and not gone[other]:
                r2, g2, b2 = pixels[other]
                if abs(r - r2) + abs(g - g2) + abs(b - b2) < step:
                    gone[other] = 1
                    queue.append(other)
    mask = Image.new('L', small.size)
    mask.putdata([0 if flag else 255 for flag in gone])
    mask = mask.resize(rgb.size, Image.BILINEAR).point(lambda v: 255 if v > 127 else 0)
    result = rgb.convert('RGBA')
    result.putalpha(mask)
    box = mask.getbbox()
    return result.crop(box) if box else result


def collage(scene, people, width, height):
    """The scene scaled to cover the canvas with each person pasted: people = [(picture, x, height, feet)] in 0-1."""
    canvas = scene.convert('RGB')
    scale = max(width / canvas.width, height / canvas.height)
    canvas = canvas.resize((max(width, round(canvas.width * scale)), max(height, round(canvas.height * scale))))
    left, top = (canvas.width - width) // 2, (canvas.height - height) // 2
    canvas = canvas.crop((left, top, left + width, top + height))
    for picture, x, tall, feet in people:
        person = cut_out(picture)
        size = max(1, round(tall * height))
        person = person.resize((max(1, round(person.width * size / person.height)), size))
        at = (round(x * width - person.width / 2), round(feet * height - person.height))
        canvas.paste(person, at, person)
    return canvas


def _check(people):
    if not isinstance(people, list) or not 1 <= len(people) <= MAX_PEOPLE:
        raise ValueError('invalid_request')
    rows = []
    for person in people:
        if not isinstance(person, dict) or not isinstance(person.get('image'), str):
            raise ValueError('invalid_media')
        x = person.get('x', .5)
        tall = person.get('height', .62)
        feet = person.get('feet', max(.9, tall) if type(tall) in (int, float) else .9)
        if any(type(v) not in (int, float) or not 0 <= v <= 1 for v in (x, tall, feet)) or tall < .05:
            raise ValueError('invalid_request')
        if feet < tall or person.get('facing', 'right') not in ('left', 'right') \
                or person.get('framing', 'full') not in ('full', 'half'):
            raise ValueError('invalid_request')
        rows.append(dict(image=person['image'], x=float(x), height=float(tall), feet=float(feet),
                         name=str(person.get('name') or '')[:40],
                         framing=person.get('framing', 'full'),
                         **({'facing': person['facing']} if 'facing' in person else {})))
    return rows


def build(value, input_directory):
    """A generate request for a first frame. value: width, height, people=[{image, x, height, feet, name}],
    and the scene as scene_image (a file name) or scene (text). Pictures are file names in the input directory
    (or freevideo-image/<name>). facing=left/right and framing=full/half are optional per person."""
    if not isinstance(value, dict):
        raise ValueError('invalid_request')
    people = _check(value.get('people'))
    width, height = value.get('width'), value.get('height')
    if any(type(v) is not int or not 256 <= v <= 2048 or v % 32 for v in (width, height)):
        raise ValueError('invalid_dimensions')
    text = value.get('scene') if isinstance(value.get('scene'), str) else ''
    directory = Path(input_directory).resolve()

    def load(name):
        folder, _, leaf = name.rpartition('/')
        if not leaf or leaf in ('.', '..') or folder not in ('', 'freevideo-image') or any(c in name for c in ('\\', ':', '\0')):
            raise ValueError('invalid_media')
        path = (directory / folder / leaf) if folder else directory / leaf
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file() \
                or path.resolve().parent != (directory / folder if folder else directory):
            raise ValueError('invalid_media')
        try:
            with Image.open(path) as image:
                return image.convert('RGBA')
        except OSError:
            raise ValueError('invalid_media') from None

    names = [person['image'] for person in people]
    scene_name = value.get('scene_image')
    if isinstance(scene_name, str) and scene_name:
        names.insert(0, scene_name)
        prompt = scene_prompt(people)
    else:
        if not text.strip():
            raise ValueError('invalid_prompt')
        first_image = 1
        prompt = 'The scene: ' + text.strip().rstrip('.') + '. '
        prompt += ' '.join('Image %d is a character reference%s.' %
                          (i, ' for ' + p['name'] if p['name'] else '') for i, p in enumerate(people, first_image))
        prompt += ' ' + position_prompt(people, first_image=first_image)
        prompt += (' Render one natural photograph with only these %d people, matching the scene lighting, '
                   'with soft contact shadows and no cut-out edges. Use the character references for appearance and clothing.'
                   % len(people))
    for name in names:
        load(name)
    if len(prompt) > 2000:
        raise ValueError('invalid_prompt')
    return dict(prompt=prompt, width=width, height=height, seed=value.get('seed'), images=names,
                **({'loras': value['loras']} if 'loras' in value else {}))
