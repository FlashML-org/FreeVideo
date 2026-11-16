// Whether this page's engine has the privacy calls (/freevideo/private/*, the image service's bin). Asked once, from
// what the image service already says about itself (history_trash), so an older engine is never sent a request it does
// not know. No imports: the page's modules and their Node tests can both use it.
let offered = null;
export function privacyOffered(fetchImpl = (...args) => globalThis.fetch(...args)) {
    return offered ||= Promise.resolve().then(() => fetchImpl('/freevideo/image-gen', {cache: 'no-store'}))
        .then(response => response?.ok ? response.json() : null)
        .then(value => value?.history_trash === true)
        .catch(() => false);
}
// For tests: forget the answer.
export function resetPrivacyOffered() { offered = null; }
