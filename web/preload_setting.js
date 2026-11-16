// Settings › Advanced › "Preload ahead": how eagerly the engine loads models before a generation is asked for.
//   'eager' (default): when a generation looks near, the models are loaded early, even before the first generation.
//   'conservative': nothing is loaded early before a generation; models already used stay loaded, so editing the
//     prompt and generating again stays quick.
// Pages send their warm requests as before and add the mode; the engine decides what to do with it:
//   import { preloadMode } from './preload_setting.js';
//   body = {...warm, mode: preloadMode()}            // read right before every warm request; no mode means 'eager'
export const PRELOAD_KEY = 'freevideo.preload';    // 'eager' | 'conservative'; missing or anything else = 'eager'
export const PRELOAD_EVENT = 'freevideo-preload';  // fired on window with detail {mode} when it changes on this page

export function preloadMode() {
    try { return localStorage.getItem(PRELOAD_KEY) === 'conservative' ? 'conservative' : 'eager'; } catch { return 'eager'; }
}

export function setPreloadMode(mode) {
    const value = mode === 'conservative' ? 'conservative' : 'eager';
    try { localStorage.setItem(PRELOAD_KEY, value); } catch { /* Kept for this page only. */ }
    window.dispatchEvent(new CustomEvent(PRELOAD_EVENT, {detail: {mode: value}}));
}

// The listener gets the new mode, from this page or another FreeVideo tab; returns the function that stops it.
export function onPreload(listener) {
    const here = event => listener(event.detail?.mode === 'conservative' ? 'conservative' : 'eager');
    const there = event => { if (event.key === PRELOAD_KEY) listener(preloadMode()); };
    window.addEventListener(PRELOAD_EVENT, here); window.addEventListener('storage', there);
    return () => { window.removeEventListener(PRELOAD_EVENT, here); window.removeEventListener('storage', there); };
}
