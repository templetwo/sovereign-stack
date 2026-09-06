"""One test per finding in the 2026-09-06 round-2 re-review of the RC.

Astra (Codex seat 3/3) rejected tip `d6d3b82` with nine findings, five P1.
This file is the proof, one class per finding, each written so it FAILS on
`d6d3b82` and PASSES on the fix.

`sovereign_stack.dispatch_context` DOES NOT EXIST on `d6d3b82`, so every N3
test imports it INSIDE the test function. A module-scope import would turn
the whole file into a collection error on the old tip, and a collection error
is a much weaker receipt than a named per-test failure: it proves the file
does not load, not that the behaviour is absent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sovereign_stack import server
from sovereign_stack import signal_ledger as sl


def _guardian_ok(root: Path) -> None:
    (root / "guardian").mkdir(parents=True, exist_ok=True)
    (root / "guardian" / "status.json").write_text('{"issues": []}', encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _a_halt(root: Path, name: str = "old.md") -> None:
    (root / "daemons" / "halts").mkdir(parents=True, exist_ok=True)
    (root / "daemons" / "halts" / name).write_text("halt\n", encoding="utf-8")


def _dispatch(name: str, payload: dict):
    return json.loads(asyncio.run(server._dispatch_tool(name, payload))[0].text)


# ══════════════════════════════════════════════════════════════════════════
# N3 — the caller does not choose its own closer. P1.
#
#   "server.py:3217 reads actor_seat directly from tool arguments, and :4388
#    passes it to the ledger as trusted identity. A real in-process MCP
#    request with server session `fixture-established-session` and
#    actor_seat="fixture-different-seat" succeeds and stamps
#    seat:fixture-different-seat."
# ══════════════════════════════════════════════════════════════════════════


class TestN3TheCloserComesFromTheDispatchContext:
    @pytest.fixture
    def signal(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
        _guardian_ok(root)
        _a_halt(root, "x.md")
        sl.scan_all(root)
        return sl.signal_id_for("halt", "x.md")

    def test_the_module_publishes_the_contract_the_bridge_codes_against(self):
        """The names are the contract. A parallel bridge build is importing
        exactly these; renaming any of them is a breaking change."""
        from sovereign_stack import dispatch_context as dc

        assert dc.CALLER_SEAT.get() is None, "the default is None, i.e. no identity"
        token = dc.set_caller_seat("seat:contract")
        try:
            assert dc.CALLER_SEAT.get() == "seat:contract"
            assert dc.caller_seat() == "seat:contract"
        finally:
            dc.reset_caller_seat(token)
        assert dc.CALLER_SEAT.get() is None, "reset restores the previous value"
        assert dc.REFUSED_IDENTITY_ARGUMENTS == (
            "actor",
            "actor_seat",
            "owner",
            "closed_by",
            "source_seat",
        )
        assert "contract" in (dc.__doc__ or "").lower()

    def test_a_blank_seat_cannot_be_established(self):
        from sovereign_stack import dispatch_context as dc

        for blank in ("", "   ", None, 7):
            with pytest.raises(ValueError):
                dc.set_caller_seat(blank)

    @pytest.mark.parametrize(
        "argument",
        ["actor", "actor_seat", "owner", "closed_by", "source_seat"],
    )
    def test_an_identity_argument_is_refused_by_name(self, signal, monkeypatch, argument):
        """REFUSED, NOT IGNORED. `d6d3b82` honoured `actor_seat` and silently
        dropped `actor`/`owner`; both outcomes look identical to the caller,
        which is how a comment claiming native input was ignored survived over
        a dispatch that read it."""
        monkeypatch.setattr(server.spiral_state, "session_id", "fixture-established-session")
        out = _dispatch(
            "signal_ack",
            {
                "signal_id": signal,
                "state": "acted",
                "reason": "closed",
                argument: "fixture-different-seat",
            },
        )
        assert out["ok"] is False
        assert f"{argument!r} is not an accepted argument" in out["error"]
        row = sl.load_latest(sl.default_sovereign_root())[signal]
        assert row["state"] == "open", "a refusal that still writes the row is the fail-open"
        assert row["closed_by"] is None

    def test_the_context_stamps_the_closer(self, signal, monkeypatch):
        from sovereign_stack import dispatch_context as dc

        monkeypatch.setattr(server.spiral_state, "session_id", "fixture-established-session")
        token = dc.set_caller_seat("seat:bridge-verified")
        try:
            out = _dispatch("signal_ack", {"signal_id": signal, "state": "acted", "reason": "ok"})
        finally:
            dc.reset_caller_seat(token)
        assert out["ok"] is True
        assert out["row"]["closed_by"] == "seat:bridge-verified"

    def test_two_contexts_two_closers(self, tmp_sovereign_root, monkeypatch):
        """The reviewer's `c_two_session_ids`, but through the surface that is
        actually per-request. One shared spiral session, two seats, two
        closers — which is the property the shared session cannot supply."""
        from sovereign_stack import dispatch_context as dc

        root = tmp_sovereign_root
        monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
        _guardian_ok(root)
        _a_halt(root, "a.md")
        _a_halt(root, "b.md")
        sl.scan_all(root)
        monkeypatch.setattr(server.spiral_state, "session_id", "one-shared-spiral-session")
        rows = []
        for seat, halt in (("seat:hq-studio", "a.md"), ("seat:grok-build", "b.md")):
            token = dc.set_caller_seat(seat)
            try:
                rows.append(
                    _dispatch(
                        "signal_ack",
                        {
                            "signal_id": sl.signal_id_for("halt", halt),
                            "state": "acted",
                            "reason": "closed",
                        },
                    )
                )
            finally:
                dc.reset_caller_seat(token)
        assert [r["row"]["closed_by"] for r in rows] == ["seat:hq-studio", "seat:grok-build"]

    def test_an_unset_context_is_refused(self, tmp_sovereign_root, monkeypatch):
        """No context AND no server session. The tool refuses; it does not
        invent a seat."""
        root = tmp_sovereign_root
        sl.open_signal(
            source="halt", native_id="c.md", produced_at="2026-09-01T00:00:00Z", root=root
        )
        out = json.loads(
            sl.handle_signal_tool(
                "signal_ack",
                {"signal_id": sl.signal_id_for("halt", "c.md"), "state": "acted", "reason": "x"},
                root=root,
            )
        )
        assert out["ok"] is False
        assert "no caller identity" in out["error"]

    def test_the_contextvar_cannot_be_reached_through_arguments(self, signal, monkeypatch):
        """THE ONE THAT MATTERS. Every argument name that could plausibly
        address the context — the var's own name, its module path, dotted and
        underscored spellings — is either refused or has no effect on the
        stamped closer. The server's own identity is what lands."""
        from sovereign_stack import dispatch_context as dc

        monkeypatch.setattr(server.spiral_state, "session_id", "fixture-established-session")
        reachers = {
            "CALLER_SEAT": "attacker",
            "caller_seat": "attacker",
            "sovereign_stack_caller_seat": "attacker",
            "dispatch_context": {"CALLER_SEAT": "attacker"},
            "context": {"caller_seat": "attacker"},
            "_caller_seat": "attacker",
        }
        out = _dispatch(
            "signal_ack",
            {"signal_id": signal, "state": "acted", "reason": "closed", **reachers},
        )
        assert out["ok"] is True, "these are not refused names; they are simply inert"
        assert out["row"]["closed_by"] == "seat:fixture-established-session"
        assert dc.CALLER_SEAT.get() is None, "the dispatch reset its own token"

    def test_the_dispatch_does_not_overwrite_an_established_identity(self, signal, monkeypatch):
        """The bridge sets its kernel-verified seat before calling in. The
        server's own spiral session is the WEAKER identity — it names the
        server, not the seat — so the dispatch must not clobber it."""
        from sovereign_stack import dispatch_context as dc

        monkeypatch.setattr(server.spiral_state, "session_id", "one-shared-spiral-session")
        token = dc.set_caller_seat("seat:kernel-verified")
        try:
            out = _dispatch("signal_ack", {"signal_id": signal, "state": "acted", "reason": "ok"})
        finally:
            dc.reset_caller_seat(token)
        assert out["row"]["closed_by"] == "seat:kernel-verified"

    def test_the_identity_survives_the_to_thread_hop(self, signal, monkeypatch):
        """The signal tools run off-loop via asyncio.to_thread, which copies
        the current context into the worker. Asserted rather than assumed: a
        bare run_in_executor or a manual thread would NOT carry it, and the
        failure would look like an unrelated refusal."""
        from sovereign_stack import dispatch_context as dc

        seen: list[str | None] = []
        monkeypatch.setattr(server.spiral_state, "session_id", "fixture-established-session")

        real = sl.handle_signal_tool

        def spy(name, arguments, root=None):
            seen.append(dc.CALLER_SEAT.get())
            return real(name, arguments, root)

        monkeypatch.setattr(server, "handle_signal_tool", spy)
        token = dc.set_caller_seat("seat:crosses-the-hop")
        try:
            _dispatch("signal_ack", {"signal_id": signal, "state": "acted", "reason": "ok"})
        finally:
            dc.reset_caller_seat(token)
        assert seen == ["seat:crosses-the-hop"]

    def test_handle_signal_tool_has_no_actor_parameter(self):
        """A parameter would be a SECOND source of identity, and two sources
        is how the first one gets bypassed."""
        import inspect

        params = inspect.signature(sl.handle_signal_tool).parameters
        assert "actor" not in params
        assert list(params) == ["name", "arguments", "root"]

    def test_signal_actor_no_longer_reads_arguments(self):
        import inspect

        assert list(inspect.signature(server._signal_actor).parameters) == []
