"""P1 — the write path fails closed (mesh-20260719).

Per standing law #2, every gate here demonstrably FAILS on unfixed code
(main @ 8ba052d). The pre-fix failure mode is stated on each test; the
proof protocol is: run this file at the branch base and watch each test
fail for the stated reason, then run it after the fix and watch it pass.

The defect chain being closed (live reproducer 2026-07-19, shard
mesh-20260719): record_insight with a slash-carrying domain raised
FileNotFoundError at memory.py's `domain_dir.mkdir(exist_ok=True)`; the
MCP SDK converted the exception into a NORMAL result with isError=True;
the REST bridge read only `.content` and reported {"ok": true} while the
write was lost. The bridge half is fixed in ~/sovereign-bridge (branch
p1/fail-closed-envelope); this file covers the stack half: the domain
label gate and the loud rejection path.
"""

import asyncio

import pytest

from sovereign_stack.memory import ExperientialMemory
from tests.test_nape_autohook import _isolated_server


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Memory layer: a domain is a label, not a path.
# ---------------------------------------------------------------------------


class TestDomainLabelGate:
    def test_slash_domain_rejected_as_label(self, tmp_path):
        """UNFIXED: raises FileNotFoundError (mkdir on a nested path whose
        parent does not exist) — the exact exception that was swallowed into
        ok:true on 2026-07-19. FIXED: a deliberate ValueError naming the rule."""
        mem = ExperientialMemory(root=str(tmp_path / "chronicle"))
        with pytest.raises(ValueError, match="label, not a path"):
            mem.record_insight("a,b,feat/c", "slash-domain probe", 0.5)

    def test_existing_parent_does_not_permit_silent_nesting(self, tmp_path):
        """UNFIXED: this write SUCCEEDS, silently nesting insights/feat/x —
        a namespace no exact-match recall will ever find. That is the quiet
        form of the same loss. FIXED: rejected before anything is created."""
        mem = ExperientialMemory(root=str(tmp_path / "chronicle"))
        (mem.insights_dir / "feat").mkdir(parents=True)
        with pytest.raises(ValueError, match="label, not a path"):
            mem.record_insight("feat/x", "nested-domain probe", 0.5)
        assert not (mem.insights_dir / "feat" / "x").exists()

    def test_traversal_domain_rejected(self, tmp_path):
        """UNFIXED: domain ".." resolves to the chronicle root's parent,
        mkdir(exist_ok=True) succeeds on the existing directory, and the
        entry lands OUTSIDE the insights tree. FIXED: rejected."""
        mem = ExperientialMemory(root=str(tmp_path / "chronicle"))
        with pytest.raises(ValueError):
            mem.record_insight("..", "traversal probe", 0.5)
        # Nothing may have escaped the insights tree.
        stray = [p for p in (tmp_path / "chronicle").glob("*.jsonl")]
        assert stray == []

    def test_plain_and_compound_domains_still_write(self, tmp_path):
        """Regression guard, passes on both sides: ordinary labels — including
        comma-compound ones and ones with dots — are untouched by the gate."""
        mem = ExperientialMemory(root=str(tmp_path / "chronicle"))
        for domain in ("mesh-20260719", "a,b,c", "v4.4-scout", "a, spaced ,b"):
            path = mem.record_insight(domain, f"ok probe for {domain}", 0.5)
            assert path


# ---------------------------------------------------------------------------
# Dispatch layer: rejections must be errors, not ok-shaped text.
# ---------------------------------------------------------------------------


class TestDispatchFailsClosed:
    def test_malformed_receipt_raises_instead_of_ok_text(self):
        """UNFIXED: _dispatch_tool RETURNS list[TextContent] whose text says
        'record_insight rejected: ...' — the SDK wraps that as a SUCCESS
        (isError=False) and every envelope above reports ok:true on a write
        that recorded nothing. FIXED: the rejection raises; the SDK converts
        it to isError=True with the same verbatim text."""
        with _isolated_server("p1-fail-closed") as (srv, _tmp_root):
            with pytest.raises(ValueError, match="record_insight rejected"):
                _run(
                    srv._dispatch_tool(
                        "record_insight",
                        {
                            "domain": "p1-test",
                            "content": "receipt probe",
                            "verified_by": [
                                {"kind": "file", "ref": "/nonexistent", "sha256": "nope"}
                            ],
                        },
                    )
                )

    def test_missing_domain_raises_instead_of_ok_text(self):
        """UNFIXED: returns the requirement message as ok-shaped text.
        FIXED: raises, so no envelope can call it a success."""
        with _isolated_server("p1-fail-closed") as (srv, _tmp_root):
            with pytest.raises(ValueError, match="non-empty 'domain' and 'content'"):
                _run(srv._dispatch_tool("record_insight", {"content": "no domain"}))

    def test_slash_domain_through_dispatch_is_a_named_rejection(self):
        """UNFIXED: FileNotFoundError sails through dispatch (only ValueError
        is caught) and the SDK stringifies it into '[Errno 2] ...' — the exact
        text of the 2026-07-19 loss. FIXED: the label gate turns it into a
        ValueError before any filesystem call, and dispatch names the tool."""
        with _isolated_server("p1-fail-closed") as (srv, _tmp_root):
            with pytest.raises(ValueError, match="label, not a path"):
                _run(
                    srv._dispatch_tool(
                        "record_insight",
                        {"domain": "a,b,feat/c", "content": "dispatch slash probe"},
                    )
                )

    def test_good_write_still_succeeds_through_dispatch(self):
        """Regression guard, passes on both sides."""
        with _isolated_server("p1-fail-closed") as (srv, _tmp_root):
            result = _run(
                srv._dispatch_tool(
                    "record_insight",
                    {"domain": "p1-test", "content": "healthy write probe"},
                )
            )
            assert result and "Insight recorded" in result[0].text
