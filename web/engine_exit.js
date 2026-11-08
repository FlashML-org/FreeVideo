// Why the ComfyUI before this one stopped, as far as the launcher saw it end. The page keeps the record it
// found when it loaded, so only an exit that happened afterwards is announced; a feedback message carries
// the newest record it knows. Only numbers and fixed words arrive from the engine, never log text.
let baselineAt, latest = null, watching = null;

const clock = at => {
    const time = new Date(at * 1000);
    return `${String(time.getHours()).padStart(2, '0')}:${String(time.getMinutes()).padStart(2, '0')}`;
};

async function load(api) {
    try {
        const response = await api.fetchApi('/freevideo/launcher/last-exit');
        if (!response.ok) return undefined;
        const value = (await response.json())?.exit;
        return value === null ? null : (value && Number.isFinite(value.at) ? value : undefined);
    } catch { return undefined; }
}

/** Remember the record that exists now, once per page. */
export function watchEngineExit(api) {
    watching ??= load(api).then(value => { if (value !== undefined) { baselineAt = value?.at ?? 0; if (value) latest = value; } });
    return watching;
}

/** The record of an exit after this page loaded, or null. */
export async function newEngineExit(api) {
    await watchEngineExit(api);
    const value = await load(api);
    if (value === undefined) return null;
    // The first read failed, so this one is the page's baseline: nothing is announced for it.
    if (baselineAt === undefined) { baselineAt = value?.at ?? 0; if (value) latest = value; return null; }
    if (!value) return null;
    latest = value;
    if (value.at === baselineAt) return null;
    // Announced once: the next recovery says nothing unless the engine stops again.
    baselineAt = value.at;
    return value;
}

/** The newest record this page has seen, for a feedback message; null when there is none. */
export function knownEngineExit() {
    return latest;
}

/** One sentence for the status line after the engine started again. The exit status stays in the report. */
export function engineExitText(record, t = en => en) {
    return t(`The engine stopped unexpectedly at ${clock(record.at)} and has been restarted.`,
        `引擎于 ${clock(record.at)} 意外停止，已重新启动。`);
}
