"""Positive sound wording for the text encoder.

The model has no negative prompt, so "no music" only draws attention to music. Sound requests that
exclude something are replaced by the sounds the scene actually has. A prompt that asks for music
is returned unchanged, and quoted or tagged speech is never edited. A prompt with spoken lines, quoted or described, gets no added
sentence either: it only lost the excluded sound words, because the sentence made the spoken lines less accurate.
"""
import re

from .prompt_vlm.rules import QUOTED, QUOTES

MUSIC = (r'(?:background\s+music|music|soundtrack|BGM|cinematic\s+(?:orchestral\s+)?score|orchestral\s+score|song|'
         r'背景音乐|音乐|配乐|歌曲|歌声)')
PERFORMED = (r'(?:\b(?:sings?|singing|singers?|bands?|DJ|whistl(?:e|es|ed|ing)|choir)\b|'
         r'\b(?:plays?|playing|played)\s+(?:an?\s+|the\s+)?(?:\w+\s+)?(?:guitar|piano|violin|cello|drums?|saxophone|trumpet|flute|keyboard|harp|accordion|ukulele|bass)\b|'
         r'唱歌|歌手|乐队|演奏|弹(?:吉他|钢琴)|拉(?:小提琴|大提琴)|吹(?:口哨|笛子|萨克斯)|口哨|打鼓|合唱)')
ANY_MUSIC = r'(?:' + MUSIC + '|' + PERFORMED + ')'
NEGATION = (r'(?:\b(?:no|without|avoid|exclude)\b|\bdo\s+not\s+(?:add|include|use|play)\b|'
            r'请?(?:不要|别|不用|不需要|无需|不加|没有|去掉|去除|禁止|无)(?:任何|有|放|加|使用|出现)?)')
SOUND_WORD = (r'(?:background\s+music|music|soundtrack|BGM|subtitles?|captions?|narration|voiceover|singing|audio|sound|'
              r'背景音乐|音乐|配乐|歌曲|歌声|字幕|旁白|解说|声音|音效)')
# An exclusion is cut out only when it ends the clause; "no music at all" or "没有音乐的派对" would be left broken.
NEGATED = re.compile(r'(?:(?:[,，]\s*)|\b(?:with|and|but|plus)\s+)?' + NEGATION + r'\s*(?:any\s+)?' + SOUND_WORD +
                     r'(?:\s+(?:or|and)\s+(?:music|subtitles?|captions?|narration|voiceover)|[和或及、]\s*(?:音乐|背景音乐|字幕|旁白|解说|音效))*'
                     r'(?:\s+of\s+[^,.;，。]*)?(?=\s*(?:[,，;；.。!?！？]|$))', re.I)
SILENT = re.compile(r'\bsilent\b|\bmute\b|\bno\s+(?:sound|audio)\b(?!\s+of)|无声|静音', re.I)
TAGGED = re.compile(r'<(d|en|zh|ja|ko|cn)>.*?</\1>', re.S)
SOUNDSCAPE = re.compile(r'(?m)^[ \t]*overall_soundscape[ \t]*:')
CLAUSE_EN = 'The sounds in the scene are the natural sounds of what is happening and its surroundings.'
CLAUSE_ZH = '画面里的声音是正在发生的动作和周围环境自然发出的声音。'
SPEECH = re.compile(r'\b(?:says?|said|speaks?|speaking|talks?|talking|whispers?|shouts?|asks?|replies|tells?|announces?)\b|'
                    r'说|讲|喊|问|答|念|朗读|台词|对白', re.I)
CHINESE = re.compile(r'[一-鿿]')


def _protect(prompt):
    """Prompt with quoted and tagged speech swapped for placeholders, and the saved pieces."""
    kept = []

    def save(match):
        kept.append(match.group())
        return '\x00%d\x00' % (len(kept) - 1)

    return QUOTED.sub(save, TAGGED.sub(save, prompt)), kept


def _restore(text, kept):
    return re.sub('\x00(\\d+)\x00', lambda m: kept[int(m.group(1))], text)


def wants_music(prompt):
    """True when music is requested. Negated mentions and the visual 'music video' style are not requests."""
    body, _ = _protect(prompt)
    excluded = any(re.search(MUSIC, m.group(), re.I) for m in NEGATED.finditer(body))
    body = NEGATED.sub('', body)
    body = re.sub(r'\bmusic\s+video\b|\bMV\b|音乐视频', '', body, flags=re.I)
    # Music that was explicitly excluded is not requested back by someone who sings or plays in the scene.
    return bool(re.search(MUSIC if excluded else ANY_MUSIC, body, re.I))


def positive_sound(prompt):
    if not prompt.strip() or SOUNDSCAPE.search(prompt) or wants_music(prompt):
        return prompt
    body, kept = _protect(prompt.strip())
    if SILENT.search(body):
        return prompt
    chinese = bool(CHINESE.search(body))
    body = re.sub(r'\bmusic\s+video\b', '\x01', body, flags=re.I)
    body = NEGATED.sub('', body)
    body = re.sub(r'\s+([,.;!?])', r'\1', body)
    body = re.sub(r'[,，;；]\s*(?=[,，;；.。!?！？]|$)', '', body)
    body = re.sub(r'([.。!?！？])(?:\s*[.。])+', r'\1', body).strip(' ,，;；').lstrip('.。 ').replace('\x01', 'music video')
    clause = CLAUSE_ZH if chinese else CLAUSE_EN
    if kept or SPEECH.search(body) or clause in _restore(body, kept):
        return _restore(body, kept)
    if body and _restore(body, kept)[-1] not in '.。!?！？' + ''.join(QUOTES.values()):
        body += '。' if chinese else '.'
    return (_restore(body, kept) + ' ' + clause).strip()
