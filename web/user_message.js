// One sentence a person can act on for any caught error. Text FreeVideo wrote for people (already in the user's
// language) is kept; what only a browser or a program would say (“Failed to fetch”, “Unexpected token '<'”, an
// error code, “HTTP 500”) is replaced by the cause in plain words and what to do next. The raw text stays in the
// error itself for reports and technical details.
import {browserExtensionAdvice, connectionLostText, detectBrowserExtension, isNetworkTypeError, wrappedFetchOnly} from './browser_extension.js';

// Wording a browser, a JavaScript engine or a server produces, never FreeVideo's own sentences.
const RAW = [
    /^(?:[A-Z][A-Za-z]*)?(?:Error|Exception)\b|\[(?:Errno|WinError) -?\d+\]/,  // TypeError…, OSError, [Errno 28]
    /Failed to fetch|fetch failed|NetworkError|network request failed|Load failed|ERR_[A-Z_]+/i,
    /Unexpected (?:token|end of (?:JSON )?input|non-whitespace)|is not valid JSON|JSON\.parse|JSON at position/i,
    /Cannot read propert|Cannot set propert|is not a function|is not defined|is not iterable|undefined is not|null is not/,
    /^(?:HTTP\s*)?\d{3}(?:\s+[A-Za-z ]+)?$/i,
    /^[a-z][a-z0-9]*(?:[_.:-][a-z0-9]+)+$/,  // a code such as editor_request or generation_failed
    /^\[object \w+\]$|Traceback \(most recent call last\)|\bat (?:\S+ \()?\S*[/.]\S*:\d+:\d+\)?$/m,
];
export const isRawErrorText = text => RAW.some(pattern => pattern.test(String(text).trim()));

const statusOf = error => {
    const status = Number(error?.status ?? error?.response?.status);
    if (Number.isInteger(status) && status >= 100 && status < 600) return status;
    const said = /\bHTTP\s*(\d{3})\b/.exec(typeof error === 'string' ? error : String(error?.message ?? ''));
    return said ? Number(said[1]) : null;
};

export function userMessage(error, t, fallback = t('Did not finish. Try again.', '没有完成，请重试。')) {
    if (error == null || error === '') return fallback;
    const network = isNetworkTypeError(error) || error?.notEngineReply === true;
    // A recent console line from an extension is weighed only when this error is itself a failed request;
    // otherwise only this error's own text and stack can name an extension.
    const extension = detectBrowserExtension(error, network ? {} : {logs: []});
    if (extension && !wrappedFetchOnly(extension)) return browserExtensionAdvice(extension, t).summary;
    if (network) return connectionLostText(t);
    const status = statusOf(error);
    if (error?.name === 'SyntaxError') {
        return status ? t(`FreeVideo sent a reply this page could not read (HTTP ${status}). Try again, and report the problem if it happens again.`,
            `FreeVideo 的回复无法读取（HTTP ${status}）。请重试，再次出现请提交问题。`)
            : t('FreeVideo sent a reply this page could not read. Try again, and report the problem if it happens again.',
                'FreeVideo 的回复无法读取。请重试，再次出现请提交问题。');
    }
    const said = (typeof error === 'string' ? error : typeof error?.message === 'string' ? error.message : '').trim();
    if (said && !isRawErrorText(said)) return said;
    return status ? t(`FreeVideo returned an error (HTTP ${status}). `, `FreeVideo 返回了错误（HTTP ${status}）。`) + fallback : fallback;
}
