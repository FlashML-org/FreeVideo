"""Review gate operations for background chains: approve, redo, and edit prompts."""
from typing import Any, Dict, Optional
import logging

from aiohttp import web
from . import chain_store as store


def get_review_block(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the block currently awaiting review in the chain record."""
    target_id = record.get('current_review')
    if target_id:
        for block in record.get('blocks', []):
            if block.get('id') == target_id:
                return block
    # Fallback to the latest completed block if current_review is not explicitly set
    done_blocks = [b for b in record.get('blocks', []) if b.get('state') == 'done']
    return done_blocks[-1] if done_blocks else None


def approve_scene(runner: Any, chain_id: str, client_id: Optional[str] = None) -> Dict[str, Any]:
    """Approve the current reviewed scene and resume the chain for subsequent scenes.

    Args:
        runner: The ChainRunner instance managing chain state.
        chain_id: Identifier of the target chain.
        client_id: Optional client ID for verification.

    Returns:
        The updated chain view dictionary.
    """
    with runner.lock:
        if chain_id not in runner.records:
            raise KeyError(f"Unknown chain: {chain_id}")
        record = runner.records[chain_id]
        if record.get('state') != 'awaiting_review':
            raise ValueError(f"Chain {chain_id} is not awaiting review (current state: {record.get('state')})")
        if client_id is not None and record.get('client_id') and record.get('client_id') != client_id:
            raise ValueError("Permission denied: client_id mismatch")

        block = get_review_block(record)
        if block is None:
            raise ValueError(f"No block awaiting review in chain {chain_id}")

        record['current_review'] = None
        has_pending = any(b.get('state') == 'pending' for b in record.get('blocks', []))
        if has_pending:
            record['state'] = 'queued'
            if chain_id not in runner.queue:
                runner.queue.append(chain_id)
        else:
            record['state'] = runner._finish_blocks(record, 'done')

        runner._save(record)
        runner.wake.set()
        logging.info("[FreeVideo] Scene '%s' approved in chain '%s'", block.get('id'), chain_id)
        return runner._view(record, client_id=client_id)


def redo_scene(
    runner: Any,
    chain_id: str,
    seed: Optional[int] = None,
    text: Optional[str] = None,
    client_id: Optional[str] = None
) -> Dict[str, Any]:
    """Reset the current reviewed scene to pending with optional new seed or prompt.

    Args:
        runner: The ChainRunner instance managing chain state.
        chain_id: Identifier of the target chain.
        seed: Optional new seed for the regenerated block.
        text: Optional revised prompt text for the regenerated block.
        client_id: Optional client ID for verification.

    Returns:
        The updated chain view dictionary.
    """
    with runner.lock:
        if chain_id not in runner.records:
            raise KeyError(f"Unknown chain: {chain_id}")
        record = runner.records[chain_id]
        if record.get('state') != 'awaiting_review':
            raise ValueError(f"Chain {chain_id} is not awaiting review (current state: {record.get('state')})")
        if client_id is not None and record.get('client_id') and record.get('client_id') != client_id:
            raise ValueError("Permission denied: client_id mismatch")

        block = get_review_block(record)
        if block is None:
            raise ValueError(f"No block available to redo in chain {chain_id}")

        prompt_id = block.get('prompt_id')
        if prompt_id:
            runner.starts.pop(prompt_id, None)
            runner.success.pop(prompt_id, None)
            runner.cancel_sent.discard(prompt_id)

        # Reset block execution artifacts
        block.update(
            state='pending',
            prompt_id=None,
            started=None,
            finished=None,
            seconds=None,
            output=None,
            report_id=None,
            error=None,
            fraction=0.0,
            remaining_seconds=None,
            continuation=None,
            skip_frames=0,
            skip_audio_samples=0
        )

        if text is not None and isinstance(text, str):
            block['inputs']['text'] = text
        if seed is not None and isinstance(seed, int) and seed >= 0:
            block['inputs']['seed'] = seed
        else:
            # Shift seed to ensure a variation if none provided
            curr_seed = block['inputs'].get('seed', 0)
            block['inputs']['seed'] = (curr_seed + 10007) % (2**53 - 1)

        record['current_review'] = None
        record['state'] = 'queued'
        if chain_id not in runner.queue:
            runner.queue.append(chain_id)

        runner._save(record)
        runner.wake.set()
        logging.info("[FreeVideo] Scene '%s' reset for redo in chain '%s'", block.get('id'), chain_id)
        return runner._view(record, client_id=client_id)


def update_scene_prompt(
    runner: Any,
    chain_id: str,
    block_id: str,
    text: Optional[str] = None,
    seed: Optional[int] = None,
    client_id: Optional[str] = None
) -> Dict[str, Any]:
    """Update prompt text or seed of a pending scene block before it executes.

    Args:
        runner: The ChainRunner instance managing chain state.
        chain_id: Identifier of the target chain.
        block_id: Identifier of the block to modify.
        text: Optional new prompt text.
        seed: Optional new seed.
        client_id: Optional client ID for verification.

    Returns:
        The updated chain view dictionary.
    """
    with runner.lock:
        if chain_id not in runner.records:
            raise KeyError(f"Unknown chain: {chain_id}")
        record = runner.records[chain_id]
        if client_id is not None and record.get('client_id') and record.get('client_id') != client_id:
            raise ValueError("Permission denied: client_id mismatch")

        target_block = None
        for b in record.get('blocks', []):
            if b.get('id') == block_id:
                target_block = b
                break
        if target_block is None:
            raise KeyError(f"Block '{block_id}' not found in chain '{chain_id}'")

        if target_block.get('state') != 'pending':
            raise ValueError(f"Cannot edit block in state '{target_block.get('state')}'; only pending blocks can be modified")

        if text is not None and isinstance(text, str):
            target_block['inputs']['text'] = text
        if seed is not None and isinstance(seed, int) and seed >= 0:
            target_block['inputs']['seed'] = seed

        runner._save(record)
        return runner._view(record, client_id=client_id)


def register_review_routes(server: Any, runner: Any) -> None:
    """Mount review HTTP routes on the ComfyUI web server."""
    def asking(req: web.Request) -> Optional[str]:
        return req.query.get('client_id')

    async def get_body(req: web.Request) -> Dict[str, Any]:
        if not req.can_read_body:
            return {}
        try:
            val = await req.json()
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    @server.routes.post('/freevideo/chain/{id}/approve')
    async def approve_route(request: web.Request) -> web.Response:
        chain_id = request.match_info['id']
        try:
            data = await get_body(request)
            client = data.get('client_id') or asking(request)
            result = approve_scene(runner, chain_id, client_id=client)
            return web.json_response({'chain': result}, headers={'Cache-Control': 'no-store'})
        except KeyError:
            return web.json_response({'error': 'Unknown chain'}, status=404)
        except ValueError as err:
            return web.json_response({'error': str(err)}, status=409)

    @server.routes.post('/freevideo/chain/{id}/redo')
    async def redo_route(request: web.Request) -> web.Response:
        chain_id = request.match_info['id']
        try:
            data = await get_body(request)
            client = data.get('client_id') or asking(request)
            result = redo_scene(runner, chain_id, seed=data.get('seed'), text=data.get('text'), client_id=client)
            return web.json_response({'chain': result}, headers={'Cache-Control': 'no-store'})
        except KeyError:
            return web.json_response({'error': 'Unknown chain'}, status=404)
        except ValueError as err:
            return web.json_response({'error': str(err)}, status=409)

    @server.routes.post('/freevideo/chain/{id}/block/{block_id}')
    async def update_block_route(request: web.Request) -> web.Response:
        chain_id = request.match_info['id']
        block_id = request.match_info['block_id']
        try:
            data = await get_body(request)
            client = data.get('client_id') or asking(request)
            result = update_scene_prompt(runner, chain_id, block_id, text=data.get('text'), seed=data.get('seed'), client_id=client)
            return web.json_response({'chain': result}, headers={'Cache-Control': 'no-store'})
        except KeyError as err:
            return web.json_response({'error': str(err)}, status=404)
        except ValueError as err:
            return web.json_response({'error': str(err)}, status=409)
