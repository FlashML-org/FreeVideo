"""Pictures of a cast card made by the image model: one place for the view prompts and sizes.

The front view comes from the description (or from a user's photo, edited into a clean card); every
other view of a character is an edit of the front picture, so they show the same person. A setting is
a single wide picture from text.
"""

import re

VIEWS = ('front', 'three_quarter', 'side', 'back', 'face')
SETTING_VIEWS = ('scene',)

SIZES = {
    'front': (832, 1472),
    'three_quarter': (832, 1472),
    'side': (832, 1472),
    'back': (832, 1472),
    'face': (1024, 1024),
    'scene': (1344, 768),
}

BACKDROP = 'in front of a plain light grey studio backdrop, even soft studio light, sharp focus, no text'
SAME = (' Keep the same person, facial features, hairstyle, clothes and accessories. Full body standing, '
        'feet visible, plain light grey background, soft even light. One photograph of one person.')

_VIEW_TEXT = {
    'three_quarter': ('Turn the person in image 1 toward the left side of the picture by 45 degrees. '
                      'Turn the head and body together. Show the front-left three-quarter view, '
                      'with both eyes visible.'),
    'side': ('将图1中的同一个人完全转向画面左边，呈现90度正侧面。鼻尖、胸口、脚尖朝向画面左边，'
             '只能看见一只眼睛，近侧肩膀遮住远侧肩膀。'),
    'back': ('将图1中的同一个人整个身体转过180度，完全背对镜头。'
             '头、肩膀、躯干、臀部和双脚一起转过去，后脑勺、发旋、颈后、背部、肩胛骨、臀部和脚后跟朝向镜头。'
             '头和身体方向一致。看不到脸、胸口、衣服前襟、前面的扣子或衣领开口，耳朵只露边缘。只展示衣服背面。'),
    'face': 'front-facing face close-up, framed from shoulders upward, centered head, eyes looking at the camera, neutral expression',
}

_HAS_GLASSES = re.compile(r'\b(?:eye|sun)?glasses\b|眼镜|太阳镜', re.IGNORECASE)
_NO_GLASSES = re.compile(
    r'\b(?:no|without|not wearing|does not wear)\s+(?:[\w-]+\s+){0,3}(?:eye|sun)?glasses\b'
    r'|(?:不戴|不佩戴|未戴|没有|无)[^，。；,.;]{0,8}(?:眼镜|太阳镜)', re.IGNORECASE)


def _eyewear(card):
    description = card.get('description') or ''
    if _HAS_GLASSES.search(description) and not _NO_GLASSES.search(description):
        return 'wearing the same glasses'
    if card.get('photo') and not _NO_GLASSES.search(description):
        # The front comes from a user's photo and the text is silent: the truth is in the picture,
        # and naming glasses either way makes the model add or drop them.
        return 'the same face exactly as in image 1, with nothing added to it or removed from it'
    return 'bare eyes, no glasses'


def size(view):
    return SIZES[view]


def uses_front(view):
    """Whether the view is an edit of the card's front picture (false for the front itself and for scenes)."""
    return view in _VIEW_TEXT


def _who(card):
    return (card.get('description') or card['name']).strip().rstrip('.')


def view_prompt(card, view):
    """The prompt for one view of a card (a dict with name, description, kind)."""
    who = _who(card)
    if card.get('kind') == 'setting':
        # The time, weather, light and colour the description gives are the point of a scene: only the people and text are barred.
        return '%s. A wide view of the place, no people, sharp focus, no text.' % who
    if view == 'front':
        return ('%s. The person stands alone, the whole body from head to feet, facing the camera, neutral pose '
                'with the arms relaxed, %s.' % (who, BACKDROP))
    if view not in _VIEW_TEXT:
        raise ValueError('unknown view')
    eyewear = _eyewear(card)
    if view == 'face':
        return ('Same person as image 1: same face, hair, clothes and body; plain light grey studio backdrop; '
                '%s. Treat image 1 as the identity and wardrobe reference. Photograph this exact person '
                'from the requested camera angle. The head, shoulders, torso and feet must all follow '
                'that angle; do not keep a frontal head on a rotated body. Preserve the facial proportions, '
                'age, hairline, hairstyle, %s, clothing details and accessories from image 1. Keep even '
                'soft studio light. Show only one person in one photograph, no text, no collage.'
                % (_VIEW_TEXT[view], eyewear))
    if view == 'side':
        return (_VIEW_TEXT[view] + eyewear + '.保持同一张脸、发型、衣服和配件。全身站立，双脚入画，'
                '浅灰纯色背景，均匀柔光，只画一个人的一张照片。')
    return '%s %s.%s' % (_VIEW_TEXT[view], eyewear, SAME)


def photo_prompt():
    """Edit a user's photo (image 1) into the clean front picture of a card."""
    return ('Image 1 shows the person. Make it a clean character reference photo: the whole body from head to '
            'feet. Turn the head and body to face us. Neutral pose with the arms relaxed, %s. Keep exactly '
            'the same person, face, hair and clothes as in image 1. Preserve all existing accessories; '
            'add no new accessories.' % BACKDROP)


def page_views(description, kind='character'):
    """What the page needs to make the pictures of a new character itself: each view's prompt and size.

    The page runs the pictures one by one so each shows when it is done; the prompts stay here, so a
    card made from a description and these pictures are made from the same words."""
    if not isinstance(description, str) or not description.strip() or len(description) > 1000:
        raise ValueError('invalid_prompt')
    if kind not in ('character', 'setting'):
        raise ValueError('invalid_request')
    card = dict(name=description.strip()[:120], description=description.strip(), kind=kind)
    views = SETTING_VIEWS if kind == 'setting' else VIEWS
    return dict(views=[dict(view=view, prompt=view_prompt(card, view), width=SIZES[view][0],
                            height=SIZES[view][1], from_front=uses_front(view)) for view in views])
