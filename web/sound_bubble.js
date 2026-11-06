// A one-time bubble on the "Separate tracks" button, shown when a clip with sound is first selected.
// It is seen once the button is used, the chapter is opened, the bubble is closed or any click lands; a bubble that only
// fades out is shown one more time.
if (typeof document !== 'undefined' && !document.head.querySelector('link[href$="sound_bubble.css"]')) {
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = new URL('./sound_bubble.css', import.meta.url).href;
    document.head.append(link);
}
const KEY = 'freevideo-sound-bubble', FADE_MS = 8000, OTHER_CARD = '.fv-guide-layer,.fv-first-start,.fv-next-step,body[data-fv-how-to],dialog.fv-feature-tour[open],.fv-tour-ring,dialog.fv-tutorial-book[open]';
const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; } };
const write = value => { try { localStorage.setItem(KEY, JSON.stringify(value)); } catch { /* The bubble then shows again next time. */ } };
export const soundBubbleSeen = () => read().seen === true;
export function markSoundBubbleSeen() { write({seen: true}); globalThis.document?.querySelector('.fv-sound-bubble')?.remove(); }
const TEXT = {line: ['Splits dialogue, music and ambience.', '分离出人声、音乐和环境声。'], view: ['Read the tutorial', '看教程'], close: ['Close', '关闭']};
const NO_BUBBLE = () => {};
/** Show the bubble under `button` when it is due. Returns a function that removes it. */
export const isBubbleRemover = remove => remove !== NO_BUBBLE;
export function showSoundBubble(button, {cn = false} = {}) {
    const t = pair => pair[cn ? 1 : 0], state = read();
    if (state.seen || state.faded > 1 || document.querySelector('.fv-sound-bubble') || document.querySelector(OTHER_CARD) || button.hidden) return NO_BUBBLE;
    const bubble = document.createElement('div'); bubble.className = 'fv-sound-bubble'; bubble.setAttribute('role', 'status');
    const line = document.createElement('p'); line.textContent = t(TEXT.line);
    const view = document.createElement('button'); view.type = 'button'; view.className = 'fv-sound-bubble-view'; view.textContent = t(TEXT.view);
    const close = document.createElement('button'); close.type = 'button'; close.className = 'fv-sound-bubble-close'; close.textContent = '×'; close.setAttribute('aria-label', t(TEXT.close));
    bubble.append(line, view, close);
    const host = button.closest('dialog[open]') || document.body;
    let timer = 0, gone = false;
    const place = () => {
        const r = button.getBoundingClientRect(), panel = host.getBoundingClientRect(), width = Math.min(300, panel.width - 16);
        const left = Math.max(panel.left + 8, Math.min(r.left, panel.right - 8 - width));
        Object.assign(bubble.style, {width: `${width}px`, left: `${left - (host === document.body ? 0 : panel.left + host.clientLeft)}px`, top: `${r.bottom + 8 - (host === document.body ? 0 : panel.top + host.clientTop)}px`});
        bubble.style.setProperty('--fv-sound-arrow', `${Math.max(12, Math.min(r.left + r.width / 2 - left, width - 12))}px`);
    };
    const remove = () => {
        if (gone) return; gone = true; clearTimeout(timer);
        document.removeEventListener('pointerdown', onPointer, true); removeEventListener('resize', place);
        bubble.remove();
    };
    function onPointer(event) { if (!bubble.contains(event.target)) { markSoundBubbleSeen(); remove(); } }
    view.onclick = async () => { markSoundBubbleSeen(); remove(); const {openTutorialBook} = await import('./tutorial_book.js'); openTutorialBook({chapter: 'separate-tracks'}); };
    close.onclick = () => { markSoundBubbleSeen(); remove(); };
    host.append(bubble); place(); addEventListener('resize', place);
    document.addEventListener('pointerdown', onPointer, true);
    timer = setTimeout(() => { write({...read(), faded: (read().faded || 0) + 1}); remove(); }, FADE_MS);
    return remove;
}
