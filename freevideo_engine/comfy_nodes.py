"""Optional ComfyUI V3 nodes. Never import these from the standalone CLI."""
import logging
from pathlib import Path

from comfy_api.latest import ComfyExtension, InputImpl, io, ui

from . import comfy_bridge
from .default_prompt import DEFAULT_PROMPT

References = io.Custom('FREEVIDEO_REFERENCES')
LoRAs = io.Custom('FREEVIDEO_LORAS')
Media = io.Custom('FREEVIDEO_MEDIA')
NO_LORA = 'None'

class FreeVideoGenerate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id='FreeVideoGenerate', display_name='FreeVideo · Video + Audio',
            category='FreeVideo', search_aliases=['VDN', 'H3', 'i2v', 'fl2v', 'ref2v'],
            description='Generate a video with audio using your FreeVideo installation. Memory placement, '
                        'caches and validated optimizations are automatic. Outputs and diagnostics are retained.',
            inputs=[
                io.String.Input('text', display_name='Prompt', multiline=True, default=DEFAULT_PROMPT,
                    tooltip='Describe the scene, motion, dialogue, sound and music.'),
                io.Int.Input('width', default=1344, min=256, max=4096, step=32),
                io.Int.Input('height', default=768, min=256, max=4096, step=32),
                io.Float.Input('seconds', display_name='Duration (s)', default=10., min=1.625, max=60., step=.1,
                    tooltip='24 fps. H3 rounds up to its 17n+5 frame grid: 10 s becomes 10.125 s. '
                            'Longer than 15 s is experimental.'),
                io.Int.Input('seed', default=2026090903, min=0, max=2**53-1, control_after_generate=True),
                io.Image.Input('first', display_name='First frame', optional=True,
                    tooltip='One image. Center-cropped to the selected canvas; enables I2VA.'),
                io.Image.Input('last', display_name='Last frame', optional=True,
                    tooltip='One image. With first frame enables FL2VA; alone enables L2VA.'),
                References.Input('references', optional=True,
                    tooltip='Experimental Ref2VA. Use FreeVideo Reference nodes. VDN is not reference-trained.'),
                LoRAs.Input('loras', display_name='LoRAs', optional=True),
                io.Conditioning.Input('conditioning', optional=True,
                    tooltip='Native H3 layer-50 conditioning including keyframe/reference metadata. '
                            'Replaces prompt/media encoding. Generic CLIP conditioning is incompatible.'),
                Media.Input('media', optional=True, tooltip='Unified Media panel: keyframes or ordered references.'),
                io.Boolean.Input('two_pass', display_name='Two-pass acceleration', default=True, optional=True,
                    tooltip='Usually faster: generate at a lower resolution, then upscale and finish sampling at the target size.'),
                io.Int.Input('base_steps', display_name='First-pass steps', default=8, min=1, max=32, optional=True,
                    tooltip='Default: 8. Changing sampling steps may reduce generation quality.'),
                io.Int.Input('refine_steps', display_name='Second-pass steps', default=3, min=1, max=31, optional=True,
                    tooltip='Default: 3. Three steps use the independent refinement schedule. Changing sampling steps may reduce generation quality.'),
                io.Boolean.Input('force_regenerate', display_name='Force regeneration', default=False, optional=True,
                    tooltip='Generate again even when an identical completed video is saved locally.'),
                io.Boolean.Input('preview', display_name='Two-pass: preview first pass', default=False, optional=True,
                    tooltip='Part of two-pass acceleration: pause after the half-resolution first pass and save it as a preview. '
                            'Run the second pass from the result when it looks right; it continues from the same latents.'),
                io.String.Input('refine_from', display_name='Second pass of preview', default='', optional=True, advanced=True,
                    socketless=True, tooltip='Set by the Run second pass button: the preview video to continue. '
                                             "The second pass uses the preview's own prompt, seed, size and inputs."),
            ],
            outputs=[io.Video.Output('video'), io.String.Output('report', display_name='Report JSON')],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_output_node=True, not_idempotent=True,
        )

    @classmethod
    def validate_inputs(cls, text, width, height, seconds, seed, **kwargs):
        try:
            comfy_bridge.validate_request(text, width, height, seconds, seed)
            if not kwargs.get('refine_from'):
                from .two_pass import validate_steps
                validate_steps(kwargs.get('base_steps', 8), kwargs.get('refine_steps', 3), kwargs.get('two_pass', True))
                if kwargs.get('preview') and not kwargs.get('two_pass', True):
                    return 'Preview first needs two-pass acceleration.'
            comfy_bridge.installation()
        except (OSError, ValueError, KeyError) as error:
            return str(error)
        return True

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # Always inspect current input/model content and output integrity in the
        # bridge. Comfy's graph cache cannot detect a deleted or changed MP4.
        return float('nan')

    @classmethod
    def execute(cls, text, width, height, seconds, seed, first=None, last=None,
                references=None, loras=None, conditioning=None, media=None, two_pass=True,
                force_regenerate=False, base_steps=8, refine_steps=3, preview=False, refine_from=''):
        from .two_pass import validate_steps
        if not refine_from:
            validate_steps(base_steps, refine_steps, two_pass)
        from .comfy_media import export
        import folder_paths
        import comfy.model_management as memory
        from comfy.utils import ProgressBar
        from server import PromptServer
        from .comfy_progress import publish
        from .encoder_prewarm import IDLE
        from .resident_process import OWNER
        prewarm = {}
        result_reused = [False]
        node_id = cls.hidden.unique_id
        bar = ProgressBar(base_steps + (refine_steps if two_pass and not preview else 0), node_id=node_id)
        last_message = [None]
        last_count = [None]
        # Without FreeVideo's page scripts the nodes still generate; say why the
        # creative workspace is missing in ComfyUI's own node text.
        from .comfy_launcher_api import page_notice
        notice = page_notice(PromptServer.instance)
        if notice:
            logging.warning('[FreeVideo] %s', notice)
            PromptServer.instance.send_progress_text(notice, node_id)
        notice = notice + '\n' if notice else ''
        def progress(message):
            if message.get('result_cache_hit'):
                result_reused[0] = True
            label = message.get('label') or 'Generating video'
            count = (message.get('done'), message.get('total'))
            if message.get('done') is not None and count != last_count[0]:
                bar.update_absolute(message['done'], message['total'])
                last_count[0] = count
            if message != last_message[0]:
                server = PromptServer.instance
                publish(server, node_id, message)
                if server is not None:
                    detail = message.get('detail')
                    server.send_progress_text(notice + label + (' · ' + detail if detail else ''), node_id)
                last_message[0] = dict(message)
        def release():
            cpu_prewarm = IDLE.stop()
            gpu_prewarm = OWNER.stop_prewarm()
            prewarm.update(gpu_prewarm or cpu_prewarm or {})
            from .comfy_residency import keep_engine_cache
            with keep_engine_cache():
                memory.unload_all_models()
                memory.soft_empty_cache()
        metadata = cls.hidden.extra_pnginfo or {}
        workflow_nodes = (metadata.get('workflow') or {}).get('nodes', [])
        properties = next((row.get('properties', {}) for row in workflow_nodes
                           if str(row.get('id')) == str(node_id)), {})
        versions = properties.get('freevideo_prompt_versions', {})
        rewrite_report = dict(report_id=properties.get('freevideo_prompt_report'),
                              selected=versions.get('selected') if versions.get('enabled') else 'original')
        try:
            from comfy.cli_args import args as comfy_args
            embed = not comfy_args.disable_metadata
        except (ImportError, AttributeError):
            embed = True
        graph = dict(prompt=getattr(cls.hidden, 'prompt', None), workflow=metadata.get('workflow')) if embed else None
        if graph and refine_from:
            # The upscaled video is the full request's result: embed that
            # workflow, not one that points at this machine's preview file.
            graph = comfy_bridge.without_upscale(graph, node_id, refine_from)
        output_root = Path(folder_paths.get_output_directory()).resolve()
        publish(PromptServer.instance, node_id, {'label': 'Preparing video', 'new_request': True})
        try:
            output = comfy_bridge.generate(text, width, height, seconds, seed, output_root,
                metadata=metadata.get('workflow', metadata), progress=progress, two_pass=two_pass,
                prompt_rewrite_report=rewrite_report,
                encoder_prewarm=prewarm, force_regenerate=force_regenerate,
                base_steps=base_steps, refine_steps=refine_steps, comfy_metadata=graph,
                preview=bool(preview), refine_from=refine_from or None,
                interrupted=memory.throw_exception_if_processing_interrupted, release_models=release,
                export_inputs=lambda run, canvas: export(run, canvas, first=first, last=last,
                    references=references, loras=loras, conditioning=conditioning, assets=media))
            relative = output.relative_to(output_root)
            report = output.with_suffix('.request.json').read_text(encoding='utf-8')
            import json
            from .comfy_assets import output_summary, saved_video
            preview = ui.PreviewVideo([saved_video(output, output_root)]).as_dict()
            preview['freevideo_summary'] = [output_summary(json.loads(report), relative)]
            preview['freevideo_summary'][0]['result_cache_hit'] = result_reused[0]
        except BaseException as error:
            cancelled = isinstance(error, KeyboardInterrupt) or type(error).__name__ in ('InterruptProcessingException', 'CancelledError')
            phase = 'cancelled' if cancelled else 'failed'
            publish(PromptServer.instance, node_id, {'label': 'Generation cancelled' if cancelled else 'Generation stopped',
                    'phase': phase, 'overall': {'status': phase}})
            raise
        publish(PromptServer.instance, node_id, {'label': 'Reused previous result' if result_reused[0] else 'Video saved', 'phase': 'complete',
                'overall': {'status': 'complete', 'fraction': 1.}, 'result': preview['freevideo_summary'][0]})
        measured = json.loads(report)
        from .encoder_prewarm import warm_target
        target = warm_target(measured)
        from .resident_process import OWNER
        if target and not result_reused[0]:
            server = PromptServer.instance
            client = server.client_id if server is not None else None
            def busy():
                return server is not None and server.prompt_queue.get_tasks_remaining() > 0
            def warmed(value):
                if server is not None:
                    server.send_sync('freevideo_prewarm', dict(value, node=node_id), sid=client)
            try:
                purpose, path = target
                if purpose == 'upscale':
                    IDLE.schedule(path, output.with_suffix('.prewarm.json'),
                                  ram_budget=measured.get('profile', {}).get('inference_ram_budget_gb', 0) * 1e9,
                                  busy=busy, notify=warmed, purpose='upscale')
                elif OWNER.process is not None:
                    root, machine = comfy_bridge.installation()
                    environment = comfy_bridge.engine_environment(root, comfy_bridge.source_root())
                    environment.update(FREEVIDEO_HOME=str(root), PYTHONPATH=str(comfy_bridge.source_root()))
                    OWNER.warm_encoder(output, machine['python'], environment, busy=busy, notify=warmed)
                else:
                    IDLE.schedule(path, output.with_suffix('.prewarm.json'),
                                  ram_budget=measured.get('profile', {}).get('inference_ram_budget_gb', 0) * 1e9,
                                  busy=busy, notify=warmed)
            except (OSError, RuntimeError):
                pass  # A saved video remains successful if idle warming cannot start.
        return io.NodeOutput(InputImpl.VideoFromFile(str(output)), report, ui=preview)


