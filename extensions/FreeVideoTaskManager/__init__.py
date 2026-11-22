"""Independent FreeVideo Task Manager, no generation or model hooks."""
from pathlib import Path
import shutil
import psutil
from aiohttp import web
from server import PromptServer

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

# Prime the non-blocking counter. No sampling thread, no CUDA calls.
psutil.cpu_percent(interval=None)
ENGINE = Path(__file__).resolve().parents[2] / "FreeVideo-engine"

@PromptServer.instance.routes.get("/freevideo-task-manager/host")
async def task_manager_host(request):
    memory = psutil.virtual_memory()
    # Same volume as the engine, not necessarily the system drive.
    disk = shutil.disk_usage(ENGINE if ENGINE.exists() else ENGINE.parent)
    return web.json_response({
        "cpu": psutil.cpu_percent(interval=None),
        "ram_used": memory.total - memory.available,
        "ram_total": memory.total,
        "disk_used": disk.used,
        "disk_total": disk.total,
    }, headers={"Cache-Control": "no-store"})
