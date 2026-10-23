"""The canonical seat list and the committed spelling map (design B3/D1, decision 2).

THE CONTRACT. A seat is named by ONE canonical id, and each signer spelling
measured on the handoff ledger is mapped to that id here, in a committed table,
so a reader's queue can key on the seat rather than on whatever string the seat
typed that night. This is decision 2's answer for the two design items that name
the seat list, B3 (the handoff queue) and D1 (the chronicle's author field); its
one planned reader is the signature ledger (``HandoffEngine.sign``,
``unsigned_by`` and ``signers_of``, plan 2 Task 4).

  * ``CANONICAL_SEATS`` holds the seven ids of decision 2 (Anthony, "All as
    proposed", 2026-10-22 11:22 EDT): the four seats of the seat registry, and
    one id each for the claude.ai web seats, the Jetson seats and the MacBook
    seats.
  * ``SPELLINGS`` maps each of the sixteen measured signer strings to a
    canonical id, or to ``None`` for a probe or audit string that names no
    seat.
  * ``canonical_seat``, ``reader_key`` and ``is_probe_spelling`` read the two
    tables by exact match on ``spelling.strip()``.

THE MEASUREMENT. ``~/.sovereign/handoffs/signatures.jsonl``, signer field only:
16 distinct signers over 820 rows on 2026-10-22, re-measured exactly on
2026-10-23. Seven spellings are HQ (540 rows), two are Grok (40), one is Codex
(40), and six are probes and audits (200 rows: 40+40+40+40+20+20). The keys
are the ledger's exact bytes. The dash in the HQ spellings is U+2014 EM DASH,
written as an escape in the keys below so a reader cannot take it for a hyphen
or an en dash.

WHY EXACT BYTES AND NOT A PATTERN. A pattern that folded every "HQ Mac
Studio" string onto HQ would fold the "(Claude Code) captest" spelling too, a
probe string, and count its 40 signatures as HQ's: a handoff a probe had signed
would leave HQ's queue without HQ reading it. Under exact bytes the probes stay
out by construction. The cost is named: a spelling the table does not know (a
new web or MacBook seat typing its own name) maps to nothing, and
``reader_key`` returns it unchanged, so that seat reads its own queue under its
own string. That is the safe failure: this map never folds it into another
seat's queue and never refuses it.

THE REGISTRY STAYS ANTHONY'S SWITCH. ``~/.sovereign/hq/seats/registry.json`` is
his, written by hand, and its absence is the off switch for seat identity. The
four registry ids, with their ``substrate`` and ``kind``, are PINNED here as
read on 2026-10-22 (re-read 2026-10-23: the same four seats, the same values).
Nothing in this module or its tests reads the file; HQ verifies the pin against
the registry at deploy.

THE LEGACY ``consumed_by`` FIELD IS NOT A SPELLING THIS MAP READS. 58 handoffs
carry a pre-ledger ``consumed_by`` naming a real reader, under 18 spellings,
mostly HQ variants such as ``HQ Mac Studio — claude-fable-5 (OVERWATCH)`` and
``Mac Studio HQ``. The legacy migration never ran: no signature row carries its
note ("migrated from legacy consumed_by"). Those spellings are deliberately
left unmapped. 17 of the 18 are absent from ``SPELLINGS``; the 18th,
``HQ Mac Studio — claude-opus-5 (1M context)`` (8 handoffs), is here only
because it is also a signer spelling. The legacy path never consults this map:
``unsigned_by`` keeps ``consumed_by == reader`` exact (``handoff.py:854`` at
278d42b), so a legacy consumer counts for the reader whose string equals it and
for no one else, exactly as today. Measured 2026-10-23: 49 of the 58 are also
signed on the ledger by ``HQ Mac Studio, claude-fable-5-1``, so once Task 4
folds the spellings they are out of HQ's queue by that signature, not by the
legacy field; the other 9 (``Mac Studio HQ`` 6,
``claude-opus-4-7-1m-claude-code`` 2, ``opus-4-7-1m-claude-code`` 1) carry no
signature and stay in HQ's queue. Extending the map to the legacy spellings is
a later HQ call.

TWO JETSON NAMES, NOT ONE. ``EDGE_SEAT_ID = "openclaw-jetson"`` (plan 2 Task
17, if decision 11's TTL half runs) is the bridge arrival gate's request
token: the first comma-separated token of the string the Jetson's grant
request sends. It is not a signer spelling and it is not defined here (neither
repo imports the other's constant). ``edge-claude-jetson`` is the chronicle's
canonical seat id for that box.
"""

from __future__ import annotations

_REGISTRY_SOURCE = "~/.sovereign/hq/seats/registry.json, read 2026-10-22"
_DECISION_SOURCE = "design decision 2, 2026-10-22"

