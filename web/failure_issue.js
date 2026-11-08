// What a failure card sends to GitHub: the redacted report's summary, never the report file,
// paths or typed text (the report was already cleaned). feedback.js turns it into the address.
const PHASES = [
    [/download|dependenc/i, ['downloading files', '下载文件']],
    [/load/i, ['loading the model', '载入模型']],
    [/prompt|text|encod|reference/i, ['encoding', '编码']],
    [/sampl|denois|first|second|refine|pass|step/i, ['sampling', '采样']],
    [/decod|vae/i, ['decoding', '解码']],
    [/save|mux|output|write/i, ['saving the video', '保存视频']],
];

/** The stage a generation stopped in, as [English, Chinese] words, or null when the report does not say. */
export function failureStage(report) {
    if (report?.stage === 'submission') return ['submitting the task', '提交任务'];
    if (report?.stage === 'queue') return ['queue operation', '队列操作'];
    // A resource-planning snapshot label says where the resources were last read, not where the run stopped.
    const named = [report?.progress?.phase, report?.progress?.stage,
        /^Stage: (\S+)/m.exec(String(report?.details || ''))?.[1]]
        .filter(name => name && !/^(?:before|after)-encoding$|^retry-\d+$/.test(name));
    for (const name of named)
        for (const [pattern, words] of PHASES) if (pattern.test(name)) return words;
    return null;
}

const TAIL = 1500, TAIL_HEAD = 500;
// The engine's one-line summary of a Mac run (machine, size, length, settings, retry). It travels as its
// own field, so a long traceback cannot cut it and it does not take room from the tail.
const RUN_LINE = /^Run: .*(?:\r?\n|$)/m;

// The engine's failure text, not ComfyUI's traceback after it: details end with
// the bridge's own frames. The error and stage lead that text and the child
// log's error ends it, so a long one gives up its middle.
function failureText(report) {
    const message = String(report?.message || '');
    return message.includes('\n') ? message : String(report?.details || '');
}

function cause(report) {
    const text = failureText(report).replace(RUN_LINE, '');
    if (text.length <= TAIL) return text;
    return text.slice(0, TAIL_HEAD) + '\n...\n' + text.slice(-(TAIL - TAIL_HEAD - 5));
}

/** Title, text and summary for the prefilled issue. `lora` marks a LoRA the check judged wrongly; `engineExit` is the
 *  record of the engine's last unexpected stop, when the launcher saw one. */
export function failureIssue(report, kind, {lora = false, t = en => en, engineExit = null} = {}) {
    const stage = failureStage(report);
    const lines = String(report?.message || '').split(/\r?\n/).filter(line => line.trim());
    // A traceback ends with the line that names the error; any other message starts with it.
    const first = (/^Traceback/.test(lines[0] || '') ? lines[lines.length - 1] : lines[0]) || '';
    const text = [lora ? 'FreeVideo refused this LoRA. The verdict is reported as a misjudgment.' : 'FreeVideo reported a generation failure.',
        `Stage: ${stage ? stage[0] : report?.stage || 'unknown'}`, `Error: ${first.trim().slice(0, 300)}`,
        `Kind: ${kind || 'unknown'}`].join('\n');
    const types = (report?.nodes || []).map(node => node.type).filter(Boolean);
    const run = RUN_LINE.exec(failureText(report))?.[0].trim().slice(5, 505);
    const summary = JSON.stringify({kind: kind || 'unknown', stage: report?.stage, exception_type: report?.exception_type,
        known_error: report?.known_error, nodes: types, settings: report?.settings, progress: report?.progress,
        // Before the long tail: an over-long summary is cut from its end.
        engine_exit: engineExit || undefined, run, tail: cause(report)});
    const title = lora ? t('[Feedback] LoRA compatibility misjudged', '[Feedback] LoRA 兼容判断有误')
        : t('[Feedback] Generation did not finish', '[Feedback] 生成未完成') + (stage ? t(`: ${stage[0]}`, `：${stage[1]}`) : '');
    return {title, text, summary};
}
