"""The built-in AI picks a ready-made white-model scene for a shot instead of writing scene code.

Two short questions go to the local prompt model: first whether any template fits at all (a single 是/否), then,
only on a yes, a five-field JSON choice. A no, an unreadable answer or an invalid choice means no white model:
the video is generated as usual. Scene code is never taken from the model; the chosen template's code runs
with its few parameters applied by a fixed wrapper."""
import json
import math
import re

from .whitebox_presets import preset

# The templates the built-in AI may choose: those in the ready-made scenes whose generated video was measured to
# follow the white model. Adding or retiring one is a change to the scenes and these tables.
ENABLED = ('cat',)
GATE_NAMES = dict(cat='猫在客厅逐层跳上书架')
PICK_NAMES = dict(cat='橘猫跳上书架')
# Camera height offsets (metres) each template tolerates without the camera meeting a wall or leaving the subject.
SAFE_HEIGHT = dict(cat=(-0.5, 0.5))
LIMITS = (('speed', .7, 1.3), ('camera_height', -1, 3), ('fov_scale', .8, 1.25))
KEYS = ('template', 'hero_color', 'speed', 'camera_height', 'fov_scale')
NONE = dict(template='none', hero_color=None, speed=1, camera_height=0, fov_scale=1)
GATE_TOKENS = 16
PICK_TOKENS = 120
SYSTEM = '你是视频制作助手，严格按要求的格式回答。'

GATE_PROMPT = """判断镜头能否用以下任一固定模板，只答是或否。
模板：{NAMES}
镜头与模板的动作基本一致即答是。只调颜色、速度、机位高度、视野无法实现的镜头答否。
镜头：{SHOT}"""
PICK_PROMPT = """镜头：{SHOT}
模板：{NAMES}
只输出JSON，五字段全齐：template(以上id；都不像选none)，hero_color(主体#RRGGBB；未提颜色填null)，speed(时间倍率0.7~1.3)，camera_height(相机高度偏移-1~3米)，fov_scale(视野倍率0.8~1.25)。未指定数值用1、0、1。"""

# Applies the parameters to a built template; embedded in the compiled scene as it is.
APPLY = """function applyParameters(base, p) {
  const {scene, camera} = base;
  if (p.hero_color !== null) {
    scene.traverse(object => {
      if (!object.isMesh) return;
      let hero = false;
      for (let node = object; node; node = node.parent) {
        if ((node.name || '').startsWith('hero')) { hero = true; break; }
      }
      if (!hero) return;
      const recolor = material => {
        const copy = material.clone();
        if (copy.color) copy.color.set(p.hero_color);
        return copy;
      };
      object.material = Array.isArray(object.material) ? object.material.map(recolor) : recolor(object.material);
    });
  }
  camera.fov *= p.fov_scale;
  camera.updateProjectionMatrix();
  let previousOffset = 0;
  return {scene, camera, update(t) {
    if (typeof t !== 'number' || !Number.isFinite(t)) throw new Error('invalid_time');
    camera.position.y -= previousOffset;
    base.update(Math.min(p.seconds, Math.max(0, t * p.speed)));
    camera.position.y += p.camera_height;
    previousOffset = p.camera_height;
  }};
}"""


def gate_question(shot):
    return GATE_PROMPT.format(NAMES='；'.join(GATE_NAMES[i] for i in ENABLED), SHOT=shot)


def pick_question(shot):
    return PICK_PROMPT.format(NAMES='；'.join(i + ' ' + PICK_NAMES[i] for i in ENABLED) + '。', SHOT=shot)


def gate_allows(text):
    """True for 是, False for 否; anything else is not an answer."""
    answer = text.strip() if isinstance(text, str) else ''
    if answer not in ('是', '否'):
        raise ValueError('gate_invalid')
    return answer == '是'


def parameters(value):
    """The five fields, checked strictly; the choice may name none or an enabled template only."""
    if not isinstance(value, dict) or set(value) != set(KEYS):
        raise ValueError('params_invalid')
    if value['template'] != 'none' and value['template'] not in ENABLED:
        raise ValueError('params_invalid')
    color = value['hero_color']
    if color is not None and not (isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color)):
        raise ValueError('params_invalid')
    for key, low, high in LIMITS:
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) \
                or not low <= number <= high:
            raise ValueError('params_invalid')
    return {key: value[key] for key in KEYS}


def effective(value):
    """The parameters as rendered: camera height limited to what the template can hold."""
    result = parameters(value)
    if result['template'] != 'none':
        low, high = SAFE_HEIGHT[result['template']]
        result['camera_height'] = min(high, max(low, result['camera_height']))
    return result


def compile_scene(value):
    """Scene code for the page: the template's own code plus the fixed parameter wrapper; None for no template."""
    params = effective(value)
    if params['template'] == 'none':
        return None
    row = preset(params['template'])
    if row is None:
        raise ValueError('template_invalid')
    source = row['code']
    seconds = row['seconds']
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 12:
        raise ValueError('template_invalid')
    head = 'export function build(THREE)'
    if source.count(head) != 1:
        raise ValueError('template_invalid')
    return (source.replace(head, 'function buildTemplate(THREE)') + '\n' + APPLY + '\nexport function build(THREE) {\n'
            '  return applyParameters(buildTemplate(THREE), ' + json.dumps(dict(params, seconds=seconds)) + ');\n}\n')


def pick(shot, ask):
    """Choose a scene for the shot. `ask(question, system, answer_format, max_tokens)` returns the model's answer
    row (`text`, and `json` for the JSON format). The result always has `params`; `code` is None, and `reason`
    says why, whenever no white model should be made. A failure to ask at all (timeout, connection) is one of those."""
    try:
        if not gate_allows(ask(gate_question(shot), SYSTEM, 'text', GATE_TOKENS).get('text')):
            return dict(params=dict(NONE), code=None, reason='gate_no')
        row = ask(pick_question(shot), SYSTEM, 'json', PICK_TOKENS)
        params = effective(row.get('json'))
        code = compile_scene(params)
    except (OSError, TimeoutError):
        # The model could not be asked (timeout, connection): the video is made without a white model.
        return dict(params=dict(NONE), code=None, reason='ask_failed')
    except (ValueError, AttributeError, TypeError, KeyError) as error:
        reason = str(error)
        return dict(params=dict(NONE), code=None, reason=reason if reason in ('gate_invalid', 'params_invalid') else 'params_invalid')
    return dict(params=params, code=code, reason='none' if code is None else 'template')
