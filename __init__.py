"""Optional ComfyUI custom-node entry point; the engine also runs standalone."""
WEB_DIRECTORY = './web'


async def comfy_entrypoint():
    from .freevideo_engine.comfy_progress import register as progress_routes
    progress_routes()
    from .freevideo_engine.comfy_upload import register
    register()
    from .freevideo_engine.comfy_setup import register as setup_routes
    setup_routes()
    from .freevideo_engine.prompt_vlm.service import register as prompt_routes
    prompt_routes()
    from .freevideo_engine.comfy_launcher_api import register as launcher_routes
    launcher_routes()
    from .freevideo_engine.comfy_updates import register as update_routes
    update_routes()
    from .freevideo_engine.comfy_library import register as library_routes
    library_routes()
    from .freevideo_engine.comfy_share import register as share_routes
    share_routes()
    from .freevideo_engine.comfy_metadata import register as workflow_backfill
    workflow_backfill()
    from .freevideo_engine.comfy_nodes import FreeVideoExtension
    return FreeVideoExtension()
