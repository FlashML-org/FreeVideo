// The address of a picture in ComfyUI's input folder (the image view's works and the creation panel's queue use the same one).
import { api } from '../../scripts/api.js';

export const pictureURL = file => { const at = file.lastIndexOf('/'); return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(at + 1), subfolder: at < 0 ? '' : file.slice(0, at), type: 'input'})); };
