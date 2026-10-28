"""Extended Multi-Tier Batch Generation Orchestrator for FreeVideo.
Executes reference rendering across pop-culture scenarios and the 5-clip montage suite.
Includes autonomous queue polling, adaptive hardware profiling, and FFmpeg audio crossfade.
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

# Enforce expandable segments to eliminate CUDA unallocated reserved fragmentation
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.multitier_specs import SPECS, MONTAGE_CLIPS, MultiTierSpec, MontageClipSpec
from scripts.adaptive_profiler import HardwareBenchmark

OUTPUT_DIR = ROOT / 'examples_generated'
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
                    'preview': True,
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
                    'preview': True,
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


def extract_last_frame(video_path: Path, output_image_path: Path) -> bool:
    output_image_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(FFMPEG_PATH), '-y',
        '-sseof', '-0.1',
        '-i', str(video_path),
        '-vframes', '1',
        '-update', '1',
        str(output_image_path)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0 and output_image_path.is_file() and output_image_path.stat().st_size > 1000


def concatenate_videos(part1: Path, part2: Path, output_file: Path) -> bool:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(FFMPEG_PATH), '-y',
        '-i', str(part1),
        '-i', str(part2),
        '-filter_complex', '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]',
        '-map', '[v]', '-map', '[a]',
        '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
        '-c:a', 'aac', '-b:a', '192k',
        str(output_file)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0 and output_file.is_file() and output_file.stat().st_size > 10000


def stitch_montage_with_audio_crossfade(clips: List[Path], output_file: Path) -> bool:
    if len(clips) != 5:
        return False
    output_file.parent.mkdir(parents=True, exist_ok=True)
    # 5 clips: video concat, audio acrossfade (0.3s smooth crossfade)
    filter_complex = (
        "[0:v][1:v][2:v][3:v][4:v]concat=n=5:v=1:a=0[v];"
        "[0:a][1:a]acrossfade=d=0.3[a01];"
        "[a01][2:a]acrossfade=d=0.3[a02];"
        "[a02][3:a]acrossfade=d=0.3[a03];"
        "[a03][4:a]acrossfade=d=0.3[aout]"
    )
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


def write_spec_sidecar(txt_path: Path, title: str, category: str, identifier: str, video_path: Path, prompt: str, settings: Dict[str, str]) -> None:
    size_mb = video_path.stat().st_size / (1024 * 1024)
    content = f"""======================================================================
FreeVideo & MiniMax H3 Showcase Reference
======================================================================
Title:          {title}
Category:       {category}
Identifier:     {identifier}
File:           {video_path.name} ({size_mb:.2f} MB)
Generated At:   {time.strftime('%Y-%m-%d %H:%M:%S')}

----------------------------------------------------------------------
[1] Generation Settings & Architecture
----------------------------------------------------------------------
"""
    for k, v in settings.items():
        content += f"- {k:<22}: {v}\n"
    content += f"""
----------------------------------------------------------------------
[2] Multimodal Prompt (Visual, Soundscape & Music)
----------------------------------------------------------------------
{prompt}
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


