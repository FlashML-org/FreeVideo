"""Run one audio separation in an isolated process and emit a final machine-readable JSON line."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    code = 'bad_request'
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--weights', required=True)
        parser.add_argument('--source', required=True)
        parser.add_argument('--out', required=True)
        parser.add_argument('--device', default='cuda')
        args = parser.parse_args(argv)
        from .separate import separate_file
        code = 'separation_failed'
        paths, numbers = separate_file(args.source, args.out, args.weights, device=args.device)
        result = dict(ok=True, paths={name: str(Path(path)) for name, path in paths.items()}, **numbers)
        status = 0
    except Exception as exc:
        try:
            import torch
            if isinstance(exc, torch.cuda.OutOfMemoryError):
                code = 'out_of_memory'
        except ImportError:
            pass
        if isinstance(exc, FileNotFoundError):
            code = 'model_missing'
        elif type(exc).__name__ == 'SeparationError' and exc.args:
            code = str(exc.args[0])
        result = dict(ok=False, error=code, detail=str(exc)[:2000])
        status = 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return status


if __name__ == '__main__':
    sys.exit(main())
