import {TUTORIAL_CHAPTERS} from './tutorial_book_content.js';

const COPY = {
    zh: {book: '教程', contents: '目录', close: '关闭', limits: '限制', start: '开始演示', tryIt: '试一试'},
    en: {book: 'Tutorial', contents: 'Contents', close: 'Close', limits: 'Limits', start: 'Start walkthrough', tryIt: 'Try it'},
};
const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
};
const language = () => {
    const choice = new URLSearchParams(location.search).get('freevideo_lang');
    return choice === 'zh' || (choice !== 'en' && navigator.language?.toLowerCase().startsWith('zh')) ? 'zh' : 'en';
};

// Media paths are filenames under this module's bundled tutorial directory only.
// Both images (including animated WebP) and short silent MP4 loops are supported.
function mediaElement(media, lang, title) {
    const file = typeof media.src === 'string' ? media.src : media.src[lang];
    if (!/^[a-z0-9][a-z0-9_-]*\.(webp|png|mp4)$/i.test(file)) throw new Error('Invalid tutorial media filename');
    const node = el(media.type === 'video' ? 'video' : 'img', 'fv-book-media');
    node.src = new URL(`./tutorial/${file}`, import.meta.url).href;
    if (media.type === 'video') {
        node.muted = true;
        node.loop = true;
        node.autoplay = true;
        node.playsInline = true;
        node.controls = true;
        node.setAttribute('aria-label', media.alt?.[lang] || title);
        if (matchMedia('(prefers-reduced-motion: reduce)').matches) node.autoplay = false;
    } else {
        node.alt = media.alt?.[lang] || title;
        node.decoding = 'async';
    }
    return node;
}

let current = null;