class FreeVideoMedia(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='FreeVideoMedia', display_name='FreeVideo · Media', category='FreeVideo',
            description='Connect IMAGE or AUDIO outputs from generation, editing or loading nodes, or upload files. '
                        'Choose first/last frames or experimental references. Empty media means text to video.',
            inputs=[io.String.Input('assets', default='[]'),
                    io.Image.Input('first', display_name='First frame', optional=True,
                        tooltip='Connect any compatible IMAGE output. Select one frame from a batch first.'),
                    io.Image.Input('last', display_name='Last frame', optional=True,
                        tooltip='Connect an IMAGE output for the last frame.'),
                    io.Image.Input('reference', display_name='Reference image', optional=True,
                        tooltip='One reference image; appended after uploaded references. Experimental.'),
                    io.Audio.Input('reference_audio', display_name='Reference audio', optional=True,
                        tooltip='Voice, music or rhythm reference, up to 15 seconds. Connect a Load Audio '
                                'or other AUDIO output. Describe its role using <Audio 1> in the prompt. '
                                'Experimental; does not lock the output soundtrack or lip sync.')],
            outputs=[Media.Output('media')])

    @classmethod
    def validate_inputs(cls, assets, **kwargs):
        from .comfy_assets import resolve_assets
        import folder_paths
        try:
            resolve_assets(assets, folder_paths.get_input_directory())
        except (OSError, ValueError) as error:
            return str(error)
        return True

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # Resolve current files again; the engine content-checks reusable inputs.
        return float('nan')

    @classmethod
    def execute(cls, assets, first=None, last=None, reference=None, reference_audio=None):
        from .comfy_assets import resolve_assets, connect_assets
        import folder_paths
        return io.NodeOutput(connect_assets(resolve_assets(assets, folder_paths.get_input_directory()),
                                            first=first, last=last, reference=reference,
                                            reference_audio=reference_audio))


