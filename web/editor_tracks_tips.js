// Hover and focus help for the timeline and the transport: a name, one line on what the control does, its key, and
// for a greyed control the reason and how to clear it. The page-wide tooltip (tooltip.js) reads the data-fv-tip-*
// attributes; the native title is dropped so two tips never show at once. No DOM work happens at import time.
export const modKey = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/i.test(navigator.userAgentData?.platform || navigator.platform || '') ? '⌘' : 'Ctrl+';

import {setTip} from './tip_words.js';
export {setTip};
// Each entry: [English name, Chinese name, English purpose (≤ 10 words), Chinese purpose (≤ 20 characters), key].
// Keys use modKey at run time where a modifier is involved.
const COPY = {
    addAudio: ['Add audio track', '添加声音轨', 'Another track for music or a voice', '多一条轨，放配乐或旁白', ''],
    removeAudio: ['Remove Audio 2', '移除声音 2', 'Take away the second audio track', '去掉第二条声音轨', ''],
    playhead: ['Playhead', '播放头', 'Drag to move through the edit', '拖动到要看的画面', '← →'],
    marker: ['Add marker', '添加标记', 'Mark the playhead position', '在播放头处做一个记号', 'M'],
    timelineMarker: ['Marker', '标记', 'Drag to move it; Delete removes it', '拖动可移动，Delete 删除', '⇧M / ⌥M'],
    snapping: ['Snap', '吸附', 'Snap to cuts and markers while dragging', '拖动时对齐剪辑点和标记', 'S'],
    shortcuts: ['Shortcuts', '快捷键', 'See every keyboard shortcut', '查看全部键盘操作', ''],
    loop: ['Loop', '循环播放', 'Start over when playback reaches the end', '播到结尾后从头再来', '⇧L'],
    playRange: ['Play range', '只播范围', 'Play only the selected part or range', '只播选中的片段或范围', '⇧Space'],
    fullscreen: ['Full screen', '全屏', 'Fill the screen with the picture', '画面铺满整个屏幕', 'F'],
    saveFrame: ['Save frame', '保存当前帧', 'Save this frame as a picture', '把这一帧存成图片', ''],
    previewVolume: ['Preview volume', '预览音量', 'Only what you hear here, not the export', '只调这里听到的，不影响导出', ''],
    previewVolumeSlider: ['Preview volume', '预览音量', 'Drag to set how loud the preview plays', '拖动调整预览的响度', '↑ ↓'],
    play: ['Play', '播放', 'Play from the playhead', '从播放头开始播放', 'Space'],
    pause: ['Pause', '暂停', 'Hold on this frame', '停在当前画面', 'Space'],
    start: ['Go to start', '跳到开头', 'Move the playhead to the first frame', '播放头回到第一帧', 'Home'],
    end: ['Go to end', '跳到结尾', 'Move the playhead to the last frame', '播放头到最后一帧', 'End'],
    previousFrame: ['Previous frame', '上一帧', 'Step back one frame', '后退一帧', '← (⇧← 10)'],
    nextFrame: ['Next frame', '下一帧', 'Step forward one frame', '前进一帧', '→ (⇧→ 10)'],
    zoomOut: ['Zoom out', '缩小', 'Show more of the timeline', '时间线显示得更长', '−'],
    zoomIn: ['Zoom in', '放大', 'Show the timeline in more detail', '时间线看得更细', '+'],
    zoomSlider: ['Track zoom', '轨道缩放', 'Drag to show more or less time', '拖动调整时间线的疏密', ''],
    fit: ['Fit all', '看全部', 'Fit the whole edit in view', '整条剪辑放进当前宽度', '⇧Z'],
    report: ['Download report', '下载报告', 'Save the details so this can be fixed', '附上诊断信息，便于修复', ''],
    retry: ['Retry', '重试', 'Generate this part again', '按同样的设置再生成一次', ''],
    closePreview: ['Back to timeline', '回到时间线', 'Leave the media preview', '结束素材预览', ''],
    fill: ['Fill', '铺满', 'Crop the edges so the picture fills the frame', '裁掉多出的边，铺满画面', ''],
    fitPicture: ['Fit', '完整显示', 'Keep the whole picture, with bars at the sides', '保留整幅画面，两边留黑', ''],
    mute: ['Mute', '静音', 'Silence this track', '这条轨不出声', ''],
    unmute: ['Unmute', '取消静音', 'Hear this track again', '恢复这条轨的声音', ''],
    solo: ['Solo', '独奏', 'Hear only this track', '只听这条轨', ''],
    unsolo: ['End solo', '取消独奏', 'Hear every track again', '恢复听全部轨道', ''],
    lock: ['Lock', '锁定', 'Protect this track from edits', '防止误改这条轨', ''],
    unlock: ['Unlock', '解锁', 'Allow edits on this track again', '允许修改这条轨', ''],
    hide: ['Hide', '隐藏', 'Leave this track out of preview and export', '预览和导出里不显示', ''],
    show: ['Show', '显示', 'Show this track in preview and export', '在预览和导出里显示', ''],
    // The picture adjustments of one part (inspector, Picture tab).
    scale: ['Scale', '缩放', "Drag to enlarge or shrink this part's picture", '拖动放大或缩小这段画面', ''],
    scaleNumber: ['Scale %', '缩放比例', 'Type a value from 10 to 1000 %', '输入 10 至 1000 的百分比', '↑ ↓'],
    offsetX: ['Horizontal position', '水平位置', 'Move the picture left or right, in percent', '左右移动画面，按百分比', '↑ ↓ (⇧ 10)'],
    offsetY: ['Vertical position', '垂直位置', 'Move the picture up or down, in percent', '上下移动画面，按百分比', '↑ ↓ (⇧ 10)'],
    rotate: ['Rotation', '旋转角度', 'Type an angle from −180° to 180°', '输入 −180° 至 180° 的角度', '↑ ↓'],
    rotateLeft: ['Rotate left 90°', '左转 90°', 'Turn the picture a quarter turn left', '画面逆时针转四分之一圈', ''],
    rotateRight: ['Rotate right 90°', '右转 90°', 'Turn the picture a quarter turn right', '画面顺时针转四分之一圈', ''],
    flipH: ['Flip horizontally', '水平翻转', 'Mirror this part left to right', '左右镜像这段画面', ''],
    flipV: ['Flip vertically', '垂直翻转', 'Turn this part upside down', '上下翻转这段画面', ''],
    crop: ['Crop', '裁剪', 'Drag the frame on the picture to trim edges', '在画面上拖边框，裁掉多余部分', ''],
    cropDone: ['Done', '完成', 'Keep this crop', '确认这次裁剪', 'Enter'],
    cropCancel: ['Cancel', '取消', 'Discard this crop', '放弃这次裁剪', 'Esc'],
    pictureReset: ['Reset', '恢复原样', 'Remove scale, position, rotation and crop', '去掉缩放、位置、旋转和裁剪', ''],
};
export const TIP_KEYS = Object.freeze(Object.keys(COPY));