/** Open the bundled manual; chapter is one of the ids in tutorial_book_content.js. */
export function openTutorialBook({chapter = TUTORIAL_CHAPTERS[0].id} = {}) {
    if (current?.dialog.isConnected) {
        current.select(chapter);
        return current.dialog;
    }
    if (!document.querySelector('link[href*="theme.css"]')) {
        const theme = el('link'); theme.rel = 'stylesheet'; theme.href = new URL('./theme.css', import.meta.url).href;
        document.head.append(theme);
    }
    if (!document.querySelector('link[data-fv-tutorial-book]')) {
        const css = el('link'); css.rel = 'stylesheet'; css.dataset.fvTutorialBook = '';
        css.href = new URL('./tutorial_book.css', import.meta.url).href;
        document.head.append(css);
    }
    const lang = language(), text = COPY[lang], restore = document.activeElement;
    const dialog = el('dialog', 'fv-tutorial-book');
    dialog.lang = lang === 'zh' ? 'zh-CN' : 'en';
    dialog.setAttribute('aria-labelledby', 'fv-book-title');
    const header = el('header', 'fv-book-header'), title = el('h1', null, text.book);
    title.id = 'fv-book-title';
    const close = el('button', 'fv-book-close', text.close); close.type = 'button';
    header.append(title, close);
    const layout = el('div', 'fv-book-layout'), nav = el('nav', 'fv-book-nav');
    nav.setAttribute('aria-label', text.contents);
    const mobile = el('label', 'fv-book-picker', text.contents), picker = el('select');
    picker.setAttribute('aria-label', text.contents); mobile.append(picker);
    const article = el('article', 'fv-book-article'); article.tabIndex = 0;
    const navButtons = new Map();
    let start = null, tryButton = null;
    const syncHowTo = () => {
        if (!start) return;
        const button = start, id = dialog.dataset.chapter;
        button.disabled = typeof window.freevideoHowTo?.start !== 'function';
        button.hidden = button.disabled;
        // A walk-through that this device cannot do (no longer videos in parts) is not offered.
        if (!button.disabled) Promise.resolve(window.freevideoHowTo.available?.(id)).then(ok => {
            if (ok === false && start === button) { button.disabled = true; button.hidden = true; }
        }).catch(() => {});
    };
    // "Try it" puts the chapter's example into the creation panel through the same entry as the feature cards.
    const syncTry = () => {
        if (!tryButton) return;
        tryButton.hidden = tryButton.disabled = typeof window.freevideoHowTo?.tryIt !== 'function';
    };
    function select(id) {
        const item = TUTORIAL_CHAPTERS.find(c => c.id === id) || TUTORIAL_CHAPTERS[0];
        dialog.dataset.chapter = item.id;
        picker.value = item.id;
        for (const [key, button] of navButtons) {
            if (key === item.id) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
        }
        article.querySelectorAll('video').forEach(video => video.pause());
        article.replaceChildren(el('h2', 'fv-book-chapter-title', item.title[lang]));
        start = null;
        article.append(el('p', 'fv-book-summary', item.summary[lang]), mediaElement(item.media, lang, item.title[lang]));
        const steps = el('ol', 'fv-book-steps');
        item.steps[lang].forEach(step => steps.append(el('li', null, step)));
        const limits = el('section', 'fv-book-limits'), list = el('ul');
        limits.append(el('h3', null, text.limits), list);
        item.limits[lang].forEach(limit => list.append(el('li', null, limit)));
        start = el('button', 'fv-book-start', text.start); start.type = 'button';
        start.onclick = () => {
            syncHowTo();
            if (start.disabled) return;
            dialog.close();
            window.freevideoHowTo?.start(item.id);
        };
        tryButton = null;
        if (item.example) {
            tryButton = el('button', 'fv-book-start fv-book-try', text.tryIt); tryButton.type = 'button';
            tryButton.onclick = () => {
                syncTry();
                if (tryButton.disabled) return;
                dialog.close();
                window.freevideoHowTo.tryIt(item.example, {chapter: item.id});
            };
        }
        article.append(steps, ...(tryButton ? [tryButton] : []), start, limits);
        syncHowTo();
        syncTry();
        article.scrollTop = 0;
    }
    for (const item of TUTORIAL_CHAPTERS) {
        const button = el('button', 'fv-book-chapter', item.title[lang]); button.type = 'button';
        button.dataset.chapter = item.id;
        button.onclick = () => select(item.id);
        navButtons.set(item.id, button); nav.append(button);
        const option = el('option', null, item.title[lang]);
        option.value = item.id; picker.append(option);
    }
    picker.onchange = () => select(picker.value);
    close.onclick = () => dialog.close();
    // Stop workspace shortcuts while reading, including Control+Enter and Delete.
    for (const name of ['keydown', 'keypress', 'keyup']) dialog.addEventListener(name, event => event.stopPropagation());
    dialog.addEventListener('close', () => {
        dialog.querySelectorAll('video').forEach(video => video.pause());
        window.removeEventListener('focus', syncHowTo);
        window.removeEventListener('focus', syncTry);
        window.removeEventListener('freevideo-howto-ready', syncHowTo);
        window.removeEventListener('freevideo-howto-ready', syncTry);
        dialog.remove(); current = null;
        if (restore?.isConnected) restore.focus({preventScroll: true});
    }, {once: true});
    window.addEventListener('focus', syncHowTo);
    window.addEventListener('focus', syncTry);
    window.addEventListener('freevideo-howto-ready', syncHowTo);
    window.addEventListener('freevideo-howto-ready', syncTry);
    layout.append(nav, article); dialog.append(header, mobile, layout);
    document.body.append(dialog);
    current = {dialog, select}; select(chapter); dialog.showModal(); close.focus();
    return dialog;
}

/** Append an entry to a settings row, or return it for the caller to place.
 * Example: mountBookEntry({container: row, chapter: 'reference-video'}).
 * Callers that own another modal may close it in beforeOpen.
 */
export function mountBookEntry({container, chapter, beforeOpen} = {}) {
    const button = el('button', 'fv-book-entry', COPY[language()].book); button.type = 'button';
    button.onclick = () => { beforeOpen?.(); openTutorialBook({chapter}); };
    container?.append(button);
    return button;
}
