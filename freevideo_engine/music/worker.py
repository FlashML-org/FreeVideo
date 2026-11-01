"""Run isolated music inference and emit a final machine-readable JSON line."""
import argparse
import json
from pathlib import Path
import sys
import time

from .pipeline import MODES, BadRequest, load, validate_request, write_wav

# Free VRAM at start -> mode. XL DiT + LM + text + VAE resident need about
# 16 GiB; one BF16 DiT stage about STAGED; the int8 DiT stage about INT8.
# Below that, DiT blocks stream from the files (slow, same samples as staged).
RESIDENT_FROM_GIB = 20.
STAGED_FROM_GIB = 10.5
INT8_FROM_GIB = 6.5


def choose_mode(free_gib):
    if free_gib >= RESIDENT_FROM_GIB:
        return "resident"
    if free_gib >= STAGED_FROM_GIB:
        return "staged"
    return "int8" if free_gib >= INT8_FROM_GIB else "stream"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise BadRequest(message)


def main(argv=None):
    phase = "request"
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("--root", required=True)
        parser.add_argument("--out", required=True)
        parser.add_argument("--caption", required=True)
        parser.add_argument("--seconds", required=True, type=float)
        parser.add_argument("--seed", required=True, type=int)
        parser.add_argument("--mode", choices=("auto",) + MODES, default="auto")
        parser.add_argument("--device", default="cuda")
        # With --frames the piece is cut to the timeline, levelled under the video sound and faded (mix.finish).
        parser.add_argument("--frames", type=int)
        parser.add_argument("--fps", type=float, default=24.)
        parser.add_argument("--quiet", default="[]")
        args = parser.parse_args(argv)
        validate_request(args.caption, args.seconds, args.seed)
        quiet = _quiet(args.quiet)
        if args.frames is not None and not (0 < args.frames and 0 < args.fps):
            raise BadRequest("frames and fps must be positive")
        import torch
        cuda = args.device == "cuda" and torch.cuda.is_available()
        mode = args.mode
        if cuda:
            torch.cuda.reset_peak_memory_stats()
            if mode == "auto":
                mode = choose_mode(torch.cuda.mem_get_info()[0] / 2**30)
        elif mode == "auto":
            mode = "resident"
        start = time.perf_counter()
        phase = "load"
        pipeline = load(args.root, device=args.device, mode=mode)
        _synchronize(torch, cuda)
        load_s = time.perf_counter() - start
        start = time.perf_counter()
        phase = "generate"
        audio = pipeline.generate(args.caption, args.seconds, args.seed)
        _synchronize(torch, cuda)
        generate_s = time.perf_counter() - start
        if args.frames is not None:
            from .mix import finish
            audio = finish(audio.numpy(), args.frames, args.fps, quiet)
        path = Path(args.out).resolve()
        phase = "write"
        if path.suffix.lower() == ".mp4":
            from .mix import write_mp4
            write_mp4(path, audio)
        else:
            write_wav(path, audio)
        result = dict(ok=True, path=str(path), seconds=audio.shape[1] / 48000,
                      sample_rate=48000, load_s=load_s, generate_s=generate_s, mode=mode,
                      sequential=mode != "resident",
                      lm_s=getattr(pipeline, "stage_times", {}).get("lm_s", 0.0),
                      peak_vram_gib=torch.cuda.max_memory_allocated() / 2**30 if cuda else None)
        code = 0
    except Exception as exc:
        import torch
        if isinstance(exc, BadRequest):
            error = "bad_request"
        elif isinstance(exc, FileNotFoundError) and phase == "load":
            error = "model_missing"
        elif isinstance(exc, torch.cuda.OutOfMemoryError):
            error = "out_of_memory"
        elif isinstance(exc, OSError) and phase == "write":
            error = "write_failed"
        else:
            error = "inference_failed"
        result = dict(ok=False, error=error, detail=str(exc)[:2000])
        code = 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


def _quiet(text):
    try:
        spans = json.loads(text)
        return [(int(start), int(frames)) for start, frames in spans]
    except (TypeError, ValueError) as error:
        raise BadRequest("quiet must be a JSON list of [start, frames]") from error


def _synchronize(torch, cuda):
    if cuda:
        torch.cuda.synchronize()


if __name__ == "__main__":
    sys.exit(main())