def run_pipeline() -> None:
    print('===================================================')
    print('[FreeVideo] Pop-Culture & Montage Suite')
    print('===================================================')

    if not check_server():
        print(f'[ERROR] Could not connect to ComfyUI at {COMFY_URL}.')
        sys.exit(1)

    print('Waiting for queue to become idle...')
    wait_for_queue_idle()
    print('[OK] Queue is idle and ready.\n')

    # SECTION 1: 5 Pop-Culture Scenarios (Tiers 5s, 10s, 15s)
    for spec in SPECS:
        print(f"\n===================================================")
        print(f"  PROCESSING: {spec.title.upper()}")
        print(f"===================================================")
        spec_folder = OUTPUT_DIR / spec.id
        spec_folder.mkdir(parents=True, exist_ok=True)

        for tier_sec in [5.0, 10.0, 15.0]:
            tier_name = f"{spec.id}_{int(tier_sec)}s"
            target_mp4 = spec_folder / f"{tier_name}.mp4"
            target_txt = spec_folder / f"{tier_name}_prompt_and_settings.txt"
            tier_seed = (spec.base_seed * 10007 + int(tier_sec * 1013)) % 9000000000 + 100000

            print(f"\n>> [{int(tier_sec)}s Tier] {spec.title} (Seed: {tier_seed})")

            if target_mp4.is_file() and target_mp4.stat().st_size > 10000:
                print(f"  [SKIP] Video already exists: {target_mp4.name}")
                continue

            if tier_sec <= 10.0:
                # Direct T2VA (under 6.85 GB VRAM budget)
                wait_for_queue_idle()
                marker = time.time()
                expected_sec = PROFILER.estimate_render_time(tier_sec, spec.width, spec.height, has_ref=False)
                adaptive_timeout = PROFILER.calculate_adaptive_timeout(tier_sec, spec.width, spec.height, has_ref=False)
                print(f"  Target: {tier_sec}s ({spec.width}x{spec.height}) | Est: ~{int(expected_sec/60)}m | Timeout Headroom: {int(adaptive_timeout/60)}m")
                try:
                    prompt_id = submit_t2va_prompt(spec.base_prompt, spec.width, spec.height, tier_sec, tier_seed)
                    print(f"  Queued T2VA prompt: {prompt_id}")
                    if monitor_and_collect(prompt_id, marker, target_mp4, expected_sec, adaptive_timeout):
                        actual_elapsed = time.time() - marker
                        PROFILER.record_run(tier_sec, spec.width, spec.height, False, actual_elapsed)
                        size_mb = f"{target_mp4.stat().st_size / (1024*1024):.2f} MB"
                        print(f"  [SUCCESS] Created: {target_mp4.name} ({size_mb} in {int(actual_elapsed/60)}m)")
                        write_spec_sidecar(target_txt, f"{spec.title} ({int(tier_sec)}s)", spec.category, tier_name, target_mp4, spec.base_prompt, {
                            'Duration': f"{tier_sec}s",
                            'Resolution': f"{spec.width}x{spec.height}",
                            'Seed': str(tier_seed),
                            'Architecture': 'FreeVideo Pascal / MiniMax H3 (T2VA)'
                        })
                        log_run_to_artifact({
                            'time': time.strftime('%Y-%m-%d %H:%M'),
                            'id': tier_name,
                            'category': spec.category,
                            'seconds': str(tier_sec),
                            'resolution': f"{spec.width}x{spec.height}",
                            'seed': str(tier_seed),
                            'size': size_mb,
                            'status': 'SUCCESS'
                        })
                except Exception as ex:
                    print(f"  [ERROR] {tier_name} failed: {ex}")
            else:
                # 15s via Continuation Flow (10s base + 5s extension)
                base_10s = spec_folder / f"{spec.id}_10s.mp4"
                part2_5s = spec_folder / f"{spec.id}_part2_5s.mp4"
                if not base_10s.is_file():
                    print(f"  [WAIT] Base 10s video required for 15s continuation not found yet.")
                    continue

                frame_name = f"{spec.id}_last_frame.png"
                frame_path = COMFY_INPUT_ROOT / frame_name
                print(f"  Extracting last frame from 10s clip -> {frame_name}...")
                if extract_last_frame(base_10s, frame_path):
                    wait_for_queue_idle()
                    marker = time.time()
                    expected_sec = PROFILER.estimate_render_time(5.0, spec.width, spec.height, has_ref=True)
                    adaptive_timeout = PROFILER.calculate_adaptive_timeout(5.0, spec.width, spec.height, has_ref=True)
                    print(f"  Continuation 5s | Est: ~{int(expected_sec/60)}m | Timeout Headroom: {int(adaptive_timeout/60)}m")
                    try:
                        prompt_id = submit_i2va_prompt(spec.continuation_prompt, spec.width, spec.height, 5.0, tier_seed, frame_name)
                        print(f"  Queued Continuation I2VA prompt: {prompt_id}")
                        if monitor_and_collect(prompt_id, marker, part2_5s, expected_sec, adaptive_timeout):
                            actual_elapsed = time.time() - marker
                            PROFILER.record_run(5.0, spec.width, spec.height, True, actual_elapsed)
                            print(f"  Stitching 10s base + 5s continuation into {target_mp4.name}...")
                            if concatenate_videos(base_10s, part2_5s, target_mp4):
                                size_mb = f"{target_mp4.stat().st_size / (1024*1024):.2f} MB"
                                print(f"  [SUCCESS] 15s Continuation Created: {target_mp4.name} ({size_mb})")
                                write_spec_sidecar(target_txt, f"{spec.title} (15s Continuation)", spec.category, tier_name, target_mp4,
                                                   f"[Part 1 - 0.0s to 10.0s]\n{spec.base_prompt}\n\n[Part 2 Continuation - 10.0s to 15.0s]\n{spec.continuation_prompt}", {
                                    'Duration': '15.0s (Stitched)',
                                    'Resolution': f"{spec.width}x{spec.height}",
                                    'Seed': str(tier_seed),
                                    'Architecture': 'FreeVideo Pascal / MiniMax H3 (Continuation Flow)'
                                })
                                log_run_to_artifact({
                                    'time': time.strftime('%Y-%m-%d %H:%M'),
                                    'id': tier_name,
                                    'category': spec.category,
                                    'seconds': '15.0',
                                    'resolution': f"{spec.width}x{spec.height} (Continuation)",
                                    'seed': str(tier_seed),
                                    'size': size_mb,
                                    'status': 'SUCCESS'
                                })
                    except Exception as ex:
                        print(f"  [ERROR] 15s Continuation failed: {ex}")

    # SECTION 2: 5-Clip Montage Suite: Harry Potter & Female Malfoy
    print("\n===================================================")
    print("  PROCESSING: 5-CLIP HARRY POTTER & FEMALE MALFOY MONTAGE")
    print("===================================================")
    montage_folder = OUTPUT_DIR / '06_multiscene_harry_and_fem_malfoy'
    montage_folder.mkdir(parents=True, exist_ok=True)
    generated_clips: List[Path] = []

    for idx, clip in enumerate(MONTAGE_CLIPS, 1):
        clip_mp4 = montage_folder / f"{clip.clip_id}.mp4"
        clip_txt = montage_folder / f"{clip.clip_id}_prompt.txt"
        print(f"\n>> [Montage Clip {idx}/5] {clip.title} ({clip.movement})")

        if clip_mp4.is_file() and clip_mp4.stat().st_size > 10000:
            print(f"  [SKIP] Clip already exists: {clip_mp4.name}")
            generated_clips.append(clip_mp4)
            continue

        ref_src = OUTPUT_DIR / clip.reference_image_file
        ref_input_name = f"ref_{clip.reference_image_file}"
        ref_dest = COMFY_INPUT_ROOT / ref_input_name
        if ref_src.is_file() and not ref_dest.is_file():
            shutil.copyfile(ref_src, ref_dest)

        has_ref = bool(ref_dest.is_file())
        expected_sec = PROFILER.estimate_render_time(clip.duration, clip.width, clip.height, has_ref)
        adaptive_timeout = PROFILER.calculate_adaptive_timeout(clip.duration, clip.width, clip.height, has_ref)
        print(f"  Target: {clip.duration}s ({clip.width}x{clip.height}) | Est: ~{int(expected_sec/60)}m | Timeout Headroom: {int(adaptive_timeout/60)}m")

        wait_for_queue_idle()
        marker = time.time()
        try:
            if has_ref:
                print(f"  Using Character Reference: {clip.reference_image_file}...")
                prompt_id = submit_i2va_prompt(clip.prompt, clip.width, clip.height, clip.duration, clip.seed, ref_input_name)
            else:
                prompt_id = submit_t2va_prompt(clip.prompt, clip.width, clip.height, clip.duration, clip.seed)

            print(f"  Queued prompt: {prompt_id}")
            if monitor_and_collect(prompt_id, marker, clip_mp4, expected_sec, adaptive_timeout):
                actual_elapsed = time.time() - marker
                PROFILER.record_run(clip.duration, clip.width, clip.height, has_ref, actual_elapsed)
                size_mb = f"{clip_mp4.stat().st_size / (1024*1024):.2f} MB"
                print(f"  [SUCCESS] Clip {idx} Created: {clip_mp4.name} ({size_mb} in {int(actual_elapsed/60)}m)")
                write_spec_sidecar(clip_txt, clip.title, 'Multi-Scene Storyboard Montage', clip.clip_id, clip_mp4, clip.prompt, {
                    'Movement': clip.movement,
                    'Duration': f"{clip.duration}s",
                    'Resolution': f"{clip.width}x{clip.height}",
                    'Character Reference': clip.reference_image_file,
                    'Seed': str(clip.seed)
                })
                generated_clips.append(clip_mp4)
                log_run_to_artifact({
                    'time': time.strftime('%Y-%m-%d %H:%M'),
                    'id': clip.clip_id,
                    'category': 'Montage Clip',
                    'seconds': str(clip.duration),
                    'resolution': f"{clip.width}x{clip.height}",
                    'seed': str(clip.seed),
                    'size': size_mb,
                    'status': 'SUCCESS'
                })
        except Exception as ex:
            print(f"  [ERROR] Clip {idx} failed: {ex}")

    # Final Montage Stitching with Audio Crossfade
    if len(generated_clips) == 5:
        final_montage_mp4 = montage_folder / '06_harry_and_fem_malfoy_montage_25s.mp4'
        final_montage_txt = montage_folder / '06_harry_and_fem_malfoy_montage_25s_notes.txt'
        print(f"\n>> Stitching all 5 clips into continuous 25s montage with 0.3s audio crossfades...")
        if stitch_montage_with_audio_crossfade(generated_clips, final_montage_mp4):
            size_mb = f"{final_montage_mp4.stat().st_size / (1024*1024):.2f} MB"
            print(f"  [SUCCESS] Final 25s Montage Master Created: {final_montage_mp4.name} ({size_mb})")
            notes = (
                "5-Clip Cinematic Montage: Harry Potter & Female Malfoy (Lyra Malfoy)\n"
                "- Total Duration: 25.0s (5 clips x 5.0s with 0.3s audio crossfade transitions)\n"
                "- Musical Theme: The Astronomy Tower Suite in D Minor (Solo Piano & Cello, 68 BPM)\n"
                "- Visual References: Integrated malfoid-anime, malfoid-animation, malfoid-realistic, and malfoid-mix.\n"
            )
            final_montage_txt.write_text(notes, encoding='utf-8')
            log_run_to_artifact({
                'time': time.strftime('%Y-%m-%d %H:%M'),
                'id': '06_harry_and_fem_malfoy_montage_25s',
                'category': 'Full Montage Master',
                'seconds': '25.0',
                'resolution': '512x288 (Crossfade Stitched)',
                'seed': '19980101-19980505',
                'size': size_mb,
                'status': 'SUCCESS'
            })

    print('\n===================================================')
    print('[FreeVideo] All pop-culture and montage runs completed!')
    print(f'Execution Report: {REPORT_FILE}')
    print('===================================================')


if __name__ == '__main__':
    run_pipeline()
