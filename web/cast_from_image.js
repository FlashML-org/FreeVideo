// Save as character: a picture of the library or the image view opens the character library's New character dialog with
// that picture already in it, as if it had been downloaded and dropped there. The rest is the dialog's own way (its views
// are made from the picture, then Save), and the @ list has the new character once it is saved.
import { agentInterfaceEnabled } from './agent_connect.js';
import { uploadMedia } from './media_upload.js';

// The entry is offered only where the character library is: the agent interface on.
export const castAvailable = api => agentInterfaceEnabled(api).catch(() => false);

const viewURL = api => (file, type = 'input') => {
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type}));
};

// The picture as a file, named as it is shown; null when it cannot be read.
export async function pictureFile(url, name = 'image.png') {
    try {
        const response = await fetch(url);
        if (!response.ok) return null;
        const blob = await response.blob();
        if (!/^image\//.test(blob.type)) return null;
        return new File([blob], name.split('/').pop() || 'image.png', {type: blob.type});
    } catch { return null; }
}

// Opens the dialog; false when the library is not available, the picture could not be read, or another one is still
// being read (a second click while the first picture loads opens nothing more; once open, the dialog is modal).
let opening = false;
export async function saveAsCharacter({api, text, url, name}) {
    if (opening) return false;
    opening = true;
    try { return await open({api, text, url, name}); }
    finally { opening = false; }
}

async function open({api, text, url, name}) {
    const [file, {createCastLibrary}] = await Promise.all([pictureFile(url, name), import('./cast_library.js')]);
    if (!file) return false;
    const library = createCastLibrary({api, text, viewURL: viewURL(api), uploadOne: picture => uploadMedia(api, picture)});
    if (!await library.ready) { library.destroy(); return false; }
    // The library lives as long as its dialog: closing it, saved or not, leaves nothing behind.
    const opened = library.openNew({file, onClose: () => queueMicrotask(() => library.destroy())});
    if (!opened) library.destroy();
    return Boolean(opened);
}
