// The picture or video above a version's release notes, as the update metadata names it: release.media =
// {url, poster, sha256}. Pure helpers shared by updates.js and its tests; nothing here touches the page.
const KINDS = {mp4: 'video', webm: 'video', gif: 'animated', webp: 'animated', png: 'image', jpg: 'image', jpeg: 'image'};

// The media fields when they are complete, else null (then nothing is drawn).
export const releaseMedia = release => {
    const media = release?.media;
    return media && typeof media === 'object' && typeof media.url === 'string' && typeof media.poster === 'string'
        && /^[0-9a-f]{64}$/.test(media.sha256 || '') ? media : null;
};

// 'video', 'animated' or 'image', by the file's extension, as the engine decides.
export const mediaKind = media => KINDS[String(media?.url || '').split(/[?#]/)[0].split('.').pop().toLowerCase()] || 'image';

// The engine's verified copy of the cover or the file, for the update ('available') or the version in use ('current').
// The digest in the address changes with the file, so a cached copy of an earlier version is never shown.
export const mediaPath = (which, part, media) =>
    '/freevideo/updates/media?' + new URLSearchParams({release: which, part, v: media.sha256.slice(0, 16)});

// "Topic: what changed" becomes a short label and the sentence; an item without a short topic stays one sentence.
// Standing alone, an English sentence starts with a capital letter ("Tasks: every …" → "Every …"); a first word
// with its own capitals (macOS, iOS) stays as written.
export const splitChange = text => {
    const whole = String(text ?? ''), match = /^([^:：\n]{1,40})(?:：|: )([\s\S]+)$/.exec(whole);
    if (!match) return [null, whole];
    const wide = /[　-鿿＀-￯]/.test(match[1]), sentence = match[2].trim();
    return match[1].trim().length <= (wide ? 16 : 40)
        ? [match[1].trim(), sentence.replace(/^[a-z](?=[a-z]*(?![A-Za-z]))/, letter => letter.toUpperCase())] : [null, whole];
};
