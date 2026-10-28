// The server owns segmentation. These helpers only describe requests and replies.
export const BLOCK_SECONDS_FIELD = 'block_seconds';
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;

export const singleLimitSeconds = maximum => positive(maximum) ? Math.min(30, maximum) : 15;
export const defaultBlockSeconds = maximum => positive(maximum) ? Math.min(15, maximum) : 15;

export function buildPlanRequest({seconds, single = false, singleLimit = null, maxFrames = null, blockSeconds = null}) {
    const body = {seconds, single: Boolean(single)};
    if (positive(singleLimit)) body.single_limit_seconds = singleLimit;
    if (Number.isSafeInteger(maxFrames) && maxFrames >= 0) body.max_frames = maxFrames;
    if (positive(blockSeconds)) body[BLOCK_SECONDS_FIELD] = blockSeconds;
    return body;
}

export function buildChainRequest(options, {node, prompt, extra_data, client_id, text, seed}) {
    const body = options.blocks ? {node: String(node), prompt, blocks: options.blocks}
        : {...buildPlanRequest(options), node: String(node), prompt};
    for (const [key, value] of Object.entries({extra_data, client_id, text, seed})) {
        if (value !== undefined) body[key] = value;
    }
    if (options.after) body.after = options.after;
    if (options.review !== undefined) body.review = Boolean(options.review);
    if (options.join !== undefined) body.join = Boolean(options.join);
    return body;
}

export function readLengthPlan(reply) {
    const plan = reply?.plan;
    if (!plan || !Array.isArray(plan.blocks) || !plan.blocks.length
        || !plan.blocks.every(block => positive(block.new_frames)) || !positive(plan.seconds)) return null;
    return {single: plan.single === true, forced: typeof plan.forced === 'boolean' ? plan.forced : null, count: plan.blocks.length,
        partSeconds: plan.blocks.map(block => block.new_frames / 24), seconds: plan.seconds, blocks: plan.blocks};
}

export function classifyPlanError(reply) {
    if (reply?.code === 'device_limit') return 'continuation_unavailable';
    if (reply?.code === 'too_long') return 'too_long';
    if (['block_too_long', 'block_too_short', 'too_short', 'invalid'].includes(reply?.code)) return 'other';
    const value = reply?.error;
    const message = reply?.code || (typeof value === 'string' ? value : value?.code || value?.message || '');
    if (/single.*(?:limit|long|cannot|unavailable|impossible)|force.*single|强制单次/i.test(message)) return 'single_unavailable';
    if (/minimum|shortest|continuation|最短|接续/i.test(message)) return 'continuation_unavailable';
    if (/too[_ -]?long|too[_ -]?many|480|太长/i.test(message)) return 'too_long';
    return 'other';
}