class FreeVideoReference(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='FreeVideoReference', display_name='FreeVideo · Reference (experimental)',
            category='FreeVideo', is_experimental=True,
            description='Append one image, video or audio reference in order. A clip is cut to the generated length, at most 15 s, and the cut is reported. '
                        'Connect the stack to Generate. VDN reference quality is experimental.',
            inputs=[io.Image.Input('image', optional=True), io.Video.Input('video', optional=True),
                    io.Audio.Input('audio', optional=True), References.Input('previous', optional=True)],
            outputs=[References.Output('references')])

    @classmethod
    def execute(cls, image=None, video=None, audio=None, previous=None):
        values = [(kind, value) for kind, value in (('image', image), ('video', video), ('audio', audio)) if value is not None]
        if len(values) != 1:
            raise ValueError('Connect exactly one image, video or audio to each Reference node')
        result = list(previous or [])
        if len(result) >= 32:
            raise ValueError('At most 32 ordered references; memory is checked against the actual request')
        kind, value = values[0]
        return io.NodeOutput(result + [{'kind': kind, 'value': value}])


class FreeVideoLoRA(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        import folder_paths
        names = [name for name in folder_paths.get_filename_list('loras') if name.lower().endswith('.safetensors')]
        return io.Schema(node_id='FreeVideoLoRA', display_name='FreeVideo · LoRA', category='FreeVideo',
            description='FreeVideo needs no acceleration LoRA.',
            inputs=[io.Combo.Input('lora', options=[NO_LORA, *names]),
                    io.Float.Input('strength', default=1., min=-4., max=4., step=.05),
                    LoRAs.Input('previous', optional=True)], outputs=[LoRAs.Output('loras')])

    @classmethod
    def execute(cls, lora, strength, previous=None):
        if lora == NO_LORA:
            return io.NodeOutput(list(previous or []))
        import folder_paths
        path = folder_paths.get_full_path_or_raise('loras', lora)
        return io.NodeOutput(list(previous or []) + [{'path': str(Path(path).resolve()), 'strength': strength}])


class FreeVideoLoRAStack(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='FreeVideoLoRAStack', display_name='FreeVideo · LoRAs', category='FreeVideo',
            description='FreeVideo needs no acceleration LoRA.',
            inputs=[io.String.Input('adapters', default='[]'), LoRAs.Input('previous', optional=True)],
            outputs=[LoRAs.Output('loras')])

    @classmethod
    def execute(cls, adapters, previous=None):
        from .comfy_assets import resolve_loras
        import folder_paths
        return io.NodeOutput(resolve_loras(adapters, folder_paths.get_filename_list('loras'),
            lambda name: folder_paths.get_full_path_or_raise('loras', name), previous))

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float('nan')  # The engine verifies current adapter content on each request.


class FreeVideoFrames(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='FreeVideoFrames', display_name='FreeVideo · First / Last Frame', category='FreeVideo',
            description='Extract just the first and last frames for image processing or the next video segment. '
                        'The full decoded movie is not retained in RAM.',
            inputs=[io.Video.Input('video')], outputs=[io.Image.Output('first'), io.Image.Output('last')])

    @classmethod
    def execute(cls, video):
        import av
        import torch
        import tempfile
        # save_to preserves native trims and crops; a temporary remux avoids
        # ignoring edits when get_stream_source points at the untrimmed file.
        with tempfile.TemporaryDirectory(prefix='FreeVideo frames ') as directory:
            path = Path(directory) / 'frames.mp4'
            video.save_to(str(path))
            first = last = None
            with av.open(str(path)) as container:
                for frame in container.decode(video=0):
                    last = frame.to_ndarray(format='rgb24')
                    if first is None:
                        first = last
            if first is None:
                raise ValueError('The video contains no frames')
            return io.NodeOutput(*(torch.from_numpy(frame.copy()).float().div_(255)[None] for frame in (first, last)))


class FreeVideoExtension(ComfyExtension):
    async def get_node_list(self):
        from .comfy_residency import install
        import comfy.model_management
        install(comfy.model_management)
        return [FreeVideoGenerate, FreeVideoMedia, FreeVideoReference, FreeVideoLoRA, FreeVideoLoRAStack, FreeVideoFrames]
