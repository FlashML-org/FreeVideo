"""Continue a liked Medium/High checkpoint without resampling earlier steps.

Medium/High/Max share one 20-step schedule. Stopping at 12 or 16 decodes a
watchable clip; resume loads latents.pt and finishes the same timesteps.
"""
import json
from pathlib import Path

CEILING = 20
CHECKPOINTS = {12: 'Medium', 16: 'High', 20: 'Max'}


def schedule_steps(base_steps, quality_ceiling=0, two_pass=False):
    ceiling = quality_ceiling if type(quality_ceiling) is int else 0
    if two_pass or ceiling <= 0 or type(base_steps) is not int:
        return base_steps, None
    if ceiling > base_steps:
        return ceiling, base_steps
    return base_steps, None


def source(video):
    output = Path(video).expanduser().resolve()
    if output.suffix.lower() != '.mp4' or not output.is_file():
        raise ValueError('Choose the checkpoint MP4 to continue')
    artifacts = output.with_suffix('.artifacts')
    latents = artifacts / 'latents.pt'
    report_path = output.with_suffix('.request.json')
    engine_path = output.with_suffix('.engine.json')
    if not latents.is_file():
        raise ValueError('This video no longer has its sampling checkpoint. Generate it again with Medium, High, or Keep this preview.')
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
    engine = json.loads(engine_path.read_text(encoding='utf-8')) if engine_path.is_file() else {}
    early = report.get('early_stop') if isinstance(report, dict) else None
    if not isinstance(early, dict):
        early = dict(completed_steps=engine.get('completed_steps'), planned_steps=engine.get('planned_steps')) if engine.get('early_stop') is True else None
    if not isinstance(early, dict) or type(early.get('completed_steps')) is not int or early['completed_steps'] < 1:
        raise ValueError('This video has no remaining sampling steps. Continue remaining steps is for a Medium/High checkpoint or Keep this preview.')
    planned = early.get('planned_steps')
    if type(planned) is not int or planned <= early['completed_steps']:
        raise ValueError('This checkpoint already finished its sampling schedule')
    conditioning = artifacts / 'conditioning.pt'
    return dict(output=str(output), latents=str(latents),
                conditioning=str(conditioning) if conditioning.is_file() else None,
                completed_steps=early['completed_steps'], planned_steps=planned,
                report=report if isinstance(report, dict) else {})
