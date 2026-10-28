"""Orchestrator for 'Fem Malfoy: The Battle of Hogwarts' Narrative Story.

Renders 10 continuous story clips and stitches them into a full 39-second
narrative short film using 0.3s audio crossfade transitions via FFmpeg.
Features adaptive hardware profiling and real-time ETA countdown timers.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

# Enforce expandable segments to eliminate CUDA fragmentation on Pascal 8GB
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.malfoid_story_specs import STORY_BIBLE, STORY_CLIPS, StoryClipSpec
from scripts.adaptive_profiler import HardwareBenchmark

OUTPUT_ROOT = ROOT / 'examples_generated'
STORY_DIR = OUTPUT_ROOT / '08_malfoid_battle_story'
ARTIFACTS_DIR = ROOT / '_project' / 'artifacts'
REPORT_FILE = ARTIFACTS_DIR / 'generation_runs_report_v1.0.md'
COMFY_URL = 'http://127.0.0.1:8189'
COMFY_OUTPUT_ROOT = Path(r'D:\AI\FreeVideoSA\ComfyUI\output\FreeVideo')
COMFY_INPUT_ROOT = Path(r'D:\AI\FreeVideoSA\ComfyUI\input')
FFMPEG_PATH = Path(r'C:\ProgramData\chocolatey\bin\ffmpeg.exe')

PROFILER = HardwareBenchmark.load()


def check_server() -> bool:
    try:
        with urllib.request.urlopen(f'{COMFY_URL}/system_stats', timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_for_queue_idle(timeout_seconds: int = 7200) -> bool:
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            with urllib.request.urlopen(f'{COMFY_URL}/queue', timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                running = data.get('queue_running', [])
                if not running:
                    return True
                elapsed = int(time.time() - start)
                print(f'Waiting for current job to finish ({elapsed}s elapsed, {len(running)} active)...', end='\r', flush=True)
        except Exception:
            pass
        time.sleep(10)
    return False


def submit_t2va_prompt(prompt_text: str, width: int, height: int, seconds: float, seed: int) -> str:
    w = int(max(256, (width // 32) * 32))
    h = int(max(256, (height // 32) * 32))
    payload = {
        'prompt': {
            '1': {
                'class_type': 'FreeVideoGenerate',
                'inputs': {
                    'text': prompt_text,
                    'width': w,
                    'height': h,
                    'seconds': seconds,
                    'seed': seed,
                    'two_pass': True,
                    'base_steps': 8,
                    'refine_steps': 3,
                    'preview': False,
                    'force_regenerate': True,
                }
            }
        }
    }
    req = urllib.request.Request(
        f'{COMFY_URL}/prompt',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode('utf-8'))
        return res['prompt_id']


def submit_i2va_prompt(prompt_text: str, width: int, height: int, seconds: float, seed: int, frame_filename: str) -> str:
    w = int(max(256, (width // 32) * 32))
    h = int(max(256, (height // 32) * 32))
    payload = {
        'prompt': {
            '1': {
                'class_type': 'LoadImage',
                'inputs': {
                    'image': frame_filename
                }
            },
            '2': {
                'class_type': 'FreeVideoGenerate',
                'inputs': {
                    'text': prompt_text,
                    'width': w,
                    'height': h,
                    'seconds': seconds,
                    'seed': seed,
                    'first': ['1', 0],
                    'two_pass': True,
                    'base_steps': 8,
                    'refine_steps': 3,
                    'preview': False,
                    'force_regenerate': True,
                }
            }
        }
    }
    req = urllib.request.Request(
        f'{COMFY_URL}/prompt',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode('utf-8'))
        return res['prompt_id']


def find_latest_video_after(timestamp_marker: float) -> Optional[Path]:
    if not COMFY_OUTPUT_ROOT.is_dir():
        return None
    matches = []
    for mp4 in COMFY_OUTPUT_ROOT.rglob('video.mp4'):
        try:
            mtime = mp4.stat().st_mtime
            if mtime >= timestamp_marker - 5.0:
                matches.append((mtime, mp4))
        except OSError:
            continue
    if matches:
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[0][1]
    return None


def stitch_full_story(clips: List[Path], output_file: Path) -> bool:
    if not clips:
        return False
    output_file.parent.mkdir(parents=True, exist_ok=True)
    n = len(clips)
    v_inputs = "".join(f"[{i}:v]" for i in range(n))
    video_filter = f"{v_inputs}concat=n={n}:v=1:a=0[v]"
    audio_chains = []
    prev_label = "0:a"
    for i in range(1, n):
        next_label = f"a{i}" if i < n - 1 else "aout"
        audio_chains.append(f"[{prev_label}][{i}:a]acrossfade=d=0.3[{next_label}]")
        prev_label = next_label

    filter_complex = f"{video_filter};" + ";".join(audio_chains)
    cmd = [str(FFMPEG_PATH), '-y']
    for clip in clips:
        cmd.extend(['-i', str(clip)])
    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', '[v]', '-map', '[aout]',
        '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
        '-c:a', 'aac', '-b:a', '192k',
        str(output_file)
    ])
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0 and output_file.is_file() and output_file.stat().st_size > 50000


def log_run_to_artifact(entry: Dict[str, str]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    if not REPORT_FILE.is_file():
        header = (
            "# FreeVideo Multi-Tier Pop-Culture & Montage Generation Report\n\n"
            "| Timestamp | Example ID | Category | Duration | Resolution | Seed | Size | Status |\n"
            "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |\n"
        )
        REPORT_FILE.write_text(header, encoding='utf-8')
    row = (f"| {entry.get('time', '-')} | `{entry.get('id', '-')}` | {entry.get('category', '-')} | "
           f"{entry.get('seconds', '-')}s | {entry.get('resolution', '-')} | `{entry.get('seed', '-')}` | "
           f"{entry.get('size', '-')} | **{entry.get('status', '-')}** |\n")
    with open(REPORT_FILE, 'a', encoding='utf-8') as f:
        f.write(row)


def write_clip_sidecar(txt_path: Path, spec: StoryClipSpec, video_path: Path) -> None:
    size_mb = video_path.stat().st_size / (1024 * 1024)
    content = f"""======================================================================
