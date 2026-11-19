// From a photo: where the face is small, the face close-up is made from the face cut out and enlarged instead of the
// whole photo. Measured with Qwen-Image 2.1 on six fictional people, three seeds each: a face under 256 px on its long
// side keeps the likeness much better this way (225 px photos: average similarity 0.758 → 0.835, worst 0.275 → 0.581);
// a larger face keeps it a little better from the whole photo, so it stays as it is. The face size that counts is the
// smaller of the photo's own and the one the engine sees once it brings a reference to about 1 MP.
// The engine finds the faces (face-box, on the CPU, the upload stays); the page cuts and enlarges with a canvas. Any
// failure (an engine without face-box, no face, a picture that cannot be read) keeps the whole photo.
import {IMAGE_GEN_PROTOCOL} from './image_generation.js';

// below: the face size (px, long side) under which the face is cut out; engineArea: the area the engine brings a
// reference to; side: the cut-out's side in face-box heights (hair and chin inside); raise: how far its centre sits
// above the box's, in box heights; size: the side the cut-out is enlarged to.
export const FACE_CROP = Object.freeze({below: 256, engineArea: 1024 * 1024, side: 2, raise: .1, size: 1024, waitMs: 20000});

// The square to cut out of a width × height photo, in its pixels, or null to keep the whole photo. faces: the engine's
// list ({box: [left, top, right, bottom]}); the largest box is the person's.
export function faceCrop(width, height, faces) {
    if (!(width > 0 && height > 0) || !Array.isArray(faces)) return null;
    const area = face => (face.box[2] - face.box[0]) * (face.box[3] - face.box[1]);
    const usable = faces.filter(face => Array.isArray(face?.box) && face.box.length === 4 && face.box.every(Number.isFinite)
        && face.box[2] > face.box[0] && face.box[3] > face.box[1]);
    if (!usable.length) return null;
    const [x0, y0, x1, y1] = usable.reduce((best, face) => area(face) > area(best) ? face : best).box;
    const boxHeight = y1 - y0, long = Math.max(x1 - x0, boxHeight);
    const seen = long * Math.sqrt(FACE_CROP.engineArea / (width * height));
    if (Math.min(long, seen) >= FACE_CROP.below) return null;
    const side = Math.min(FACE_CROP.side * boxHeight, width, height);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2 - FACE_CROP.raise * boxHeight;
    const left = Math.round(Math.min(Math.max(0, cx - side / 2), width - side));
    const top = Math.round(Math.min(Math.max(0, cy - side / 2), height - side));
    return {left, top, side: Math.max(1, Math.min(Math.round(side), width - left, height - top)), face: Math.round(Math.min(long, seen))};
}

// The faces the engine finds in an uploaded picture, or null where it cannot tell (an engine without face-box included).
export async function findFaces(client, fetcher, name) {
    const p = IMAGE_GEN_PROTOCOL, info = await client.info();
    if (info?.face_box !== true || !info[p.token]) return null;
    const stop = new AbortController(), timer = setTimeout(() => stop.abort(), FACE_CROP.waitMs);
    try {
        const response = await fetcher(`${p.root}/face-box`, {method: 'POST', signal: stop.signal,
            headers: {'Content-Type': 'application/json', [p.header]: info[p.token]}, body: JSON.stringify({image: name})});
        if (!response.ok) return null;
        const value = await response.json();
        return Number.isFinite(value?.width) && Number.isFinite(value?.height) && Array.isArray(value?.faces) ? value : null;
    } finally { clearTimeout(timer); }
}

// The face cut out of `file` (the photo uploaded as `name`) and enlarged, as a PNG file, or null to keep the whole photo.
export async function faceCloseUp({client, fetcher, name, file}) {
    try {
        const found = await findFaces(client, fetcher, name);
        if (!found) return null;
        const bitmap = await createImageBitmap(file);
        try {
            // The engine reads the photo as it is shown (its orientation applied), as the browser draws it.
            if (bitmap.width !== found.width || bitmap.height !== found.height) return null;
            const crop = faceCrop(found.width, found.height, found.faces);
            if (!crop) return null;
            const size = FACE_CROP.size, canvas = new OffscreenCanvas(size, size), g = canvas.getContext('2d');
            g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
            g.drawImage(bitmap, crop.left, crop.top, crop.side, crop.side, 0, 0, size, size);
            const blob = await canvas.convertToBlob({type: 'image/png'});
            return {file: new File([blob], 'face.png', {type: 'image/png'}), crop};
        } finally { bitmap.close?.(); }
    } catch { return null; }
}

