// Fonts for text that FreeVideo draws into pictures and videos (the image editor's text
// layers, the share card): open-source faces that ship with FreeVideo in web/fonts, built
// by scripts/build_web_fonts.py. A font installed on the computer is never used for them:
// each stack ends with FreeVideo Box, whose one glyph, a box, stands for every character,
// so a character none of the bundled fonts has shows a box instead of a system font.
const FAMILIES = {
    sans: {family: 'FreeVideo Sans SC', files: {400: 'FreeVideoSansSC-Regular.woff2', 700: 'FreeVideoSansSC-Bold.woff2'}},
    serif: {family: 'FreeVideo Serif SC', files: {400: 'FreeVideoSerifSC-Regular.woff2', 700: 'FreeVideoSerifSC-Bold.woff2'}},
    // LXGW WenKai has no bold: its Medium is the heavier face, served as weight 700.
    kai: {family: 'FreeVideo Kai SC', files: {400: 'FreeVideoKaiSC-Regular.woff2', 700: 'FreeVideoKaiSC-Medium.woff2'}},
    mono: {family: 'FreeVideo Mono SC', files: {400: 'FreeVideoMonoSC-Regular.woff2', 700: 'FreeVideoMonoSC-Bold.woff2'}},
};
// One face for every weight, so a bold line never gets a synthesized bold box.
const BOX = {family: 'FreeVideo Box', file: 'FreeVideoBox.woff2', weights: '100 900'};
export const FONT_KEYS = Object.keys(FAMILIES);
export const FONT_WEIGHTS = [400, 700];

const key = name => FAMILIES[name] ? name : 'sans';
// The bundled weight nearest to the one asked for.
export const fontWeight = weight => Number(weight) >= 550 ? 700 : 400;
export function fontFamily(name) { return FAMILIES[key(name)].family; }
// The CSS font list for a family: itself, the sans for what it lacks, then the box.
export function fontStack(name) {
    const k = key(name), names = [FAMILIES[k].family];
    if (k !== 'sans') names.push(FAMILIES.sans.family);
    names.push(BOX.family);
    return names.map(family => `"${family}"`).join(',');
}
export const fontCSS = (name, weight, size) => `${fontWeight(weight)} ${size}px ${fontStack(name)}`;

const faces = new Map(), ready = new Set();
function face(family, file, weight, descriptor = String(weight)) {
    const id = `${family}@${weight}`;
    if (!faces.has(id)) {
        const url = new URL(`./fonts/${file}`, import.meta.url).href;
        const font = new FontFace(family, `url("${url}") format("woff2")`, {weight: descriptor, style: 'normal', display: 'block'});
        document.fonts.add(font);
        // A face that failed to load is tried again next time.
        faces.set(id, font.load().then(() => { ready.add(id); }, error => { faces.delete(id); document.fonts.delete(font); throw error; }));
    }
    return faces.get(id);
}
// Load what drawing a family at a weight needs: that face, the sans fallback at the same weight and the box.
export function loadFont(name, weight = 400) {
    const k = key(name), w = fontWeight(weight), loads = [face(FAMILIES[k].family, FAMILIES[k].files[w], w), face(BOX.family, BOX.file, 400, BOX.weights)];
    if (k !== 'sans') loads.push(face(FAMILIES.sans.family, FAMILIES.sans.files[w], w));
    return Promise.all(loads);
}
// Whether a family at a weight is ready to draw now (measuring before that would use other metrics).
export function fontLoaded(name, weight = 400) {
    const k = key(name), w = fontWeight(weight);
    return ready.has(`${FAMILIES[k].family}@${w}`) && ready.has(`${BOX.family}@400`)
        && (k === 'sans' || ready.has(`${FAMILIES.sans.family}@${w}`));
}
export const loadFonts = pairs => Promise.all(pairs.map(([name, weight]) => loadFont(name, weight)));