// The words of one entry in the page language; `t(en, zh)` is the editor's translator.
export function tipCopy(id, t = en => en) {
    const row = COPY[id];
    if (!row) throw new Error(`Unknown tip ${id}`);
    const [en, zh, useEn, useZh, key] = row;
    return {name: t(en, zh), use: t(useEn, useZh), key: key.replace('Space', t('Space', '空格'))};
}

export function applyTip(element, id, t, {why = '', key} = {}) {
    const words = tipCopy(id, t);
    return setTip(element, {...words, ...(key === undefined ? {} : {key}), why});
}

// Track head switches are drawn by the audio and text modules; their state is in data-flag and aria-pressed.
const SWITCHES = {muted: ['mute', 'unmute'], solo: ['solo', 'unsolo'], locked: ['lock', 'unlock'], hidden: ['hide', 'show']};
export function switchTip(button, t) {
    const pair = SWITCHES[button.dataset.flag];
    if (!pair) return;
    const active = button.getAttribute('aria-pressed') === 'true';
    // A switch the track cannot use keeps the reason its module wrote as its title.
    const why = button.disabled ? (button.dataset.fvTipWhy || button.getAttribute('title') || button.getAttribute('aria-label') || '') : '';
    if (button.disabled && why) button.dataset.fvTipWhy = why;
    const id = pair[active ? 1 : 0], words = tipCopy(id, t), signature = `${id}|${why}`;
    if (button.dataset.fvTipFor === signature && !button.title) return;
    button.dataset.fvTipFor = signature;
    setTip(button, {...words, why});
}