// New clothes on a finished face close-up: the part to repaint (white = change), or null to leave the close-up as it is.
// Everything from the level of the mouth down except the face: the face is an ellipse in its box, kept with a margin
// wider than the engine's grown, feathered edge (24 + 16 px), so the face, the jaw and the chin keep their own pixels
// while the neck, the collar and a hood or high collar beside the jaw are repainted. top: where the part starts, in
// face-box heights below the box's top; margin: px kept around the face ellipse.
export const DRESS_MASK = Object.freeze({top: .6, margin: 44});
export function dressMask(width, height, faces) {
    if (!(width > 0 && height > 0) || !Array.isArray(faces)) return null;
    const usable = faces.filter(face => Array.isArray(face?.box) && face.box.length === 4 && face.box.every(Number.isFinite)
        && face.box[2] > face.box[0] && face.box[3] > face.box[1]);
    if (!usable.length) return null;
    const area = face => (face.box[2] - face.box[0]) * (face.box[3] - face.box[1]);
    const [x0, y0, x1, y1] = usable.reduce((best, face) => area(face) > area(best) ? face : best).box;
    const keep = {cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, rx: (x1 - x0) / 2 + DRESS_MASK.margin, ry: (y1 - y0) / 2 + DRESS_MASK.margin};
    const top = Math.round(y0 + DRESS_MASK.top * (y1 - y0));
    // Nothing to repaint when the kept face reaches the bottom.
    if (keep.cy + keep.ry > height - 32) return null;
    return {top, keep: Object.fromEntries(Object.entries(keep).map(([k, v]) => [k, Math.round(v)]))};
}

// Whether a repaint changed the clothes: the share of the mask's pixels that changed by more than `change` grey levels, on
// copies a quarter the size. Measured on 9 repaints: the 7 that put on the new clothes changed 0.46-0.67 of them, the 2 that
// kept the old ones (a green hoodie to be white) 0.02 and 0.05.
export const DRESS_TOOK = Object.freeze({change: 40, share: .15, scale: .25});
export async function repaintTook({before, after, mask}) {
    try {
        const bitmaps = await Promise.all([before, after].map(async url => createImageBitmap(await (await fetch(url)).blob())).concat([createImageBitmap(mask)]));
        try {
            const width = Math.max(1, Math.round(bitmaps[0].width * DRESS_TOOK.scale)), height = Math.max(1, Math.round(bitmaps[0].height * DRESS_TOOK.scale));
            const [a, b, m] = bitmaps.map(bitmap => { const g = new OffscreenCanvas(width, height).getContext('2d'); g.drawImage(bitmap, 0, 0, width, height); return g.getImageData(0, 0, width, height).data; });
            let inside = 0, changed = 0;
            for (let i = 0; i < m.length; i += 4) {
                if (m[i] < 128) continue;
                inside++;
                if (Math.max(Math.abs(a[i] - b[i]), Math.abs(a[i + 1] - b[i + 1]), Math.abs(a[i + 2] - b[i + 2])) > DRESS_TOOK.change) changed++;
            }
            return !inside || changed / inside >= DRESS_TOOK.share;
        } finally { for (const bitmap of bitmaps) bitmap.close?.(); }
    } catch { return true; }
}

// Where the face cannot be found (an engine without face-box, an illustration it cannot read): a fixed part of a close-up. Measured
// on 40 close-ups of 4 described people (6-20 seeds each): the face box ends at 42-62% of the picture's height and spans 30-70% of
// its width, so everything from 68% down is clothes and shoulders, and an ellipse over the middle keeps the chin.
export const DRESS_FIXED = Object.freeze({top: .68, keep: Object.freeze({cx: .5, cy: .4, rx: .27, ry: .3})});
export function fixedDressMask(width, height) {
    if (!(width > 0 && height > 0)) return null;
    const {cx, cy, rx, ry} = DRESS_FIXED.keep;
    return {top: Math.round(DRESS_FIXED.top * height), keep: {cx: Math.round(cx * width), cy: Math.round(cy * height), rx: Math.round(rx * width), ry: Math.round(ry * height)}};
}

// The mask for dressMask's part as a PNG file of the close-up's size (white = change), or null. Where no face is found, the
// fixed part is used from the picture's own size (`url`) instead of leaving the old clothes on.
export async function dressMaskFile({client, fetcher, name, url}) {
    try {
        const found = await findFaces(client, fetcher, name).catch(() => null);
        let width = found?.width, height = found?.height, part = null;
        if (found) part = dressMask(found.width, found.height, found.faces);
        if (!part && !found?.faces?.length && url) {
            const bitmap = await createImageBitmap(await (await fetcher(url)).blob());
            width = bitmap.width; height = bitmap.height; bitmap.close?.();
            part = fixedDressMask(width, height);
        }
        if (!part) return null;
        const canvas = new OffscreenCanvas(width, height), g = canvas.getContext('2d'), {cx, cy, rx, ry} = part.keep;
        g.fillStyle = '#000'; g.fillRect(0, 0, width, height);
        g.fillStyle = '#fff'; g.fillRect(0, part.top, width, height - part.top);
        g.fillStyle = '#000'; g.beginPath(); g.ellipse(cx, cy, rx, ry, 0, 0, 2 * Math.PI); g.fill();
        return new File([await canvas.convertToBlob({type: 'image/png'})], 'mask.png', {type: 'image/png'});
    } catch { return null; }
}

