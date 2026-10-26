"""Run the page's timeline rules (web/editor_*.js) on the server in QuickJS.

The Agent edits a project with the same code the editor page runs. The rule
modules are plain ES modules without page globals; this file links them into
one script (QuickJS's Python binding has no module loader) and calls them with
JSON in and JSON out, so results match Node byte for byte.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / 'web'
# Definition order only; evaluation starts at editor_actions like the page's
# import does. editor_model and editor_text_model import each other: the
# module still loading is reached only through a namespace import.
MODULES = ('chain_rules', 'editor_picture_fields', 'editor_transport_model', 'editor_audio_model', 'editor_speed_model',
           'editor_text_model', 'editor_model', 'editor_room', 'editor_actions', 'editor_generation', 'sampling_effort',
           'chain_support', 'editor_store')
TIME_LIMIT = 5.0
MEMORY_LIMIT = 256 * 2**20
STACK_LIMIT = 4 * 2**20

_IMPORT = re.compile(r"^import\s+(\*\s+as\s+([\w$]+)|\{([^}]*)\})\s+from\s+'\./([\w]+)\.js';[ \t]*$", re.M)
_EXPORT = re.compile(r'^export\s+((?:async\s+)?(?:function\*?|const|let|class)\s+([\w$]+))', re.M)
# `export {a, b as c};` — names the module already holds, its own or imported ones.
_EXPORT_LIST = re.compile(r'^export\s+\{([^}]*)\};[ \t]*$', re.M)
_LEFT = re.compile(r'^\s*(?:import|export)\b', re.M)

# structuredClone for the plain JSON-like values the rules copy, and the newer array/string/object
# methods the browser has and this engine lacks (the rules are written for the browser).
_PRELUDE = r'''
(() => {
    const define = (target, name, value) => {
        if (typeof target[name] !== 'function') Object.defineProperty(target, name, {value, writable: true, configurable: true});
    };
    function at(index) {
        const n = Math.trunc(index) || 0, i = n < 0 ? this.length + n : n;
        return i >= 0 && i < this.length ? this[i] : undefined;
    }
    define(Array.prototype, 'at', at);
    define(String.prototype, 'at', at);
    define(Array.prototype, 'findLast', function (test, self) {
        for (let i = this.length - 1; i >= 0; i--) if (test.call(self, this[i], i, this)) return this[i];
        return undefined;
    });
    define(Array.prototype, 'findLastIndex', function (test, self) {
        for (let i = this.length - 1; i >= 0; i--) if (test.call(self, this[i], i, this)) return i;
        return -1;
    });
    define(Array.prototype, 'toSorted', function (compare) { return [...this].sort(compare); });
    define(Array.prototype, 'toReversed', function () { return [...this].reverse(); });
    define(Array.prototype, 'with', function (index, value) {
        const copy = [...this], n = Math.trunc(index) || 0, i = n < 0 ? copy.length + n : n;
        if (i < 0 || i >= copy.length) throw new RangeError('Invalid index');
        copy[i] = value;
        return copy;
    });
    define(Object, 'hasOwn', (value, key) => Object.prototype.hasOwnProperty.call(value, key));
})();
const structuredClone = value => {
    if (value === null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(structuredClone);
    if (value instanceof Map) return new Map([...value].map(([k, v]) => [structuredClone(k), structuredClone(v)]));
    if (value instanceof Set) return new Set([...value].map(structuredClone));
    const copy = {};
    for (const key of Object.keys(value)) copy[key] = structuredClone(value[key]);
    return copy;
};
const __modules = new Map(), __factories = new Map();
const __define = (name, factory) => __factories.set(name, factory);
const __load = name => {
    if (__modules.has(name)) return __modules.get(name);
    const exports = Object.create(null);
    __modules.set(name, exports);
    __factories.get(name)(exports);
    return exports;
};
'''

# editor_store is the page's HTTP module; only its pure capability arithmetic is
# used here, and it is loaded on first use, never at start.
# One call: {fn, record, args, parse} -> {ok, value} | {ok: false, error: {code, message}}.
_ENTRY = r'''
globalThis.__call = text => {
    const request = JSON.parse(text), model = __load('editor_model'), actions = __load('editor_actions');
    const table = request.module === 'model' ? model : actions;
    if (!Object.prototype.hasOwnProperty.call(table, request.fn) || typeof table[request.fn] !== 'function')
        return JSON.stringify({ok: false, error: {code: 'unknown_function', message: String(request.fn)}});
    try {
        const record = request.parse === false ? request.record : model.parse(request.record);
        const value = table[request.fn](record, ...(request.args || []));
        return JSON.stringify({ok: true, value: request.serialize ? model.serialize(value) : value});
    } catch (error) {
        if (error instanceof model.EditError) return JSON.stringify({ok: false, error: {code: error.code, message: error.message}});
        throw error;
    }
};

// The Agent's operation batch: every operation runs on the result of the one
// before; the first rejection stops the batch and nothing is returned to save.
globalThis.__batch = text => {
    const {project, operations} = JSON.parse(text), model = __load('editor_model'), actions = __load('editor_actions');
    const texts = __load('editor_text_model'), by = {by: 'agent'}, results = [];
    const ids = record => new Set(record.tracks.flatMap(track => track.clips.map(clip => clip.id)));
    let record;
    try { record = model.parse(project); }
    catch (error) {
        if (error instanceof model.EditError) return JSON.stringify({ok: false, index: null, error: {code: error.code, message: error.message}});
        throw error;
    }
    for (let index = 0; index < operations.length; index++) {
        const op = operations[index], before = ids(record), result = {};
        try {
            // The page acts on every track at the playhead when nothing is selected;
            // the Agent always names the parts, and only parts of this revision.
            if ('clip_ids' in op && !(Array.isArray(op.clip_ids) && op.clip_ids.length && op.clip_ids.every(id => before.has(id))))
                throw new model.EditError('missing');
            if ('clip_id' in op && !before.has(op.clip_id)) throw new model.EditError('missing');
            // Likewise a cut never falls back to another part under the frame.
            if ((op.op === 'split' || op.op === 'trim') && !(Number.isSafeInteger(op.frame) && actions.linkedIds(record, op.clip_ids)
                .some(id => { const clip = model.find(record, id); return clip.start < op.frame && op.frame < clip.start + clip.frames; })))
                throw new model.EditError('range');
            switch (op.op) {
            case 'insert': record = model.insertSource(record, {...op.media, at: op.at ?? model.duration(record)}); break;
            case 'split': record = actions.splitAt(record, op.clip_ids, op.frame, by); break;
            case 'trim': record = actions.trimAt(record, op.clip_ids, op.frame, op.edge, by); break;
            case 'remove': record = actions.removeParts(record, op.clip_ids); break;
            case 'remove_gaps': {
                const value = actions.removeGaps(record, op.gap ?? null);
                record = value.record; result.removed = value.removed; result.skipped = value.skipped; break;
            }
            case 'duplicate': record = actions.duplicateParts(record, op.clip_ids).record; break;
            case 'copy': record = actions.copyTo(record, op.clip_ids, op.at).record; break;
            case 'move': record = actions.moveParts(record, op.clip_ids, {frames: op.frames, track: null}); break;
            case 'describe': record = model.describe(record, actions.linkedIds(record, op.clip_ids), op.text, by); break;
            case 'set_ref': record = model.setRef(record, actions.linkedIds(record, op.clip_ids), op.ref); break;
            case 'note': record = model.setNote(record, actions.linkedIds(record, op.clip_ids), op.text); break;
            case 'use_version': record = model.restoreVersion(record, op.clip_id, op.index, by); break;
            // One undo on the page; rejects as subtitles / range / text / locked / overlap.
            case 'subtitles': record = texts.addSubtitles(record, op.cues, {replace: op.replace === true}); break;
            default: throw new model.EditError('invalid', 'unknown operation');
            }
        } catch (error) {
            if (!(error instanceof model.EditError)) throw error;
            return JSON.stringify({ok: false, index, error: {code: error.code, message: error.message,
                ...(error.track !== undefined ? {track: error.track} : {})}});
        }
        result.new_clip_ids = [...ids(record)].filter(id => !before.has(id));
        results.push(result);
    }
    return JSON.stringify({ok: true, project: model.serialize(record), results});
};

// Generation, as the page's actions.generate does it (web/editor.js). 'plan'
// marks the parts to make and returns the prepared project and the source keys
// whose files the request continues from; 'request' builds the chain's blocks.
globalThis.__generation = text => {
    const request = JSON.parse(text), model = __load('editor_model');
    const fail = error => {
        if (!(error instanceof model.EditError)) throw error;
        return JSON.stringify({ok: false, error: {code: error.code, message: error.message,
            ...(error.track !== undefined ? {track: error.track} : {})}});
    };
    try {
        const record = model.parse(request.project);
        const generation = __load('editor_generation');
        if (request.mode === 'request') {
            const value = model.generationRequest(record, request.ids, {text: request.text, paths: request.paths});
            // Parts that continue finished footage are made at its shape, as on the page.
            const follow = generation.continuedSize(model.generationBefore(record, request.ids), new Map(Object.entries(request.sizes || {})));
            return JSON.stringify({ok: true, blocks: value.blocks.map(({before, ...block}) => block), size: follow});
        }
        const store = __load('editor_store');
        const size = request.size || generation.generationSize(record), target = request.target, words = request.text;
        const reply = request.capability, ready = reply && (!reply.status || reply.status === 'ready');
        const limits = ready ? store.capabilityPoints(reply, {...record, ...size})
            : {maxSeconds: 15, maxFollowSeconds: 15, maxRequestFrames: null};
        let range, selected = [];
        if (target.clip_ids) {
            const ids = new Set(record.tracks.flatMap(track => track.clips.map(clip => clip.id)));
            if (!target.clip_ids.every(id => ids.has(id))) throw new model.EditError('missing');
            if (target.clip_ids.some(id => __load('editor_text_model').isText(model.find(record, id))))
                throw new model.EditError('unsupported');
            selected = record.tracks[0].clips.filter(clip => target.clip_ids.includes(clip.id) || target.clip_ids.includes(clip.link));
            if (!selected.length) throw new model.EditError('unsupported');
            const start = Math.min(...selected.map(clip => clip.start));
            range = {start, frames: Math.max(...selected.map(clip => clip.start + clip.frames)) - start};
        } else range = {start: target.start, frames: target.frames, ...(target.parts ? {parts: target.parts} : {})};
        // A part length the caller chose stays allowed, as the page keeps manual parts.
        const parts = range.parts || [];
        if (!Array.isArray(parts) || !parts.every(count => Number.isInteger(count) && count > 0)) throw new model.EditError('range');
        const maxSeconds = Math.max(limits.maxSeconds, (parts[0] || 0) / model.FPS);
        const maxFollowSeconds = Math.max(limits.maxFollowSeconds, ...parts.map(count => count / model.FPS));
        const before = record.tracks[0].clips.find(clip => clip.start + clip.frames === range.start && clip.source && clip.extend && clip.in + clip.frames === clip.length);
        if (!words.trim() && !before) throw new model.EditError('text');
        const planned = selected.length && selected.every(clip => !clip.source && clip.pending && !['queued', 'running'].includes(clip.pending));
        let next = planned ? model.startAttempt(record, selected.map(clip => clip.id), words, {by: 'agent'})
            : model.planRange(record, {...range, target: 'both', text: words, by: 'agent', maxSeconds, maxFollowSeconds});
        if (!record.tracks[0].clips.length && words.trim()) next = model.rename(next, model.truncateText(words.trim(), 40));
        const ids = next.tracks[0].clips.filter(clip => clip.start >= range.start && clip.start < range.start + range.frames
            && clip.pending && !['queued', 'running'].includes(clip.pending)).map(clip => clip.id);
        const prepared = model.prepare(next, ids), starts = new Set(ids.map(id => model.find(prepared, id).start));
        const keys = prepared.tracks[0].clips.filter(clip => clip.source && !ids.includes(clip.id) && starts.has(clip.start + clip.frames))
            .map(clip => clip.source.kind + ':' + clip.source.id);
        return JSON.stringify({ok: true, project: model.serialize(prepared), ids, keys: [...new Set(keys)], size,
            max_request_frames: limits.maxRequestFrames});
    } catch (error) { return fail(error); }
};

// A chain's state into the project, as the page's deliverChain does: finished
// blocks fill their parts, failed ones fail them, stopped ones settle. Each
// done block carries the source facts the server read for its file.
globalThis.__deliver = text => {
    const {project, ids, job, chain, sources, assign, settle} = JSON.parse(text), model = __load('editor_model');
    let record = model.parse(project);
    const mismatched = [];
    if (assign) record = model.assignJob(record, ids, job, {prepared: model.parse(assign)});
    if (settle) record = model.settle(record, ids.filter(id => model.attemptClip(record, id)?.job === job), settle);
    for (const block of (chain && chain.blocks) || []) {
        const clip = model.attemptClip(record, block.id);
        if (!clip || clip.job !== job) continue;
        if (block.state === 'done' && block.output) {
            const metadata = sources[block.output];
            const result = {job, source: {kind: 'generated', id: block.output}, frames: metadata?.frames || block.inputs?.frames,
                width: metadata?.width, height: metadata?.height,
                skip_frames: block.skip_frames || 0, skip_audio_samples: block.skip_audio_samples || 0};
            try {
                if (!metadata || metadata.missing) throw new model.EditError('source');
                record = model.fill(record, block.id, result);
            } catch (error) {
                if (!(error instanceof model.EditError)) throw error;
                record = model.fail(record, block.id, 'result_mismatch', job); mismatched.push(block.id);
            }
        } else if (block.state === 'failed') {
            const reason = /^[a-z_]{1,80}$/.test(block.error || '') ? block.error : 'generation_failed';
            record = model.fail(record, block.id, reason, job);
        } else if (['cancelled', 'interrupted'].includes(block.state)
                || (['cancelled', 'interrupted', 'failed'].includes(chain.state) && block.state === 'pending')) {
            record = model.settle(record, [block.id].filter(id => model.attemptClip(record, id)?.job === job), 'stopped');
        } else if (block.state === 'running' && clip.pending !== 'running') record = model.settle(record, [block.id], 'running');
    }
    return JSON.stringify({ok: true, project: model.serialize(record), mismatched});
};
'''


def _module(name, source):
    exports = [(match.group(2), match.group(2)) for match in _EXPORT.finditer(source)]
    for match in _EXPORT_LIST.finditer(source):
        for part in (part.strip() for part in match.group(1).split(',')):
            if part:
                local, _, public = part.partition(' as ')
                exports.append((public.strip() or local.strip(), local.strip()))

    def imported(match):
        if match.group(2):
            return f"const {match.group(2)} = __load('{match.group(4)}');"
        names = [part.strip() for part in match.group(3).split(',') if part.strip()]
        names = [re.sub(r'\s+as\s+', ': ', part) for part in names]
        return f"const {{{', '.join(names)}}} = __load('{match.group(4)}');"

    body = _EXPORT_LIST.sub('', _EXPORT.sub(r'\1', _IMPORT.sub(imported, source)))
    if _LEFT.search(body):
        raise ValueError(f'{name}.js: unsupported import or export form')
    getters = ''.join(f"Object.defineProperty(__exports, '{public}', {{enumerable: true, get: () => {local}}});\n"
                      for public, local in exports)
    return f"__define('{name}', function (__exports) {{\n'use strict';\n{getters}{body}\n}});\n"


def bundle(web=WEB, modules=MODULES, start='editor_actions', entry=None, prelude=''):
    """The linked script: prelude, every rule module, and the JSON entry point."""
    parts = [_PRELUDE, prelude]
    for name in modules:
        parts.append(_module(name, (Path(web) / f'{name}.js').read_text(encoding='utf-8')))
    # Evaluate from the entry, in the order ES module linking would use.
    parts.append(f"__load('{start}');\n")
    parts.append(_ENTRY if entry is None else entry)
    return ''.join(parts)


def _editing_unavailable():
    """The answer when the rules engine is missing. Adding it is not always ours to do (the user's own
    ComfyUI), so this says what does not work here instead of sending the user to repair."""
    from . import errors
    from .jobs import JobError

    class EditingUnavailable(JobError):
        def view(self):
            answer = errors.build(self.code, validation=True, details=[], message=self.message, next_action=(
                'Tell the user that editing videos and images is not available here for now, and go on with what '
                'does not need it. features in the status reply shows these as false.'))
            answer['retryable'] = False
            return answer

    return EditingUnavailable('not_ready', 'Editing videos and images is not available in this installation for now. '
                                           'Generation and everything else work.')


class ScriptRuntime:
    """A QuickJS context with linked page modules. Calls are serialized by a
    lock; a failed or interrupted call discards the context."""

    def __init__(self, source):
        self._source = source
        self._lock = threading.Lock()
        self._context = None

    def _fresh(self):
        try:
            import quickjs
        except ImportError:
            raise _editing_unavailable() from None
        context = quickjs.Context()
        context.set_memory_limit(MEMORY_LIMIT)
        context.set_max_stack_size(STACK_LIMIT)
        context.set_time_limit(-1)
        context.eval(self._source)
        context.set_time_limit(TIME_LIMIT)
        return context

    def _text(self, name, request):
        try:
            request.encode('utf-8')
        except UnicodeEncodeError:
            # QuickJS ends the whole program on half of a character pair, wherever the text came from.
            raise ValueError('The text holds half of a character pair') from None
        with self._lock:
            if self._context is None:
                self._context = self._fresh()
            try:
                return self._context.get(name)(request)
            except Exception:
                self._context = None
                raise

    def _entry(self, name, value):
        return json.loads(self._text(name, json.dumps(value, ensure_ascii=False)))


class EditorRuntime(ScriptRuntime):
    """The timeline rules (web/editor_*.js)."""

    def __init__(self, web=WEB):
        super().__init__(bundle(web))

    def call(self, fn, record, args=(), *, module='actions', parse=True, serialize=False):
        request = json.dumps(dict(module=module, fn=fn, record=record, args=list(args), parse=parse,
                                  serialize=serialize), ensure_ascii=False)
        text = self._text('__call', request)
        return json.loads(text), text

    def batch(self, project, operations):
        """Apply operations to a stored project; returns the parsed batch reply."""
        return self._entry('__batch', dict(project=project, operations=operations))

    def generation(self, **request):
        """mode='plan' (project, target, text, capability[, size]) or mode='request' (project, ids, text, paths, sizes)."""
        return self._entry('__generation', request)

    def deliver(self, **request):
        """Apply a chain's state to the parts of one generation (project, ids, job, chain, sources)."""
        return self._entry('__deliver', dict(dict(chain=None, sources={}, assign=None, settle=None), **request))


_RUNTIME = None
_RUNTIME_LOCK = threading.Lock()


def available():
    """Whether this process can run the editor rules (project and image edits)."""
    try:
        import quickjs  # noqa: F401
    except Exception:
        # Not installed (an update could not add it), or installed and its library does not load here.
        return False
    return True


def runtime():
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = EditorRuntime()
        return _RUNTIME
