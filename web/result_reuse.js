// The prompt, size and duration an earlier video was made from, to fill back into the creation panel.
const IDENTITY = /^FreeVideo\/(\d{4}-\d{2}-\d{2}\/[0-9a-f]{32})\/video\.mp4$/;

// Frames a duration in seconds becomes (the model takes 17k + 5).
export function framesOf(seconds) {
    const requested = Math.ceil(Number(seconds) * 24);
    return requested + ((5 - requested) % 17 + 17) % 17;
}

// Null when the video kept no prompt, or is not a saved creation.
export async function savedSettings(api, record) {
    const id = IDENTITY.exec(record?.video || '')?.[1], g = record.geometry;
    if (!id || !(g?.width > 0) || !(g?.height > 0)) return null;
    try {
        const reply = await api.fetchApi('/freevideo/library/prompt?' + new URLSearchParams({id}), {cache: 'no-store'});
        if (!reply.ok) return null;
        const {prompt} = await reply.json();
        if (typeof prompt !== 'string' || !prompt) return null;
        // The panel keeps seconds to three decimals, so the frames it will ask for are those of that rounded duration.
        const raw = Number(g.frames) > 0 ? Number(g.frames) / 24 : Number(g.seconds);
        const seconds = raw > 0 ? Math.floor(raw * 1000) / 1000 : null;
        return {text: prompt, width: Number(g.width), height: Number(g.height), seconds, frames: seconds === null ? null : framesOf(seconds)};
    } catch { return null; }
}

// Whether filling the saved settings would change anything the panel can still change.
export function differs(saved, now) {
    return saved.text !== now.text || saved.width !== now.width || saved.height !== now.height
        || (saved.frames !== null && saved.frames !== now.frames);
}
