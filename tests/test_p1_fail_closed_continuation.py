"""P1 continuation — the remaining catch-and-return branches fail closed.

The P1 merge (40eefac) fixed record_insight, but six dispatch branches
still caught ValueError/KeyError and RETURNED the rejection as ok-shaped
text — the SDK wraps a returned list as a SUCCESS (isError=False), so
every envelope above reported ok:true. The handoff branch is where the
damage concentrated: lane 3/3's forensics measured 28 of 122 handoffs
(23%) silently dead, versus 0.03% for insights, because a note over
HANDOFF_MAX_BYTES raised, was caught, and was reported as success.

Per standing law #2, every gate here demonstrably FAILS on the unfixed
base (main @ 40eefac): pre-fix, each dispatch call RETURNS text and the
pytest.raises gate fails; post-fix, each raises with the same verbatim
message. The reflection-tool gates live in test_reflector_handlers.py
(updated in the same commit).
"""

import asyncio
from unittest.mock import patch

import pytest

from sovereign_stack.handoff import HANDOFF_MAX_BYTES
from tests.test_nape_autohook import _isolated_server


def _run(coro):
    return asyncio.run(coro)


class TestHandoffFailsClosed:
    def test_oversized_note_raises_instead_of_ok_text(self):
        """The 23%-loss reproducer: a note over HANDOFF_MAX_BYTES must be a
        loud error, not ok-shaped 'Handoff rejected: ...' text."""
        with _isolated_server("p1c-handoff") as (srv, _tmp_root):
            big_note = "x" * (HANDOFF_MAX_BYTES + 1)
            with pytest.raises(ValueError, match="Handoff rejected"):
                _run(
                    srv._dispatch_tool(
                        "handoff",
                        {"note": big_note, "source_instance": "p1c-test", "thread": "p1c"},
                    )
                )

    def test_healthy_handoff_still_succeeds(self):
        """Regression guard, passes on both sides."""
        with _isolated_server("p1c-handoff-ok") as (srv, _tmp_root):
            result = _run(
                srv._dispatch_tool(
                    "handoff",
                    {"note": "healthy probe", "source_instance": "p1c-test", "thread": "p1c"},
                )
            )
            assert result and "rejected" not in result[0].text.lower()


class TestCompassCheckFailsClosed:
    def test_missing_action_raises(self):
        with (
            _isolated_server("p1c-compass") as (srv, _tmp_root),
            pytest.raises(ValueError, match="non-empty 'action'"),
        ):
            _run(srv._dispatch_tool("compass_check", {}))

    def test_engine_valueerror_raises_with_named_tool(self):
        """The catch branch itself: a ValueError from the compass engine must
        surface as a raised, tool-named error, not ok-shaped text."""
        with (
            _isolated_server("p1c-compass-engine") as (srv, _tmp_root),
            patch.object(srv, "runtime_compass_check", side_effect=ValueError("boom")),
            pytest.raises(ValueError, match="compass_check error: boom"),
        ):
            _run(srv._dispatch_tool("compass_check", {"action": "probe"}))


class TestNapeAckFailsClosed:
    def test_unknown_honk_id_raises(self):
        with (
            _isolated_server("p1c-nape") as (srv, _tmp_root),
            pytest.raises(ValueError, match="nape_ack failed"),
        ):
            _run(
                srv._dispatch_tool("nape_ack", {"honk_id": "honk_does_not_exist", "note": "probe"})
            )

    def test_missing_honk_id_raises(self):
        with (
            _isolated_server("p1c-nape-missing") as (srv, _tmp_root),
            pytest.raises(ValueError, match="nape_ack requires honk_id"),
        ):
            _run(srv._dispatch_tool("nape_ack", {"note": "probe"}))