// A face toward the camera in face-box's answer (a back view that came out facing the camera): found surely, both eyes well
// apart for the box, and the nose between them. Measured on 76 back views made from photos and descriptions: the 5 that face
// the camera pass (score 1.00, eyes 0.45-0.48 of the box width apart, the nose 0.04-0.07 eye distances off their middle)
// and none of the 20 other finds do (the back of the head, curls, a print or a head turned to the side: score 0.60-0.98,
// eyes 0.02-0.34 of the box apart, or the nose 0.15 or more off).
export const FRONTAL = Object.freeze({score: .9, eyes: .35, nose: .3});
export function frontalFace(faces) {
    return Array.isArray(faces) && faces.some(face => {
        const box = face?.box, marks = face?.landmarks;
        if (!(face?.score >= FRONTAL.score) || !Array.isArray(box) || box.length !== 4 || !Array.isArray(marks) || marks.length < 3
            || !marks.slice(0, 3).every(point => Array.isArray(point) && point.length >= 2 && point.every(Number.isFinite))) return false;
        const [[ax, ay], [bx, by], [nx]] = marks, width = box[2] - box[0], apart = Math.hypot(bx - ax, by - ay);
        return width > 0 && apart >= FRONTAL.eyes * width && Math.abs(nx - (ax + bx) / 2) <= FRONTAL.nose * apart;
    });
}

// Whether the made picture `name` shows a face toward the camera; false where the engine cannot tell (no face-box).
export async function facesCamera({client, fetcher, name}) {
    try { const found = await findFaces(client, fetcher, name); return !!found && frontalFace(found.faces); } catch { return false; }
}

// How much of a made close-up the face takes: the largest face box's height over the picture's height, or null where the
// engine cannot tell (no face-box, no face). Measured on 82 close-ups of 5 described people, 6-20 seeds each, and 10 more
// from photos and pictures: 0.26-0.45 (median 0.32, 4% at 0.40 or more); the one cropped at the waist 0.16. The page draws a close-up again under 0.36 (a face about 40% of the height with the margin of the engine's box), and the larger face stays.
export const CLOSEUP_FRAME = Object.freeze({min: .36});
export function faceShare(height, faces) {
    if (!(height > 0) || !Array.isArray(faces)) return null;
    const heights = faces.filter(face => Array.isArray(face?.box) && face.box.length === 4 && face.box.every(Number.isFinite)).map(face => face.box[3] - face.box[1]);
    return heights.length ? Math.max(...heights) / height : null;
}
export async function closeUpShare({client, fetcher, name}) {
    try { const found = await findFaces(client, fetcher, name); return found ? faceShare(found.height, found.faces) : null; } catch { return null; }
}

// The close-up with everything below the chin painted flat grey, as a PNG file, or null: a repaint of the same garment in
// another colour (a green hoodie to be white) takes the new colour from this base, where it keeps the old one from the
// close-up itself. Measured on a green hoodie to be white, seeds 32-36: 4 of 5 white, against 0 of 6 from the close-up.
export const NEUTRAL_BASE = Object.freeze({grey: 200, chin: .05});
export async function neutralCloseUp({client, fetcher, name, url}) {
    try {
        const found = await findFaces(client, fetcher, name);
        if (!found?.faces?.length) return null;
        const bitmap = await createImageBitmap(await (await fetcher(url)).blob());
        try {
            if (bitmap.width !== found.width || bitmap.height !== found.height) return null;
            const usable = found.faces.filter(face => Array.isArray(face?.box) && face.box.length === 4 && face.box.every(Number.isFinite));
            if (!usable.length) return null;
            const area = face => (face.box[2] - face.box[0]) * (face.box[3] - face.box[1]);
            const [, y0, , y1] = usable.reduce((best, face) => area(face) > area(best) ? face : best).box;
            const top = Math.round(y1 - NEUTRAL_BASE.chin * (y1 - y0));
            if (top >= found.height) return null;
            const canvas = new OffscreenCanvas(found.width, found.height), g = canvas.getContext('2d');
            g.drawImage(bitmap, 0, 0);
            g.fillStyle = `rgb(${NEUTRAL_BASE.grey},${NEUTRAL_BASE.grey},${NEUTRAL_BASE.grey})`; g.fillRect(0, top, found.width, found.height - top);
            return new File([await canvas.convertToBlob({type: 'image/png'})], 'neutral.png', {type: 'image/png'});
        } finally { bitmap.close?.(); }
    } catch { return null; }
}
