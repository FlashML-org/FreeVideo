// Settings opens as a room in the FreeVideo shell (settings_room.js). Every caller keeps importing openSetup from here.
// setup.css stays loaded for the small sheets that still use its look (feedback, the compatibility fallback).
import { openSetup } from './settings_room.js';

if (typeof document !== 'undefined') {
    const css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = new URL('./setup.css', import.meta.url).href;
    document.head.append(css);
}

export { openSetup };
