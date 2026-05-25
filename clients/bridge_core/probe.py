from __future__ import annotations

"""
Generic per-connection Ring 2 capability probe.

A probe detects, at connect time, whether a given substrate connector
actually dispatches Ring 2 (write-class) tool calls to our SSE handler —
as opposed to narrating a write that never arrives.

Usage pattern
─────────────
1. At connect time (in the substrate's SSE handler):
       probe_key = arm_probe(connection_id)

2. In the per-tool dispatch handler, when the sentinel tool arrives:
       resolve_probe(connection_id)

3. Back in the connect handler, concurrently with serving tools:
       outcome = await await_probe(connection_id, timeout=PROBE_TIMEOUT_SECONDS)
       # "verified" | "failed"

Design constraints
──────────────────
- Pure and testable: no SSE, no MCP, no globals beyond the registry dict.
- No cross-connection leakage: each probe is keyed by a per-connection UUID.
- Guaranteed cleanup: await_probe removes the registry entry in a finally
  block regardless of whether the Future resolved or timed out.
- Thread-safety: asyncio.Future is created on the running event loop; this
  module must be used from a single asyncio event loop (standard for ASGI).
"""

import asyncio
import logging
from typing import Literal

logger = logging.getLogger(__name__)

# Registry: connection_id → asyncio.Future[None]
# Populated by arm_probe, resolved by resolve_probe, consumed + cleaned by await_probe.
_PROBE_REGISTRY: dict[str, asyncio.Future[None]] = {}

ProbeOutcome = Literal["verified", "failed"]


def arm_probe(probe_key: str) -> None:
    """
    Register an awaitable Future for `probe_key`.

    Should be called once per connection before any tool dispatch can
    arrive. If a probe is already armed for the same key (should not
    happen in normal operation), the existing Future is replaced —
    this prevents a stale Future from blocking a new connection that
    happens to reuse the same key.
    """
    loop = asyncio.get_running_loop()
    _PROBE_REGISTRY[probe_key] = loop.create_future()
    logger.debug("probe: armed for key=%s", probe_key)


def resolve_probe(probe_key: str) -> bool:
    """
    Signal that the sentinel tool arrived for `probe_key`.

    Returns True if a Future was found and resolved; False if no probe
    was armed for this key (e.g. probing not enabled for this connection).
    Safe to call even when no probe is armed — sentinel handling code
    can always call this without checking first.
    """
    fut = _PROBE_REGISTRY.get(probe_key)
    if fut is None:
        logger.debug("probe: resolve called but no probe armed for key=%s", probe_key)
        return False
    if not fut.done():
        fut.set_result(None)
        logger.debug("probe: resolved for key=%s", probe_key)
    return True


async def await_probe(probe_key: str, timeout: float) -> ProbeOutcome:
    """
    Await the probe Future for up to `timeout` seconds.

    Returns:
        "verified"  — sentinel arrived within the timeout window.
        "failed"    — asyncio.TimeoutError; sentinel never arrived.

    ALWAYS removes the registry entry in a finally block, so no Future
    leaks regardless of outcome. If no Future is registered for this
    key (arm_probe was not called), returns "failed" immediately.
    """
    fut = _PROBE_REGISTRY.get(probe_key)
    if fut is None:
        logger.warning(
            "probe: await_probe called but no probe armed for key=%s — "
            "returning 'failed' without timeout wait",
            probe_key,
        )
        return "failed"

    try:
        await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        logger.debug("probe: verified for key=%s", probe_key)
        return "verified"
    except asyncio.TimeoutError:
        logger.debug("probe: timeout for key=%s (%.1fs)", probe_key, timeout)
        return "failed"
    finally:
        _PROBE_REGISTRY.pop(probe_key, None)
        logger.debug("probe: registry cleaned for key=%s", probe_key)


def probe_registry_size() -> int:
    """Return the current number of armed probes. Exposed for testing only."""
    return len(_PROBE_REGISTRY)
