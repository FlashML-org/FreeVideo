"""A second text-encoder worker for a first one whose CUDA context was lost.

An out-of-memory error that surfaces while the encoder's weights are copied to
the card can leave the worker's context unusable: even the cleanup that frees
its weights fails, so the worker exits and the in-process retries (which add
GPU room one GiB at a time) never run. Nothing was wrong with the request
itself, so the controller starts one more worker, in a new process with a new
context, and asks it to ask less of the system: no page-locked registration of
the checkpoint's read-only pages (the weights are then read from ordinary
memory, with no change to any value) and one more GiB left free on the card.
"""

EXTRA_RESERVE_GIB = 1.0


def fresh_process_retry(failure, metrics):
    """Request fields for a second encoder worker, or None when a retry cannot help.

    Only a positively identified GPU out-of-memory failure whose worker could
    not clean up after itself qualifies. A worker that cleaned up and returned
    the error has already used its own retries with growing reserves, and
    every other failure kind (code errors, device-state errors, RAM pressure,
    cancellation) is not helped by changing how the encoder is registered.
    """
    if not isinstance(failure, dict) or failure.get('kind') != 'gpu_oom' or failure.get('retryable') is not True:
        return None
    metrics = metrics if isinstance(metrics, dict) else {}
    cleanup = metrics.get('failure_cleanup')
    if not isinstance(cleanup, dict) or cleanup.get('complete') is not False:
        return None
    return dict(readonly_registration=False, extra_reserve_gib=EXTRA_RESERVE_GIB)