Fem Malfoy: The Battle of Hogwarts - Scene {spec.scene_index}
======================================================================
Title:          {spec.title}
Scene Index:    {spec.scene_index} of {len(STORY_CLIPS)}
Clip ID:        {spec.clip_id}
File:           {video_path.name} ({size_mb:.2f} MB)
Resolution:     {spec.width} x {spec.height}
Target Length:  {spec.duration}s
Seed:           {spec.seed}
Reference Art:  {spec.reference_image_file or 'None (Original Model Generation)'}
Generated At:   {time.strftime('%Y-%m-%d %H:%M:%S')}

----------------------------------------------------------------------
[1] Narrative Beat
----------------------------------------------------------------------
{spec.description}

----------------------------------------------------------------------
[2] Master Story Bible (Global Setting, Staging & Appearance Anchors)
----------------------------------------------------------------------
{STORY_BIBLE}

----------------------------------------------------------------------
[3] Scene-Specific Action & Multi-Shot Camera Cuts
----------------------------------------------------------------------
{spec.prompt}
======================================================================
"""
    txt_path.write_text(content, encoding='utf-8')


def monitor_and_collect(prompt_id: str, marker_time: float, target_dest: Path, expected_time: float, timeout: int) -> bool:
    start_time = time.time()
    job_done = False
    while time.time() - start_time < timeout:
        time.sleep(10)
        elapsed = int(time.time() - start_time)
        print(PROFILER.format_progress(elapsed, expected_time), end='\r', flush=True)
        try:
            with urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}", timeout=5) as h_resp:
                history = json.loads(h_resp.read().decode('utf-8'))
                if prompt_id in history:
                    job_done = True
                    break
        except Exception:
            pass

    print()
    if not job_done:
        print(f"  [WARNING] Prompt timed out after {elapsed}s.")
        try:
            req = urllib.request.Request(f"{COMFY_URL}/interrupt", data=b'', headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
        return False

    time.sleep(4)
    video = find_latest_video_after(marker_time)
    if video and video.is_file():
        shutil.copyfile(video, target_dest)
        return True
    return False


def run_story_pipeline() -> None:
    print('===================================================')
    print("[FreeVideo] 'Fem Malfoy: Battle of Hogwarts' Story")
    print(f"Hardware Profile: {PROFILER.device_name}")
    print(f"Calibrated Base Rate: {PROFILER.base_sec_per_video_sec:.1f}s / video-sec")
    print('===================================================')

    if not check_server():
        print(f'[ERROR] Could not connect to ComfyUI at {COMFY_URL}.')
        sys.exit(1)

    print('Waiting for queue to become idle...')
    wait_for_queue_idle()
    print('[OK] Queue is idle and ready.\n')

    STORY_DIR.mkdir(parents=True, exist_ok=True)
    completed_clips: List[Path] = []

    for idx, clip in enumerate(STORY_CLIPS, 1):
        clip_mp4 = STORY_DIR / f"{clip.clip_id}.mp4"
        clip_txt = STORY_DIR / f"{clip.clip_id}_prompt.txt"

        print(f"\n>> [Scene {idx}/{len(STORY_CLIPS)}] {clip.title}")

        if clip_mp4.is_file() and clip_mp4.stat().st_size > 10000:
            print(f"  [SKIP] Clip already exists: {clip_mp4.name}")
            write_clip_sidecar(clip_txt, clip, clip_mp4)
            completed_clips.append(clip_mp4)
            continue

        ref_filename = None
        if clip.reference_image_file:
            ref_src = OUTPUT_ROOT / clip.reference_image_file
            ref_filename = f"ref_{clip.reference_image_file}"
            ref_dest = COMFY_INPUT_ROOT / ref_filename
            if ref_src.is_file() and not ref_dest.is_file():
                shutil.copyfile(ref_src, ref_dest)

        has_ref = bool(ref_filename and (COMFY_INPUT_ROOT / ref_filename).is_file())
        expected_sec = PROFILER.estimate_render_time(clip.duration, clip.width, clip.height, has_ref)
        adaptive_timeout = PROFILER.calculate_adaptive_timeout(clip.duration, clip.width, clip.height, has_ref)

        print(f"  Target: {clip.duration}s ({clip.width}x{clip.height}) | Est: ~{int(expected_sec/60)}m | Timeout Headroom: {int(adaptive_timeout/60)}m")

        wait_for_queue_idle()
        marker = time.time()
        full_story_prompt = f"{STORY_BIBLE}\n\n[SCENE {clip.scene_index} ACTION & MULTI-SHOT CUTS]:\n{clip.prompt}"
        try:
            if has_ref:
                print(f"  Grounded with Reference Image: {clip.reference_image_file}...")
                prompt_id = submit_i2va_prompt(full_story_prompt, clip.width, clip.height, clip.duration, clip.seed, ref_filename)
            else:
                prompt_id = submit_t2va_prompt(full_story_prompt, clip.width, clip.height, clip.duration, clip.seed)

            print(f"  Queued prompt: {prompt_id}")
            if monitor_and_collect(prompt_id, marker, clip_mp4, expected_sec, adaptive_timeout):
                actual_elapsed = time.time() - marker
                PROFILER.record_run(clip.duration, clip.width, clip.height, has_ref, actual_elapsed)
                size_mb = f"{clip_mp4.stat().st_size / (1024*1024):.2f} MB"
                print(f"  [SUCCESS] Scene {idx} Created: {clip_mp4.name} ({size_mb} in {int(actual_elapsed/60)}m {int(actual_elapsed%60)}s)")
                write_clip_sidecar(clip_txt, clip, clip_mp4)
                completed_clips.append(clip_mp4)
                log_run_to_artifact({
                    'time': time.strftime('%Y-%m-%d %H:%M'),
                    'id': clip.clip_id,
                    'category': 'Malfoid Battle Story Scene',
                    'seconds': str(clip.duration),
                    'resolution': f"{clip.width}x{clip.height}",
                    'seed': str(clip.seed),
                    'size': size_mb,
                    'status': 'SUCCESS'
                })
        except Exception as ex:
            print(f"  [ERROR] Scene {idx} failed: {ex}")

    # Stitch master narrative film if all clips are generated
    if len(completed_clips) == len(STORY_CLIPS):
        total_sec = round(sum(c.duration for c in STORY_CLIPS), 1)
        master_mp4 = STORY_DIR / f'malfoid_battle_story_master_{int(total_sec)}s.mp4'
        print(f"\n===================================================")
        print(f"Stitching all {len(completed_clips)} scenes into final master movie ({total_sec}s)...")
        print(f"===================================================")
        if stitch_full_story(completed_clips, master_mp4):
            size_mb = f"{master_mp4.stat().st_size / (1024*1024):.2f} MB"
            print(f"[SUCCESS] Final Master Film Created: {master_mp4.name} ({size_mb})")
            log_run_to_artifact({
                'time': time.strftime('%Y-%m-%d %H:%M'),
                'id': f'malfoid_battle_story_master_{int(total_sec)}s',
                'category': 'Full Narrative Master Film',
                'seconds': str(total_sec),
                'resolution': '448x256 (Crossfade Stitched)',
                'seed': '20261001-20261010',
                'size': size_mb,
                'status': 'SUCCESS'
            })
    else:
        print(f"\n[NOTICE] Rendered {len(completed_clips)} of {len(STORY_CLIPS)} scenes.")

    print('\n===================================================')
    print("[FreeVideo] Narrative short film pipeline finished!")
    print(f'Execution Report: {REPORT_FILE}')
    print('===================================================')


if __name__ == '__main__':
    run_story_pipeline()
