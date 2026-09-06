"""Caller identity for one dispatch, held OUT OF BAND of the arguments.

THE CONTRACT (2026-09-06, review N3). Two implementations depend on the exact
names in this module — the native MCP server in ``server.py`` and the bridge's
tool dispatch — so they are stated here once, in the module every caller
imports, rather than in either caller's comments.

    from .dispatch_context import CALLER_SEAT, set_caller_seat, reset_caller_seat

    token = set_caller_seat("seat:<verified identity>")
    try:
        ...dispatch the tool...
    finally:
        reset_caller_seat(token)

``CALLER_SEAT`` is a ``contextvars.ContextVar`` whose default is ``None``.
``None`` means *no identity was established*, and every consumer must refuse
rather than substitute one. There is no fallback value, no placeholder and no
"unknown" — those are the three shapes this exists to delete.

WHO MAY SET IT, exhaustively:

  * **The native MCP server**, from its OWN spiral session, at dispatch entry.
    Server-side, before any tool body runs. The session id is minted by
    ``spiral.py`` at server start; a tool caller cannot reach it or change it.
  * **The SSE transport**, from the ``X-Sovereign-Seat`` request header on a
    LOOPBACK connect to the plain native ``/sse``, established for the whole
    session before ``server.run``. See ``sse_server.seat_from_scope``.

CORRECTED 2026-09-06 BY THE BRIDGE BUILD, and the correction is the reason the
transport half exists at all: **the bridge does NOT dispatch in-process.** It
opens an SSE session against the sovereign-sse process per call
(``bridge.py:550``, ``sse_client(MCP_SSE_URL, headers=...)``), so a ContextVar
it sets lives in the bridge's process and never reaches a tool handler. With
only the in-process path, every bridge seat would have fallen through to the
server's own spiral session — one shared identity stamped as the closer for
every remote seat, which is exactly the defect N3 names, relocated. A
ContextVar cannot cross a socket; a header can.

WHO MAY NOT: anybody holding a tool argument. This is the whole point.
``signal_ack`` REFUSES the arguments ``actor``, ``actor_seat``, ``owner``,
``closed_by`` and ``source_seat`` — see ``REFUSED_IDENTITY_ARGUMENTS`` — and
the refusal names the offending argument. It does not ignore them. An ignored
argument is indistinguishable, from the caller's side, from an honoured one,
which is how the reviewed build shipped a comment reading "native input is
ignored" over a dispatch that read ``actor_seat`` straight out of the call.

WHY A CONTEXTVAR AND NOT A PARAMETER. The value must cross
``asyncio.to_thread`` (the signal tools run off-loop) and must NOT be
reachable from the tool's own argument dict. ``contextvars`` gives exactly
that: ``to_thread`` copies the current context into the worker thread, so the
identity travels; nothing in the arguments can name it, so a caller cannot
supply it. Two concurrent dispatches each carry their own value — an
``asyncio.Task`` copies the context at creation, so one request's seat can
never leak into another's.

WHY IT IS NOT A MODULE GLOBAL. A global would be shared across concurrent
dispatches, and the failure would be silent and rare: the second request
stamps the first request's seat. ``ContextVar`` is the only shape that is
per-execution-context by construction.

ALWAYS RESET IN A ``finally``. A token left unreset leaks the identity into
whatever the loop runs next in that same context.
"""

from __future__ import annotations

import contextvars

# The one source of actor identity for the signal ledger. Default None =
# "nobody established an identity", which is a refusal, never a default seat.
CALLER_SEAT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sovereign_stack_caller_seat", default=None
)


# ARGUMENT NAMES THAT ARE REFUSED, NOT IGNORED, on signal_ack. Anything that
# smells like a caller naming the closer: the two the reviewed build actually
# read (`actor_seat` at the dispatch, `actor` at the helper), the field the
# pre-review build used for the same purpose (`owner`), the ledger's own
# stamped field (`closed_by`), and the bridge-flavoured spelling somebody
# would reach for next (`source_seat`).
REFUSED_IDENTITY_ARGUMENTS: tuple[str, ...] = (
    "actor",
    "actor_seat",
    "owner",
    "closed_by",
    "source_seat",
)


# HOW AN OUT-OF-PROCESS CALLER GETS ITS IDENTITY IN HERE. Named in this module
# rather than in the transport, because it is part of the contract, and because
# `sse_server` imports `server` — so `server` cannot import `sse_server` back to
# ask. The transport half lives in `sse_server.SEAT_HEADER`, which is this
# string's header form, and the native `heartbeat` publishes this value as
# `caller_identity_channel` so a bridge can tell a stack that carries seat
# identity from one that does not before it admits a seat-attributed write.
CALLER_IDENTITY_CHANNEL = "x-sovereign-seat-sse-header"


def set_caller_seat(seat: str) -> contextvars.Token:
    """Establish the caller identity for this dispatch. Returns the reset token.

    ``seat`` must be a non-empty string. A blank one is a programming error at
    the dispatch, not an identity, and raising here is what keeps a dispatch
    from "successfully" establishing nothing — the caller then has to decide
    what to do about a seat it could not resolve, which is the decision the
    refusal path exists for.
    """
    if not isinstance(seat, str) or not seat.strip():
        raise ValueError("caller seat must be a non-empty string")
    return CALLER_SEAT.set(seat.strip())


def reset_caller_seat(token: contextvars.Token) -> None:
    """Restore whatever the identity was before ``set_caller_seat``.

    Call it in a ``finally``. Resetting with a token from another context
    raises ``ValueError`` out of ``ContextVar.reset``; that is left to
    propagate rather than swallowed, because it means the dispatch nesting is
    wrong and a wrong nesting is how one request's seat becomes another's.
    """
    CALLER_SEAT.reset(token)


def caller_seat() -> str | None:
    """The identity established for this dispatch, or None.

    ``None`` is returned for an unset context AND for a context that was set
    to something blank by a path that bypassed ``set_caller_seat``. Consumers
    treat None as "refuse".
    """
    value = CALLER_SEAT.get()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
