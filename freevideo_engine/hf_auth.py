"""Optional session credentials; never written to a settings or receipt file."""
import os
import re

TOKEN_URL = 'https://huggingface.co/settings/tokens'
HELP_EN = ('Optional: add a Hugging Face read token to use your account’s download quota. '
           'This can reduce anonymous throttling; it does not guarantee higher speed. '
           'Kept for this session only. You can continue without it.')
HELP_ZH = ('可选：填写 Hugging Face 读取 Token，使用账号下载额度，减少匿名限流。'
           '可能更快，但不保证提速。仅本次打开有效，不填写也能继续。')
ERROR_EN = 'Enter a token that begins with hf_ and contains only letters and digits, or leave the field empty.'
ERROR_ZH = '令牌应以 hf_ 开头，只含英文字母和数字；也可留空。'


def clean(value):
    """Remove surrounding whitespace, paired quotes and an HF_TOKEN= prefix."""
    if not isinstance(value, str):
        raise ValueError(ERROR_EN)
    value = value.strip()
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
        value = value[1:-1].strip()
    if value[:9].upper() == 'HF_TOKEN=':
        value = value[9:].strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1].strip()
    return value


def validate(value):
    value = clean(value)
    if value and not re.fullmatch(r'hf_[A-Za-z0-9]{1,4093}', value):
        raise ValueError(ERROR_EN)
    return value


def environment(token='', environ=None):
    env = dict(os.environ if environ is None else environ)
    token = validate(token)
    if token:
        env['HF_TOKEN'] = token
    return env
