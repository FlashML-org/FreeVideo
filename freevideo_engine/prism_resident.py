"""Prism models kept between requests in the ComfyUI-owned resident worker.

The resident worker (resident_worker.py) runs Prism's encode and video stages
in-process. This cache keeps what the request's plan marked as affordable
(``policy['resident']``): the text encoder (on the GPU or in RAM), the Wan VAE,
and the Prism model (roots, and both experts' GPU-resident units when they fit
together). Everything else is released at the end of the stage, so an idle
worker only holds what the next request can use. The cache is dropped when an
H3 request arrives, when the plan or the weights change, after
FREEVIDEO_PRISM_IDLE_SECONDS without a request (default 600), and when the
worker exits (ComfyUI asking for memory stops the whole idle worker).
"""
import gc
import os
import threading
import time


def _tensor_bytes(module, device_type):
    total = 0
    seen = set()
    for tensor in list(module.parameters()) + list(module.buffers()):
        if tensor.device.type != device_type:
            continue
        key = tensor.untyped_storage().data_ptr() if tensor.numel() else id(tensor)
        if key in seen:
            continue
        seen.add(key)
        total += tensor.numel() * tensor.element_size()
    return total


class PrismResident:
    def __init__(self):
        self.lock = threading.RLock()
        self.text = None    # dict(key, tokenizer, encoder, device)
        self.vae = None     # dict(key, module, config)
        self.model = None   # dict(key, model)
        self.used = time.monotonic()
        self.events = []
        self.idle_seconds = float(os.environ.get('FREEVIDEO_PRISM_IDLE_SECONDS', '600'))
        self._watch = None

    # bookkeeping ---------------------------------------------------------
    def note(self, what, **detail):
        self.events.append(dict(what=what, **detail))
        del self.events[:-50]

    def touch(self):
        self.used = time.monotonic()
        if self._watch is None and self.idle_seconds > 0:
            self._watch = threading.Thread(target=self._idle_watch, name='prism-resident-idle', daemon=True)
            self._watch.start()

    def _idle_watch(self):
        while True:
            time.sleep(min(30.0, max(1.0, self.idle_seconds / 4)))
            if time.monotonic() - self.used < self.idle_seconds:
                continue
            # A request holds the lock while it runs, so this never interrupts one.
            if not self.lock.acquire(blocking=False):
                continue
            try:
                if self.held() and time.monotonic() - self.used >= self.idle_seconds:
                    self.clear('idle for %.0f s' % self.idle_seconds)
            finally:
                self.lock.release()

    def held(self):
        return any(x is not None for x in (self.text, self.vae, self.model))

    # text encoder --------------------------------------------------------
    def take_text(self, key):
        """The cached (tokenizer, encoder) for ``key`` or None (and drop a stale one)."""
        if self.text is not None and self.text['key'] == key:
            self.note('text_encoder_reused', device=self.text['device'])
            return self.text['tokenizer'], self.text['encoder'], self.text['device']
        self.drop_text()
        return None

    def keep_text(self, key, tokenizer, encoder, device, keep):
        """After the encode stage: keep the encoder on the GPU, move it to RAM, or drop it."""
        import torch
        if keep not in ('gpu', 'cpu'):
            self.note('text_encoder_released')
            return False
        if keep == 'cpu' and device == 'cuda':
            encoder.to('cpu')
            torch.cuda.empty_cache()
            device = 'cpu'
        self.text = dict(key=key, tokenizer=tokenizer, encoder=encoder, device=device)
        self.note('text_encoder_kept', device=device)
        return True

    def drop_text(self):
        if self.text is not None:
            self.text = None
            self._collect()

    # VAE -----------------------------------------------------------------
    def take_vae(self, key, device):
        import torch
        if self.vae is not None and self.vae['key'] == key:
            module = self.vae['module']
            if next(module.parameters()).device.type != torch.device(device).type:
                module.to(device)
            self.note('vae_reused')
            return module, self.vae['config']
        self.vae = None
        return None

    def keep_vae(self, key, module, config, keep):
        import torch
        if keep not in ('gpu', 'cpu'):
            self.vae = None
            return False
        if keep == 'cpu':
            module.to('cpu')
            torch.cuda.empty_cache()
        self.vae = dict(key=key, module=module, config=config)
        return True

    # Prism model ---------------------------------------------------------
    def take_model(self, key):
        if self.model is not None and self.model['key'] == key:
            self.note('model_reused', experts=sorted(self.model['model'].loaded_experts()))
            return self.model['model']
        self.drop_model()
        return None

    def keep_model(self, key, model, keep_experts):
        """After sampling: keep the model; its expert phases only when both fit."""
        if not keep_experts:
            model.release_phases()
        self.model = dict(key=key, model=model)
        self.note('model_kept', experts=sorted(model.loaded_experts()))

    def drop_model(self):
        if self.model is not None:
            try:
                self.model['model'].close()
            finally:
                self.model = None
                self._collect()

    # all -----------------------------------------------------------------
    def clear(self, reason):
        with self.lock:
            if not self.held():
                return
            self.note('cleared', reason=reason)
            self.drop_model()
            self.text = None
            self.vae = None
            self._collect()

    def _collect(self):
        gc.collect()
        try:
            import torch
            if torch.cuda.is_initialized():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                from .torch_compat import empty_host_cache
                empty_host_cache(torch)
        except Exception:  # noqa: BLE001 - release is best effort; the worker can still exit
            pass

    def ram_bytes(self):
        """Host memory held by cached models (reclaimable by dropping them)."""
        total = 0
        if self.text is not None:
            total += _tensor_bytes(self.text['encoder'], 'cpu')
        if self.vae is not None:
            total += _tensor_bytes(self.vae['module'], 'cpu')
        if self.model is not None:
            total += self.model['model'].host_bytes()
        return total

    def snapshot(self):
        return dict(prism_models=[name for name, value in (('text_encoder', self.text), ('vae', self.vae),
                                                         ('prism', self.model)) if value is not None],
                    prism_ram_bytes=self.ram_bytes(), prism_events=self.events[-10:])


CACHE = PrismResident()
