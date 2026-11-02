"""Whether a prompt asks for no music, and what that means for the score the model is given.

H3 reads the score from its own `non_diegetic_music` section. The starter prompt carries a full
score there, so a user who adds "no background music" to the scene text but leaves that section
as it came still gets the score. Only that untouched starter text is replaced by `N/A`; a score
the user wrote stays as written.
"""
import re

from .default_prompt import DEFAULT_PROMPT

SECTION = re.compile(r'(?m)^[ \t]*non_diegetic_music[ \t]*:[ \t]*(.*?)(?=\n\s*\n|\Z)', re.S)
NOTE = 'non_diegetic_music: N/A'


def _words(text):
    return ' '.join(text.split())


STARTER_MUSIC = _words(SECTION.search(DEFAULT_PROMPT).group(1))

# A music word that is not another noun: "music video", "music stand", 音乐会.
_EN_TERM = (r'(?:(?:background|backing|ambient|incidental|film|movie|cinematic)\s+)*'
            r'(?:music(?!al\b|ian|\s+(?:video|stand|box|hall|store|shop|school|class|player|room|note|sheet|book|'
            r'festival|stage|teacher|lesson))|bgm\b|soundtrack|musical\s+(?:score|accompaniment)|'
            r'(?:background|film|movie)\s+score|backing\s+track)')
_EN_FILLER = (r'(?:\s+(?:to|the|any|a|an|of|add|include|use|play|put|have|want|need|generate|create|make|produce|'
              r'insert|bring|with|in|on|over|under|any\s+kind\s+of|kind\s+of|sort\s+of|type\s+of|form\s+of)){0,5}')
_EN = [
    re.compile(r'\b(?:no|without|zero)\s+(?:(?:any|the|extra|added|additional)\s+){0,2}' + _EN_TERM, re.I),
    re.compile(r"\b(?:do\s*n[o']t|do\s+not|don[’']t|never|avoid|omit|exclude|skip|remove|mute|disable|"
               r'turn\s+off|leave\s+out|cut)\b' + _EN_FILLER + r'\s+' + _EN_TERM, re.I),
    re.compile(r'\b(?:music|bgm|soundtrack)[- ]free\b|\bfree\s+of\s+' + _EN_TERM + r'|\bno-music\b', re.I),
]

_ZH_TERM = r'(?:背景音乐|背景乐|背景配乐|电影配乐|配乐|伴奏|[Bb][Gg][Mm]|音乐(?!会|剧|人|家|盒|厅|节|学|课|教室|剧院|视频|短片|[Mm][Vv]|老师))'
_ZH_NEG = (r'(?:不要|不用|无需|不需要|不想要|不能有|不可以有|不得有|不许有|严禁|禁止|别|勿|去掉|去除|删掉|删除|取消|关闭|关掉|'
           r'没有|无|不带|不加|不配|不含|不放|不出现|不添加|不使用|不生成|不包含|不要有|不要加|不要带|不要配|不要放)')
_ZH_FILLER = r'(?:任何|任何的|任何形式的|一切|所有|的|有|加|带|配|放|要|出现|添加|使用|生成|含|包含|额外|多余|额外的|多余的|任何额外的){0,3}'
_ZH = [
    re.compile(_ZH_NEG + _ZH_FILLER + _ZH_TERM),
    re.compile(_ZH_TERM + r'(?:请)?(?:不要|不用|不需要|无需|关闭|关掉|去掉|去除|取消|删掉)'),
]


def asks_for_no_music(text):
    """True when the prompt text says, in English or Chinese, that it wants no music."""
    return isinstance(text, str) and any(each.search(text) for each in _EN + _ZH)


def writes_own_music(text):
    """True when the prompt has a score section that is neither the starter's nor N/A."""
    found = SECTION.search(text)
    return bool(found) and _words(found.group(1)) not in (STARTER_MUSIC, 'N/A', '')


def without_music(text):
    """The prompt with the starter's score replaced by `N/A` when the scene text asks for no music."""
    if not asks_for_no_music(text):
        return text
    found = SECTION.search(text)
    if not found or _words(found.group(1)) != STARTER_MUSIC:
        return text
    return text[:found.start()] + NOTE + text[found.end():]