CANONICAL_SEATS: dict[str, dict] = {
    # The four registry seats. ``substrate`` and ``kind`` are the registry's
    # values, verbatim.
    "hq-claude-studio": {
        "machine": "studio",
        "substrate": "anthropic",
        "kind": "hq",
        "source": _REGISTRY_SOURCE,
    },
    "grok-build-studio": {
        "machine": "studio",
        "substrate": "xai",
        "kind": "seated",
        "source": _REGISTRY_SOURCE,
    },
    "codex-astra-studio": {
        "machine": "studio",
        "substrate": "openai",
        "kind": "seated",
        "source": _REGISTRY_SOURCE,
    },
    "openclaw-watcher-studio": {
        "machine": "studio",
        "substrate": (
            "openclaw (ollama local-strategist-v3 primary; deepseek cloud provider configured)"
        ),
        "kind": "seated",
        "source": _REGISTRY_SOURCE,
    },
    # The three seat kinds outside the registry, one id each (decision 2). The
    # ids follow the registry's <role>-<substrate>-<machine> grammar. For the
    # Jetson and MacBook seats ``machine`` is the id's last segment; the web
    # seats run in claude.ai, on no house machine, so theirs names the service.
    "web-claude-ai": {
        "machine": "claude.ai",
        "substrate": "anthropic",
        "kind": "seated",
        "source": _DECISION_SOURCE,
    },
    "edge-claude-jetson": {
        "machine": "jetson",
        "substrate": "anthropic",
        "kind": "seated",
        "source": _DECISION_SOURCE,
    },
    "terminal-claude-macbook": {
        "machine": "macbook",
        "substrate": "anthropic",
        "kind": "seated",
        "source": _DECISION_SOURCE,
    },
}

SPELLINGS: dict[str, str | None] = {
    # HQ: seven spellings, 540 rows (360, 40, 40, 40, 20, 20, 20 in this order).
    "HQ Mac Studio, claude-fable-5-1": "hq-claude-studio",
    "HQ Mac Studio \u2014 claude-fable-5-1 (Claude Code)": "hq-claude-studio",
    "hq-claude-studio": "hq-claude-studio",
    "HQ Mac Studio \u2014 claude-opus-5 (1M context)": "hq-claude-studio",
    "HQ Mac Studio foreground Terminal seat, claude-fable-5-1, 2026-09-02": "hq-claude-studio",
    "HQ Mac Studio Claude Code seat, claude-fable-5-1": "hq-claude-studio",
    "HQ Mac Studio \u2014 claude-fable-5-1": "hq-claude-studio",
    # Codex: one spelling, 40 rows.
    "gpt-6-astra": "codex-astra-studio",
    # Grok: two spellings, 40 rows (20, 20).
    "Grok 4.6 Mac Studio Copilot seat": "grok-build-studio",
    "grok-build-studio": "grok-build-studio",
    # Probes and audits: six spellings, 200 rows (40, 40, 40, 40, 20, 20). They
    # map to no seat by design; folding them into a seat would count a probe's
    # signatures as that seat's.
    "HQ Mac Studio \u2014 claude-fable-5-1 (Claude Code) captest": None,
    "HQ-Audit-Subagent (claude-sonnet-5)": None,
    "audit-verify-sonnet5": None,
    "audit-phase3-independent-check": None,
    "HQ-acceptance-probe-20260831": None,
    "audit-check-sonnet5": None,
}


def canonical_seat(spelling: str) -> str | None:
    """The canonical seat id ``spelling`` names, or None.

    Exact match on ``spelling.strip()``. A canonical id maps to itself, so a
    seat stamped by the socket (``hq-claude-studio``) and the same seat typing
    an old spelling resolve to one id. None means the spelling is a probe row
    of ``SPELLINGS`` or a string the table does not know; ``is_probe_spelling``
    tells the two apart.
    """
    key = spelling.strip()
    if key in CANONICAL_SEATS:
        return key
    return SPELLINGS.get(key)


def reader_key(spelling: str) -> str:
    """The key a reader's queue and a signer's signature are compared on.

    The canonical id when the spelling maps to a seat, else the stripped
    spelling unchanged. A probe keeps its own key, and so does a spelling the
    table does not know, so an unmapped seat reads its own queue under its own
    string and is never folded into another seat's.
    """
    key = spelling.strip()
    seat = canonical_seat(key)
    return key if seat is None else seat


def is_probe_spelling(spelling: str) -> bool:
    """True for a ``SPELLINGS`` row that maps to no seat (a probe or audit string).

    False for a mapped spelling and for a string the table does not know: an
    unknown spelling is not known to be a probe.
    """
    key = spelling.strip()
    return key in SPELLINGS and SPELLINGS[key] is None
