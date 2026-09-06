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


# ══════════════════════════════════════════════════════════════════════════
# N1 — a designated record's body must not reach a display surface. P1.
#
#   "signal_ledger.py:1089 copies the honk observation into the ledger; :1701
#    returns it verbatim in list mode. The scanner discards the input claim
#    reference rather than preserving enough provenance to apply the
#    designation at read time."
# ══════════════════════════════════════════════════════════════════════════

SYNTHETIC_BODY = "SYNTHETIC_PROTECTED_RECORD_BODY_ROUND3_852"
SYNTHETIC_STAKES = "Synthetic fixture stakes; no personal material."


def _designated_claim(root: Path) -> str:
    """The reviewer's fixture, rebuilt: one insight, its stakes archived and
    hash-verified, one real designation in the index under this root."""
    from sovereign_stack import protected, provenance
    from sovereign_stack.memory import ExperientialMemory

    mem = ExperientialMemory(root=str(root / "chronicle"))
    path = mem.record_insight(
        domain="review-fixture", content=SYNTHETIC_BODY, intensity=0.6, layer="hypothesis"
    )
    record = json.loads(Path(path).read_text().splitlines()[-1])
    cid = provenance.derive_claim_id(record)
    archive = mem.archive_exchange(
        content=SYNTHETIC_STAKES,
        source="review-fixture",
        descriptor="fixture stakes",
        vector_id="protected_stakes",
    )
    protected.designate_protected(
        claim_ref=cid,
        stakes_archive_id=archive["archive_id"],
        designated_by="fixture-human",
        chronicle_root=mem.root,
        subject="fixture",
        emotion="neutral",
    )
    return cid


def _honk_about(root: Path, claim_id: str | None, body: str) -> None:
    rec = {
        "honk_id": "about-designated-record",
        "timestamp": "2026-09-06T16:00:00Z",
        "pattern": "fixture",
        "observation": f"Review of claim {claim_id}: {body}",
    }
    if claim_id:
        rec["claim_id"] = claim_id
    _write_jsonl(root / "nape" / "honks.jsonl", [rec])


def _list_honks(root: Path) -> dict:
    return json.loads(
        sl.handle_signal_tool("signals_summary", {"mode": "list", "source": "honk"}, root=root)
    )


class TestN1ProtectedTextNeverReachesListMode:
    def test_the_designated_body_is_withheld(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        cid = _designated_claim(root)
        _honk_about(root, cid, SYNTHETIC_BODY)
        out = _list_honks(root)
        assert out["ok"] is True
        assert SYNTHETIC_BODY not in json.dumps(out), "the body reached a display surface"
        assert out["signals"][0]["concern"] == sl.WITHHELD_CONCERN
        assert out["withheld_protected"] == 1
        # AND THE STAKES ARE NOT SUBSTITUTED IN. Withholding is not coupling;
        # this surface has no consent gate, so it shows neither.
        assert SYNTHETIC_STAKES not in json.dumps(out)

    def test_the_row_carries_origin_provenance(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        cid = _designated_claim(root)
        _honk_about(root, cid, SYNTHETIC_BODY)
        origin = _list_honks(root)["signals"][0]["origin"]
        assert origin["source"] == "honk"
        assert origin["native_id"] == "about-designated-record"
        assert origin["claim_id"] == cid
        assert origin["path"] == "nape/honks.jsonl"

    def test_a_designation_made_after_the_scan_still_withholds(self, tmp_sovereign_root):
        """THE READ-TIME PROPERTY, and the reason provenance is carried at all.
        Designation happens when the human says so, which is normally AFTER
        the material was written. A scan-time filter would honour only the
        designations that existed when the row was minted."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        from sovereign_stack import protected, provenance
        from sovereign_stack.memory import ExperientialMemory

        mem = ExperientialMemory(root=str(root / "chronicle"))
        path = mem.record_insight(
            domain="review-fixture", content=SYNTHETIC_BODY, intensity=0.6, layer="hypothesis"
        )
        cid = provenance.derive_claim_id(json.loads(Path(path).read_text().splitlines()[-1]))
        _honk_about(root, cid, SYNTHETIC_BODY)

        before = _list_honks(root)
        assert before["signals"][0]["concern"].endswith(SYNTHETIC_BODY)
        assert before["withheld_protected"] == 0

        archive = mem.archive_exchange(
            content=SYNTHETIC_STAKES,
            source="review-fixture",
            descriptor="fixture stakes",
            vector_id="protected_stakes",
        )
        protected.designate_protected(
            claim_ref=cid,
            stakes_archive_id=archive["archive_id"],
            designated_by="fixture-human",
            chronicle_root=mem.root,
            subject="fixture",
            emotion="neutral",
        )
        after = _list_honks(root)
        assert after["signals"][0]["concern"] == sl.WITHHELD_CONCERN
        assert after["withheld_protected"] == 1
        assert SYNTHETIC_BODY not in json.dumps(after)

    def test_the_row_already_in_the_ledger_is_judged_at_read_time(self, tmp_sovereign_root):
        """The row was written before the designation existed, and it is the
        SAME row afterwards — nothing rewrote the ledger. The withholding is
        a property of the read."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        cid = _designated_claim(root)
        _honk_about(root, cid, SYNTHETIC_BODY)
        _list_honks(root)
        raw = sl.ledger_path(root).read_text()
        assert SYNTHETIC_BODY in raw, "the ledger keeps the record; the READER withholds"

    def test_an_unparseable_index_withholds_everything(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _designated_claim(root)
        _honk_about(root, None, "an ordinary unrelated concern")
        (root / "chronicle" / "protected.jsonl").write_text("{not json\n", encoding="utf-8")
        out = _list_honks(root)
        assert out["signals"][0]["concern"] == sl.WITHHELD_CONCERN
        assert out["withheld_protected"] == 1
        assert "protected_index_malformed" in out["error"]

    def test_an_oversized_index_is_refused_not_truncated(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        _guardian_ok(root)
        _designated_claim(root)
        _honk_about(root, None, "an ordinary unrelated concern")
        monkeypatch.setattr(sl, "PROTECTED_INDEX_MAX_BYTES", 10)
        out = _list_honks(root)
        assert out["signals"][0]["concern"] == sl.WITHHELD_CONCERN
        assert "protected_index_unbounded" in out["error"]

    def test_an_absent_index_withholds_nothing_that_carries_provenance(self, tmp_sovereign_root):
        """SUPERSEDED IN FORM BY R4-ROUND R1, KEPT IN SUBSTANCE. Round 3 used an
        UNPROVENANCED honk here and asserted its body was shown; that is the
        exposure R1 closes. The property this test exists for is unchanged and
        still holds: with no designations at all, a concern whose provenance
        CAN be evaluated is shown, so the boundary is a gate and not a blanket
        denial."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _honk_about(root, "c" * 64, "an ordinary unrelated concern")
        assert not sl.protected_index_path(root).exists()
        out = _list_honks(root)
        assert out["signals"][0]["concern"].endswith("an ordinary unrelated concern")
        assert out["withheld_protected"] == 0
        assert out["unprovenanced_concerns"] == 0

    def test_an_unprovenanced_concern_is_withheld_and_counted(self, tmp_sovereign_root):
        """REVERSED BY R1, DELIBERATELY. Round 3 published these and counted
        them, reasoning that a blanket withhold would blank the queue. The
        reviewer answered in one sentence — "counting uncertainty does not
        contain the text already returned" — and proved it by putting a
        designated body in a honk that simply omitted the claim id. The count
        was accurate and the body was still on the wire."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _designated_claim(root)
        _honk_about(root, None, "quoted something without citing it")
        out = _list_honks(root)
        assert out["withheld_protected"] == 0
        assert out["unprovenanced_concerns"] == 1
        assert out["signals"][0]["concern"] == sl.WITHHELD_UNEVALUATED
        assert "quoted something without citing it" not in json.dumps(out)
        # THE QUEUE STAYS ADDRESSABLE. Only the body is withheld.
        row = out["signals"][0]
        assert row["signal_id"] and row["source"] == "honk" and row["state"] == "open"
        assert row["owner"] and row["opened_at"]

    def test_the_ack_response_withholds_too(self, tmp_sovereign_root, monkeypatch):
        """The second display boundary. `signal_ack` returns the row it just
        wrote, concern and all."""
        from sovereign_stack import dispatch_context as dc

        root = tmp_sovereign_root
        _guardian_ok(root)
        cid = _designated_claim(root)
        _honk_about(root, cid, SYNTHETIC_BODY)
        sid = _list_honks(root)["signals"][0]["signal_id"]
        token = dc.set_caller_seat("seat:watch-2-3")
        try:
            out = json.loads(
                sl.handle_signal_tool(
                    "signal_ack",
                    {"signal_id": sid, "state": "acknowledged", "reason": "seen"},
                    root=root,
                )
            )
        finally:
            dc.reset_caller_seat(token)
        assert out["ok"] is True
        assert out["row"]["concern"] == sl.WITHHELD_CONCERN
        assert out["withheld_protected"] == 1
        assert SYNTHETIC_BODY not in json.dumps(out)

    def test_the_ack_carries_origin_forward(self, tmp_sovereign_root):
        """A close that dropped origin would stop the withholding applying to
        exactly the rows a human has already looked at."""
        from sovereign_stack import dispatch_context as dc

        root = tmp_sovereign_root
        _guardian_ok(root)
        cid = _designated_claim(root)
        _honk_about(root, cid, SYNTHETIC_BODY)
        sid = _list_honks(root)["signals"][0]["signal_id"]
        token = dc.set_caller_seat("seat:watch-2-3")
        try:
            sl.handle_signal_tool(
                "signal_ack",
                {"signal_id": sid, "state": "acknowledged", "reason": "seen"},
                root=root,
            )
        finally:
            dc.reset_caller_seat(token)
        row = sl.load_latest(root)[sid]
        assert row["origin"]["claim_id"] == cid
        closed = json.loads(
            sl.handle_signal_tool(
                "signals_summary",
                {"mode": "list", "source": "honk", "state": "acknowledged"},
                root=root,
            )
        )
        assert closed["signals"][0]["concern"] == sl.WITHHELD_CONCERN
        assert SYNTHETIC_BODY not in json.dumps(closed)


# ══════════════════════════════════════════════════════════════════════════
# N2 / N5 / N6 — a read never repairs the evidence, and every error branch
#                returns null. P1, P1, P2.
#
#   "signal_ledger.py:1523 checks marker integrity only to decide whether to
#    skip scanning; on mismatch it calls scan_all at :1531, which writes a new
#    marker. Both return error:null, ingestion:'ok', total:0; the original
#    signal is gone and the marker has been replaced."
# ══════════════════════════════════════════════════════════════════════════


def _scanned_root(root: Path) -> str:
    """One open signal and two completed scans — the reviewer's `damaged`
    fixture up to the point of damage."""
    _guardian_ok(root)
    sl.open_signal(
        source="halt", native_id="fixture", produced_at="2026-08-01T00:00:00Z", root=root
    )
    sl.scan_all(root, guardian_provider=lambda: {"issues": []})
    sl.scan_all(root, guardian_provider=lambda: {"issues": []})
    return sl.signal_id_for("halt", "fixture")


def _read_all_three(root: Path, monkeypatch) -> dict[str, dict]:
    """The reviewer read every damage through all three readers, because the
    passive one was already honest and the two PUBLIC ones were not."""
    monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
    passive = sl.heartbeat_field(root)
    native = _dispatch("heartbeat", {})["unacked_signals"]
    summary = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
    return {"passive": passive, "native": native, "summary": summary}


def _damage(root: Path, kind: str) -> None:
    mpath = sl.scan_marker_path(root)
    marker = json.loads(mpath.read_text())
    if kind == "truncated":
        sl.ledger_path(root).write_text("")
        return
    if kind == "missing":
        sl.ledger_path(root).unlink()
        return
    if kind == "bytes":
        marker["ledger_bytes"] += 20
    elif kind == "hash":
        marker["ledger_sha256"] = "0" * 64
    elif kind == "rows":
        marker["ledger_rows"] += 20
    elif kind == "counts":
        marker["counts"]["halt"] += 20
    mpath.write_text(json.dumps(marker, sort_keys=True) + "\n")


class TestN2ARefreshNeverErasesTheDamage:
    @pytest.mark.parametrize("kind", ["truncated", "missing", "bytes", "hash"])
    def test_every_reader_reports_the_damage(self, tmp_sovereign_root, monkeypatch, kind):
        root = tmp_sovereign_root
        _scanned_root(root)
        _damage(root, kind)
        before = sl.scan_marker_path(root).read_text()
        out = _read_all_three(root, monkeypatch)
        for reader, payload in out.items():
            assert payload.get("error"), f"{reader} reported no error"
            assert payload.get("total") is None, f"{reader} published a total"
        assert sl.scan_marker_path(root).read_text() == before, "the read replaced the marker"

    def test_the_summary_refuses_rather_than_listing_from_a_damaged_ledger(
        self, tmp_sovereign_root, monkeypatch
    ):
        root = tmp_sovereign_root
        _scanned_root(root)
        _damage(root, "truncated")
        out = _read_all_three(root, monkeypatch)["summary"]
        assert out["ok"] is False

    def test_the_refusal_says_what_a_human_must_do(self, tmp_sovereign_root):
        """A fail-closed state a reader cannot get out of is a wedge unless the
        error says who unwedges it and how."""
        root = tmp_sovereign_root
        _scanned_root(root)
        _damage(root, "truncated")
        error = sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []})
        assert error is not None
        assert "ledger_truncated" in error
        # WORDING REPLACED BY R8: round 3's "move the damaged ledger aside" was
        # followed literally by the reviewer and landed in a SECOND refusal,
        # because the certificate survived the move. The instruction now names
        # both artifacts and a quarantine directory, and is followed end to end
        # in test_the_advertised_recovery_actually_recovers below.
        assert "quarantine" in error
        assert str(sl.ledger_path(root)) in error
        assert str(sl.scan_marker_path(root)) in error
        assert "Nothing is deleted" in error

    def test_the_refusal_is_not_cleared_by_reading_again(self, tmp_sovereign_root, monkeypatch):
        """It stays refused. A state that heals itself on the next read is the
        erasure with a delay."""
        root = tmp_sovereign_root
        _scanned_root(root)
        _damage(root, "truncated")
        for _ in range(3):
            out = _read_all_three(root, monkeypatch)
            assert out["native"]["error"]
            assert out["native"]["total"] is None

    def test_a_never_scanned_root_initializes(self, tmp_sovereign_root):
        """The case that must NOT be refused: nothing is being overwritten
        because nothing has been claimed."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        assert not sl.scan_marker_path(root).exists()
        assert sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}) is None
        assert sl.scan_marker_path(root).exists()

    def test_a_zero_byte_certificate_is_invalid_not_absent(self, tmp_sovereign_root):
        """REVERSED BY R4, DELIBERATELY. Round 3 read an empty certificate as
        absent, because the refresh lock flocked that same path and creating it
        for the lock would otherwise have made a fresh root's first read an
        error. The lock is now a sidecar, so the entanglement is gone and the
        two facts separate: no file is a fresh root, an empty file is a
        certificate that was destroyed."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.scan_marker_path(root).parent.mkdir(parents=True, exist_ok=True)
        sl.scan_marker_path(root).write_text("")
        marker, error = sl._read_scan_marker(root)
        assert marker is None
        assert error and "marker_invalid" in error
        refusal = sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []})
        assert refusal is not None and "marker_invalid" in refusal
        assert sl.scan_marker_path(root).read_text() == "", "the read recertified it"

    def test_a_stale_marker_refreshes(self, tmp_sovereign_root):
        """The ordinary path. A refresh over a marker that AGREES with the
        file destroys no evidence, so it is allowed."""
        root = tmp_sovereign_root
        _scanned_root(root)
        mpath = sl.scan_marker_path(root)
        marker = json.loads(mpath.read_text())
        marker["scanned_at"] = "2020-01-01T00:00:00.000Z"
        mpath.write_text(json.dumps(marker, sort_keys=True) + "\n")
        assert sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}) is None
        refreshed = json.loads(mpath.read_text())
        assert refreshed["scanned_at"] != "2020-01-01T00:00:00.000Z"

    def test_an_unparseable_marker_is_refused_not_replaced(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _scanned_root(root)
        mpath = sl.scan_marker_path(root)
        mpath.write_text('{"bogus": true}\n')
        before = mpath.read_text()
        error = sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []})
        assert error is not None and "marker_invalid" in error
        assert mpath.read_text() == before

    def test_two_concurrent_readers_run_one_scan(self, tmp_sovereign_root, monkeypatch):
        """Review judgment (1): 'use a refresh lock if multiple callers may
        arrive together.' Without it both readers sweep and both write a
        marker, the second certifying a ledger the first was still appending
        to."""
        import threading
        import time

        root = tmp_sovereign_root
        _guardian_ok(root)
        calls: list[int] = []
        real = sl.scan_all

        def counted(*a, **kw):
            calls.append(1)
            time.sleep(0.15)
            return real(*a, **kw)

        monkeypatch.setattr(sl, "scan_all", counted)
        threads = [
            threading.Thread(
                target=sl.ensure_scanned,
                args=(root,),
                kwargs={"guardian_provider": lambda: {"issues": []}},
            )
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in threads), "the lock deadlocked"
        assert len(calls) == 1, f"{len(calls)} sweeps for two concurrent readers"


class TestN5TheShrankBranchReturnsNull:
    def test_a_marker_claiming_more_than_the_ledger_holds_nulls_the_total(
        self, tmp_sovereign_root, monkeypatch
    ):
        """`:990` set ledger_shrank and `:1009` copied the numeric total
        through unchanged — the ONE branch with arithmetic proof that rows are
        missing was also the one that still published a count."""
        root = tmp_sovereign_root
        _scanned_root(root)
        _damage(root, "counts")
        out = _read_all_three(root, monkeypatch)
        for reader, payload in out.items():
            assert "ledger_shrank" in (payload.get("error") or ""), reader
            assert payload.get("total") is None, f"{reader} published a total"

    def test_the_per_source_counts_are_nulled_too(self, tmp_sovereign_root, monkeypatch):
        """The missing rows have no source to subtract them from either, so
        the per-source view is exactly as untrustworthy as the total."""
        root = tmp_sovereign_root
        _scanned_root(root)
        _damage(root, "counts")
        field = _read_all_three(root, monkeypatch)["passive"]
        assert field["stale_24h"] is None and field["stale_7d"] is None
        assert set(field["by_source"].values()) == {None}
        assert all(
            v is None for facts in field["by_source_detail"].values() for v in facts.values()
        )


class TestN6TheRecordedRowCountIsReconciled:
    def test_a_row_count_that_shrank_is_caught(self, tmp_sovereign_root, monkeypatch):
        """`_validate_marker` only asked that ledger_rows be a non-negative
        integer; nothing ever compared it to the file. A marker claiming 21
        rows over a one-row ledger stayed healthy through all three readers."""
        root = tmp_sovereign_root
        _scanned_root(root)
        _damage(root, "rows")
        out = _read_all_three(root, monkeypatch)
        for reader, payload in out.items():
            # RENAMED BY R5: the check is now an exact reconciliation against
            # the certified byte prefix, not a shrinkage test, so the error
            # says mismatch rather than shrank.
            assert "ledger_rows_mismatch" in (payload.get("error") or ""), reader
            assert payload.get("total") is None, reader

    def test_a_legitimate_append_is_still_valid(self, tmp_sovereign_root):
        """POSITIVE CONTROL (experimental law #3). The ledger is append-only
        and a watch seat legitimately acks a second after a scan, so
        `actual > claimed` is the ordinary honest case. Reconciling on
        equality instead of shrinkage would fire on every acknowledgement."""
        root = tmp_sovereign_root
        sid = _scanned_root(root)
        sl.ack_signal(sid, "seat:watch-2-3", "acted", "handled", root)
        field = sl.heartbeat_field(root)
        assert field["error"] is None
        assert field["total"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Astra's judgment (2) — not_configured is DECLARED, never inferred.
# ══════════════════════════════════════════════════════════════════════════


class TestNotConfiguredIsDeclaredNeverInferred:
    def test_an_unavailable_source_is_not_reported_as_unconfigured(self, tmp_sovereign_root):
        """The whole point. A guardian probe that returns None is a
        MEASUREMENT FAILURE and must stay loud; reading it as 'not applicable
        here' is F2 wearing a new label."""
        root = tmp_sovereign_root
        sl.scan_all(root, guardian_provider=lambda: None)
        field = sl.heartbeat_field(root)
        assert field["source_status"]["guardian"] == "unavailable"
        assert field["not_configured"] == []
        assert field["total"] is None

    def test_a_declared_source_is_out_of_scope_but_total_still_says_null(self, tmp_sovereign_root):
        """`total` promises all seven sources. A source out of scope is still
        UNMEASURED against that promise, so `total` stays null and the
        narrower number gets its own name."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.source_config_path(root).parent.mkdir(parents=True, exist_ok=True)
        sl.source_config_path(root).write_text(
            json.dumps({"sources": {"guardian": "not_configured"}})
        )
        sl.open_signal(
            source="halt", native_id="h.md", produced_at="2026-08-01T00:00:00Z", root=root
        )
        sl.scan_all(root, guardian_provider=lambda: None)
        field = sl.heartbeat_field(root)
        assert field["source_status"]["guardian"] == "not_configured"
        assert field["not_configured"] == ["guardian"]
        assert field["total"] is None
        assert field["total_configured"] == 1
        assert "guardian" not in field["total_configured_scope"]
        assert len(field["total_configured_scope"]) == len(sl.SOURCES) - 1

    def test_a_configured_source_that_failed_still_nulls_the_configured_total(
        self, tmp_sovereign_root
    ):
        """Astra: 'A configured failed source still makes its required
        aggregate null.' Not-configured and could-not-read are different
        facts, and only the first one shrinks the scope."""
        root = tmp_sovereign_root
        sl.source_config_path(root).parent.mkdir(parents=True, exist_ok=True)
        sl.source_config_path(root).write_text(json.dumps({"sources": {"halt": "not_configured"}}))
        sl.scan_all(root, guardian_provider=lambda: None)
        field = sl.heartbeat_field(root)
        assert field["not_configured"] == ["halt"]
        assert field["total_configured"] is None
        assert field["total"] is None

    def test_an_unreadable_declaration_excuses_nothing(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.source_config_path(root).parent.mkdir(parents=True, exist_ok=True)
        sl.source_config_path(root).write_text("{not json")
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        field = sl.heartbeat_field(root)
        assert field["not_configured"] == []
        assert field["total_configured"] is None
        assert "source_config_unreadable" in field["error"]

    def test_the_scope_travels_with_the_number_on_the_tool_surface(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        out = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
        assert out["total_configured_scope"] == list(sl.SOURCES)
        assert out["not_configured"] == []


# ══════════════════════════════════════════════════════════════════════════
# N4 — watch management accepts paths and cancels outside its store. P1.
#
#   "post_fix_tools.py:231 joins unvalidated IDs; :239 reads the resulting
#    path; :248 trusts the ID inside the loaded record; :253 writes and :256
#    unlinks. With a valid-shaped watch at <root>/post_fix/outside.json
#    carrying watch_id='../outside', status discloses it, resample mutates it,
#    and cancel deletes it after writing the mislocated archive."
# ══════════════════════════════════════════════════════════════════════════


def _pf_call(monkeypatch, root: Path, mode: str, **args) -> dict:
    from sovereign_stack import post_fix_tools as pf

    monkeypatch.setattr(pf, "_root", lambda: root)
    text = asyncio.run(
        pf.handle_post_fix_tool("post_fix_verify", {"mode": mode, **args}, "fixture-session")
    )[0].text
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def _make_watch(monkeypatch, root: Path) -> dict:
    from sovereign_stack import post_fix_tools as pf

    monkeypatch.setattr(pf, "_root", lambda: root)
    target = root / "probe.txt"
    target.write_text("fixture baseline")
    return pf.create_watch(
        "review fixture",
        [],
        [{"name": "fixture-file", "type": "file_hash", "path": str(target)}],
        session_id="fixture-session",
    )


class TestN4TheWatchStoreIsContained:
    def test_the_ordinary_lifecycle_still_works(self, tmp_sovereign_root, monkeypatch):
        """POSITIVE CONTROL FIRST. A containment check that also breaks the
        legitimate path is not a fix; the reviewer's valid-id lifecycle
        (`d_watch_modes_valid_lifecycle`) has to stay green."""
        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        wid = watch["watch_id"]
        assert _pf_call(monkeypatch, root, "status", watch_id=wid)["watch_id"] == wid
        assert _pf_call(monkeypatch, root, "status")["count"] == 1
        assert _pf_call(monkeypatch, root, "resample", watch_id=wid)["status"] == "force_sampled"
        assert (
            _pf_call(monkeypatch, root, "cancel", watch_id=wid, reason="done")["status"]
            == "cancelled"
        )
        assert (root / "post_fix" / "watches" / "archive" / f"{wid}.json").exists()

    @pytest.mark.parametrize("mode", ["status", "resample", "cancel"])
    def test_a_parent_traversal_is_refused_and_nothing_outside_moves(
        self, tmp_sovereign_root, monkeypatch, mode
    ):
        """THE REVIEWER'S FIXTURE. A valid-shaped record parked one directory
        up, addressed as `../outside`."""
        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        watch["watch_id"] = "../outside"
        outside = root / "post_fix" / "outside.json"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text(json.dumps(watch))
        before = outside.read_text()

        out = _pf_call(monkeypatch, root, mode, watch_id="../outside", reason="fixture")
        assert out.get("refused") is True
        assert out.get("status") not in ("force_sampled", "sampled", "cancelled")
        assert out.get("watch_id") != "../outside"
        assert outside.exists(), "the file outside the store was deleted"
        assert outside.read_text() == before, "the file outside the store was mutated"
        assert not (root / "post_fix" / "watches" / "outside.json").exists()

    def test_a_nested_separator_is_refused(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        watch["watch_id"] = "nested/item"
        nested = root / "post_fix" / "watches" / "nested" / "item.json"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text(json.dumps(watch))
        out = _pf_call(monkeypatch, root, "status", watch_id="nested/item")
        assert out.get("refused") is True
        assert out.get("watch_id") != "nested/item"

    def test_a_symlink_out_of_the_store_is_caught_by_path_resolution(
        self, tmp_sovereign_root, monkeypatch
    ):
        """THE SECOND LIMB, PROVEN. A symlink inside the store points out of
        it, and its id is perfectly shape-valid — the regex cannot see through
        a filename. Only resolving the path and demanding its parent EQUAL the
        store directory catches this.

        (Stated precisely so nobody over-reads the regex: `pfw_..` also
        matches the pattern, but `.json` is appended, so it names the ordinary
        file `pfw_...json` INSIDE the store and escapes nothing. The symlink
        is the case where shape and location genuinely disagree.)"""
        from sovereign_stack import post_fix_tools as pf

        root = tmp_sovereign_root
        _make_watch(monkeypatch, root)
        assert pf.WATCH_ID_RE.match("pfw_..")
        assert pf._watch_path("pfw_..").parent == pf._watches_dir().resolve()

        outside = root / "post_fix" / "outside.json"
        outside.write_text("{}")
        (pf._watches_dir() / "pfw_link.json").symlink_to(outside)
        assert pf.WATCH_ID_RE.match("pfw_link")
        with pytest.raises(pf.WatchIdRefused) as caught:
            pf._watch_path("pfw_link")
        assert "outside" in str(caught.value)

    def test_a_stored_id_that_disagrees_with_its_filename_is_refused(
        self, tmp_sovereign_root, monkeypatch
    ):
        """The record steers the archive write, so a file whose contents
        disagree with its own name is refused rather than believed."""
        from sovereign_stack import post_fix_tools as pf

        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        wid = watch["watch_id"]
        path = root / "post_fix" / "watches" / f"{wid}.json"
        watch["watch_id"] = "pfw_somewhere_else"
        path.write_text(json.dumps(watch))
        with pytest.raises(pf.WatchIdRefused):
            pf.load_watch(wid)
        out = _pf_call(monkeypatch, root, "cancel", watch_id=wid, reason="fixture")
        assert out.get("refused") is True
        assert path.exists(), "a refused record was deleted anyway"

    def test_a_listing_never_hands_back_an_id_it_would_refuse(
        self, tmp_sovereign_root, monkeypatch
    ):
        """A listing is a handle factory: every id it prints comes back as a
        `watch_id` argument."""
        from sovereign_stack import post_fix_tools as pf

        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        rogue = dict(watch, watch_id="../outside")
        (root / "post_fix" / "watches" / "pfw_rogue.json").write_text(json.dumps(rogue))
        listing = _pf_call(monkeypatch, root, "status")
        ids = [w["watch_id"] for w in listing["watches"]]
        assert ids == [watch["watch_id"]]
        for wid in ids:
            assert pf._watch_path(wid)

    def test_the_writer_refuses_a_record_that_would_land_outside(
        self, tmp_sovereign_root, monkeypatch
    ):
        from sovereign_stack import post_fix_tools as pf

        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        with pytest.raises(pf.WatchIdRefused):
            pf.save_watch(dict(watch, watch_id="../outside"))
        assert not (root / "post_fix" / "outside.json").exists()


# ══════════════════════════════════════════════════════════════════════════
# N7 — an unchanged unresolved thread must not reopen its acknowledged
#      signal. P2. Introduced by the F7 repair.
#
#   "signal_ledger.py:1425 treats every non-open ledger state as a stale
#    resolution whenever the source thread is unresolved. Scan, acknowledge
#    with reason and closer, scan the identical source again: state becomes
#    open, and reason/closer/closed_at become null in the latest row."
# ══════════════════════════════════════════════════════════════════════════


def _a_thread(root: Path, *, resolved: bool = False, ts: str = "2026-09-01T00:00:00Z") -> str:
    _write_jsonl(
        root / "chronicle" / "open_threads" / "a.jsonl",
        [
            {
                "thread_id": "fixture-thread",
                "question": "Synthetic ordinary thread",
                "timestamp": ts,
                "resolved": resolved,
            }
        ],
    )
    return sl.signal_id_for("thread", "fixture-thread")


class TestN7AnAcknowledgementIsNotAStaleResolution:
    def test_a_rescan_of_an_unchanged_source_preserves_the_ack(self, tmp_sovereign_root):
        """THE REVIEWER'S `f7_unchanged_thread_preserves_ack`."""
        root = tmp_sovereign_root
        sid = _a_thread(root)
        sl.scan_threads(root)
        sl.ack_signal(sid, "fixture-seat", "acknowledged", "read and tracked", root)
        before = sl.load_latest(root)[sid]
        sl.scan_threads(root)
        after = sl.load_latest(root)[sid]
        assert after["state"] == "acknowledged"
        assert after["closed_by"] == before["closed_by"] == "fixture-seat"
        assert after["reason"] == before["reason"] == "read and tracked"
        assert after["closed_at"] == before["closed_at"]

    def test_an_actual_later_source_record_does_reopen_it(self, tmp_sovereign_root):
        """The reversal is not forbidden, it is CONDITIONED. A source record
        dated after the acknowledgement is a real later transition and reopens
        the signal — otherwise the fix would just be F7 undone."""
        root = tmp_sovereign_root
        sid = _a_thread(root)
        sl.scan_threads(root)
        sl.ack_signal(sid, "fixture-seat", "acknowledged", "read and tracked", root)
        _a_thread(root, ts="2099-01-01T00:00:00Z")
        sl.scan_threads(root)
        assert sl.load_latest(root)[sid]["state"] == "open"

    def test_a_scanner_written_close_is_still_reversible(self, tmp_sovereign_root):
        """Source resolution and human acknowledgment are different facts.
        This module's OWN close is a restatement of the source, so a later
        source record may freely restate it back — which is F7, and it must
        keep working."""
        root = tmp_sovereign_root
        sid = _a_thread(root, resolved=True)
        sl.scan_threads(root)
        closed = sl.load_latest(root)[sid]
        assert closed["state"] == "acted"
        assert closed["closed_by"] in sl.SOURCE_DERIVED_CLOSERS
        _a_thread(root, resolved=False)
        sl.scan_threads(root)
        assert sl.load_latest(root)[sid]["state"] == "open"

    def test_an_undatable_source_leaves_the_ack_standing(self, tmp_sovereign_root):
        """A reversal we cannot justify is not a reversal. With no parseable
        source timestamp there is no later transition to point at."""
        root = tmp_sovereign_root
        sid = _a_thread(root)
        sl.scan_threads(root)
        sl.ack_signal(sid, "fixture-seat", "acted", "handled", root)
        _write_jsonl(
            root / "chronicle" / "open_threads" / "a.jsonl",
            [
                {
                    "thread_id": "fixture-thread",
                    "question": "Synthetic ordinary thread",
                    "timestamp": "not-a-date",
                    "resolved": False,
                }
            ],
        )
        rows_before = len(sl.ledger_path(root).read_text().splitlines())
        sl.scan_threads(root)
        assert sl.load_latest(root)[sid]["state"] == "acted"
        # AND NOTHING WAS WRITTEN AT ALL. `d6d3b82` appended a reopen row with
        # `produced_at="not-a-date"`, which `_validate_row` then rejected — so
        # the state looked preserved while the ledger had gained a CORRUPT row
        # that blinds every source count. Preserved-by-refusal is not the same
        # as preserved.
        assert len(sl.ledger_path(root).read_text().splitlines()) == rows_before
        assert sl.load_state(root).corrupt_count == 0

    def test_the_earlier_acknowledgment_survives_in_the_history_either_way(
        self, tmp_sovereign_root
    ):
        """POSITIVE CONTROL. The ledger is append-only: even the legitimate
        reopen must leave the ack readable in the history, not erased."""
        root = tmp_sovereign_root
        sid = _a_thread(root)
        sl.scan_threads(root)
        sl.ack_signal(sid, "fixture-seat", "acknowledged", "read and tracked", root)
        _a_thread(root, ts="2099-01-01T00:00:00Z")
        sl.scan_threads(root)
        rows = [json.loads(x) for x in sl.ledger_path(root).read_text().splitlines() if x.strip()]
        mine = [r for r in rows if r.get("signal_id") == sid]
        assert any(r["state"] == "acknowledged" and r["closed_by"] == "fixture-seat" for r in mine)
        assert mine[-1]["state"] == "open"


# ══════════════════════════════════════════════════════════════════════════
# N9 — a scanner failure keeps its traceback somewhere. P3.
#
#   "Neither logs the traceback. The independent exception fixtures capture no
#    logging output and find no traceback in produced files. Exception text is
#    useful partial diagnosis; it is not a traceback."
# ══════════════════════════════════════════════════════════════════════════


class TestN9ScannerFailuresKeepTheirTraceback:
    def test_a_whole_scanner_failure_writes_a_traceback(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        _guardian_ok(root)

        def boom(*a, **kw):
            raise RuntimeError("fixture scanner diagnosis token")

        monkeypatch.setattr(sl, "scan_all", boom)
        field = sl.heartbeat_field(root, scan=True)
        assert field["total"] is None
        assert "fixture scanner diagnosis token" in field["error"]
        # THE PUBLIC ERROR STAYS A STRING. `heartbeat_field` must not raise —
        # dashboard_web.build_snapshot calls it with no section guard.
        assert isinstance(field["error"], str)
        assert "Traceback (most recent call last)" not in field["error"]
        log = sl.diagnostics_path(root).read_text()
        assert "Traceback (most recent call last)" in log
        assert "fixture scanner diagnosis token" in log
        assert "ensure_scanned" in log

    def test_a_single_source_failure_writes_a_traceback(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        _guardian_ok(root)

        def boom(*a, **kw):
            raise RuntimeError("fixture honk diagnosis token")

        monkeypatch.setattr(sl, "scan_honks", boom)
        result = sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        assert result["source_status"]["honk"].startswith("failed:RuntimeError")
        log = sl.diagnostics_path(root).read_text()
        assert "Traceback (most recent call last)" in log
        assert "fixture honk diagnosis token" in log
        assert "'honk'" in log

    def test_the_other_sources_still_measured(self, tmp_sovereign_root, monkeypatch):
        """POSITIVE CONTROL. Logging the traceback must not change the
        isolation the marker already had."""
        root = tmp_sovereign_root
        _guardian_ok(root)

        def boom(*a, **kw):
            raise RuntimeError("fixture honk diagnosis token")

        monkeypatch.setattr(sl, "scan_honks", boom)
        result = sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        assert result["source_status"]["guardian"] == "ok"

    def test_the_log_is_bounded(self, tmp_sovereign_root, monkeypatch):
        """An exception firing on a 3-second console poll must not fill a
        disk."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        path = sl.diagnostics_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * (sl.DIAGNOSTICS_MAX_BYTES + 4096))

        def boom(*a, **kw):
            raise RuntimeError("fixture honk diagnosis token")

        monkeypatch.setattr(sl, "scan_honks", boom)
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        text = path.read_text()
        assert path.stat().st_size < sl.DIAGNOSTICS_MAX_BYTES
        assert "truncated at" in text, "a fresh file must not read as a quiet one"
        assert "fixture honk diagnosis token" in text

    def test_a_broken_diagnostics_path_never_breaks_the_read(self, tmp_sovereign_root, monkeypatch):
        """This runs inside the handler for something that already went wrong.
        A diagnostic that turns a degraded read into a broken one is worse
        than a missing diagnostic."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        monkeypatch.setattr(
            sl, "diagnostics_path", lambda r=None: Path("/nonexistent-volume/x/y.log")
        )

        def boom(*a, **kw):
            raise RuntimeError("fixture honk diagnosis token")

        monkeypatch.setattr(sl, "scan_honks", boom)
        result = sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        assert result["source_status"]["honk"].startswith("failed:RuntimeError")


# ══════════════════════════════════════════════════════════════════════════
# Astra's judgment (3) — the retirement notice claims only what follows.
# ══════════════════════════════════════════════════════════════════════════


class TestTheRetirementNoticeIsBounded:
    def test_resolve_uncertainty_no_longer_overstates_the_consequence(self):
        """ "'markers stay unresolved forever' overstates what follows from
        removing a tool when underlying files/APIs remain, and the review's
        'real store' was a temporary store, not production." """
        error = server.retired_tool_error("resolve_uncertainty")
        assert "unresolved forever" not in error
        assert "against a real store" not in error
        assert "through the tool surface those markers stay unresolved" in error
        assert "temporary store" in error

    def test_it_still_says_the_capability_is_gone(self):
        """The softening must not walk back the functional distinction — that
        was the whole finding the reclassification closed."""
        error = server.retired_tool_error("resolve_uncertainty")
        assert "NOT FOLDED" in error
        assert "It has no replacement." in error
        assert "does not resolve an existing uncertainty_N marker" in error


# ══════════════════════════════════════════════════════════════════════════
# N3, TRANSPORT HALF — the bridge does NOT dispatch in-process.
#
# HQ, 2026-09-06, from the parallel bridge build: bridge.py:550 opens an SSE
# session per call (`sse_client(MCP_SSE_URL, headers=...)`), so a ContextVar
# the bridge sets never reaches a handler here, and the in-process fallback
# would stamp the SHARED spiral session on every seat's ack. A ContextVar
# cannot cross a socket; a header can.
# ══════════════════════════════════════════════════════════════════════════


def _sse_scope(headers=None, path="/sse", client=("127.0.0.1", 12345)):
    return {
        "type": "http",
        "path": path,
        "method": "GET",
        "headers": headers or [],
        "query_string": b"",
        "client": client,
    }


class TestTheSeatHeaderCarriesIdentityAcrossTheSocket:
    def test_a_loopback_header_binds_the_seat_for_a_signal_ack_on_that_session(
        self, tmp_sovereign_root, monkeypatch
    ):
        """THE WHOLE POINT: a tool dispatched inside the session's context is
        stamped with the seat the header named, not the server's session."""
        from sovereign_stack import sse_server

        root = tmp_sovereign_root
        monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
        _guardian_ok(root)
        _a_halt(root, "x.md")
        sl.scan_all(root)
        monkeypatch.setattr(server.spiral_state, "session_id", "one-shared-spiral-session")

        scope = _sse_scope(headers=[(b"x-sovereign-seat", b"hq-studio")])
        with sse_server.caller_seat_for_session(scope) as seat:
            assert seat == "seat:hq-studio"
            out = _dispatch(
                "signal_ack",
                {
                    "signal_id": sl.signal_id_for("halt", "x.md"),
                    "state": "acted",
                    "reason": "closed",
                },
            )
        assert out["ok"] is True
        assert out["row"]["closed_by"] == "seat:hq-studio"
        assert "one-shared-spiral-session" not in out["row"]["closed_by"]

    def test_no_header_falls_back_to_the_native_spiral_identity(
        self, tmp_sovereign_root, monkeypatch
    ):
        """Every existing client keeps working. An absent header is not an
        error; it is the case that was always there."""
        from sovereign_stack import sse_server

        root = tmp_sovereign_root
        monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
        _guardian_ok(root)
        _a_halt(root, "x.md")
        sl.scan_all(root)
        monkeypatch.setattr(server.spiral_state, "session_id", "native-session")

        scope = _sse_scope()
        assert sse_server.seat_from_scope(scope) == (None, None)
        with sse_server.caller_seat_for_session(scope) as seat:
            assert seat is None
            out = _dispatch(
                "signal_ack",
                {
                    "signal_id": sl.signal_id_for("halt", "x.md"),
                    "state": "acted",
                    "reason": "closed",
                },
            )
        assert out["row"]["closed_by"] == "seat:native-session"

    @pytest.mark.parametrize(
        "bad",
        [
            b"HQ-Studio",
            b"hq studio",
            b"seat:hq",
            b"a",
            b"../etc",
            b"hq/studio",
            b"-leading",
            b"x" * 80,
        ],
    )
    def test_a_malformed_header_refuses_the_connection(self, bad):
        """It does NOT fall back. A client that tried to say who it was and
        got it wrong must not be silently answered as somebody else — that is
        the fail-open family this whole release is closing."""
        from sovereign_stack import sse_server

        seat, refusal = sse_server.seat_from_scope(_sse_scope(headers=[(b"x-sovereign-seat", bad)]))
        assert seat is None
        assert refusal and "X-Sovereign-Seat" in refusal

    def test_a_non_loopback_peer_may_not_assert_a_seat(self):
        """The tunnel terminates elsewhere and forwards, so a non-loopback
        peer is a remote client at the native door. A bearer token proves it
        may call; it does not prove who it is."""
        from sovereign_stack import sse_server

        seat, refusal = sse_server.seat_from_scope(
            _sse_scope(headers=[(b"x-sovereign-seat", b"hq-studio")], client=("203.0.113.9", 443))
        )
        assert seat is None
        assert refusal and "loopback" in refusal

    @pytest.mark.parametrize("path", ["/openai/sse", "/grok/sse"])
    def test_the_header_is_not_read_on_the_substrate_doors(self, path):
        """Ring-filtered doors reached by remote OAuth clients. A
        caller-supplied seat there is N3 one door over. Ignored rather than
        refused: those clients never agreed to this convention."""
        from sovereign_stack import sse_server

        assert sse_server.seat_from_scope(
            _sse_scope(headers=[(b"x-sovereign-seat", b"hq-studio")], path=path)
        ) == (None, None)

    def test_the_route_refuses_before_it_opens_the_session(self, monkeypatch):
        """Through the REAL ASGI router branch, not the helper: a malformed
        header must 400 without ever reaching connect_sse."""
        from sovereign_stack import sse_server

        monkeypatch.setenv("SSE_ALLOW_UNAUTHENTICATED", "true")
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        opened = []
        monkeypatch.setattr(
            sse_server.sse, "connect_sse", lambda *a, **kw: opened.append(1), raising=True
        )
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "http.request"}

        asyncio.run(
            sse_server.app(_sse_scope(headers=[(b"x-sovereign-seat", b"BAD SEAT")]), receive, send)
        )
        assert sent[0]["status"] == 400
        assert not opened, "the session was opened before the header was judged"

    def test_the_route_establishes_the_seat_before_server_run(self, monkeypatch):
        """Through the REAL ASGI router branch: the identity must be live by
        the time `sovereign_server.run` starts, or the first tool call on the
        session is unbound."""
        import contextlib as _contextlib

        from sovereign_stack import dispatch_context as dc
        from sovereign_stack import sse_server

        monkeypatch.setenv("SSE_ALLOW_UNAUTHENTICATED", "true")
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)

        @_contextlib.asynccontextmanager
        async def fake_connect(*a, **kw):
            yield (None, None)

        seen = []

        async def fake_run(*a, **kw):
            seen.append(dc.CALLER_SEAT.get())

        monkeypatch.setattr(sse_server.sse, "connect_sse", fake_connect)
        monkeypatch.setattr(sse_server.sovereign_server, "run", fake_run)

        async def send(msg):
            return None

        async def receive():
            return {"type": "http.request"}

        asyncio.run(
            sse_server.app(
                _sse_scope(headers=[(b"x-sovereign-seat", b"grok-build")]), receive, send
            )
        )
        assert seen == ["seat:grok-build"]
        assert dc.CALLER_SEAT.get() is None, "the session's seat outlived the session"

    def test_the_heartbeat_advertises_the_identity_channel(self, tmp_sovereign_root, monkeypatch):
        """The bridge reads this before admitting a seat-attributed write. An
        older stack answers WITHOUT the field, and that absence is the
        bridge's signal to refuse rather than let every seat close as the
        shared server session."""
        from sovereign_stack import sse_server

        monkeypatch.setattr(sl, "default_sovereign_root", lambda: tmp_sovereign_root)
        _guardian_ok(tmp_sovereign_root)
        out = _dispatch("heartbeat", {})
        assert out["caller_identity_channel"] == "x-sovereign-seat-sse-header"
        assert out["caller_identity_channel"] == sse_server.CALLER_IDENTITY_CHANNEL
        assert sse_server.SEAT_HEADER == b"x-sovereign-seat"


# ══════════════════════════════════════════════════════════════════════════
# HQ closes on round 3's own findings 1 and 2, 2026-09-06.
#
#   (a) ONE identity shape: a header-borne seat is stamped "seat:<name>"
#       exactly like the native fallback, so closed_by has one form
#       everywhere. The bridge keeps sending the bare name.
#   (b) The header validator refuses any value colliding with
#       SOURCE_DERIVED_CLOSERS or the producer labels, with a 400 naming the
#       collision. The shape rule is otherwise unchanged.
# ══════════════════════════════════════════════════════════════════════════


class TestOneIdentityShapeAndNoReservedSeats:
    def test_a_header_seat_is_namespaced_like_the_native_one(self):
        """Round 3 shipped the bare header value into `closed_by` while the
        native fallback shipped `seat:<session>` — two shapes in one column.
        The bridge's side is unchanged: it still sends the bare name, and the
        stack namespaces it at the boundary where it takes custody."""
        from sovereign_stack import sse_server

        seat, refusal = sse_server.seat_from_scope(
            _sse_scope(headers=[(b"x-sovereign-seat", b"hq-studio")])
        )
        assert refusal is None
        assert seat == "seat:hq-studio"
        assert seat.startswith(sse_server.SEAT_NAMESPACE)

    def test_both_doors_produce_the_same_shape(self, tmp_sovereign_root, monkeypatch):
        """The property stated as a property: whichever door the identity came
        through, `closed_by` has one form."""
        from sovereign_stack import sse_server

        root = tmp_sovereign_root
        monkeypatch.setattr(sl, "default_sovereign_root", lambda: root)
        _guardian_ok(root)
        _a_halt(root, "a.md")
        _a_halt(root, "b.md")
        sl.scan_all(root)
        monkeypatch.setattr(server.spiral_state, "session_id", "native-session")

        native = _dispatch(
            "signal_ack",
            {"signal_id": sl.signal_id_for("halt", "a.md"), "state": "acted", "reason": "x"},
        )
        with sse_server.caller_seat_for_session(
            _sse_scope(headers=[(b"x-sovereign-seat", b"grok-build")])
        ):
            header = _dispatch(
                "signal_ack",
                {"signal_id": sl.signal_id_for("halt", "b.md"), "state": "acted", "reason": "x"},
            )
        shapes = {native["row"]["closed_by"], header["row"]["closed_by"]}
        assert shapes == {"seat:native-session", "seat:grok-build"}
        assert all(c.startswith("seat:") for c in shapes)
        assert all(c.count(":") == 1 for c in shapes)

    def test_the_reserved_set_is_derived_from_the_ledger_not_retyped(self):
        """A retyped copy drifts the first time either ledger set gains a
        member, and the drift is silent."""
        from sovereign_stack import sse_server

        reserved = sse_server.reserved_seat_names()
        assert {c.casefold() for c in sl.SOURCE_DERIVED_CLOSERS} <= reserved
        assert {p.casefold() for p in sl.SOURCE_PRODUCER.values()} <= reserved
        assert {"drain", "nape-ack", "daemon", "nape", "watchman", "bridge"} <= reserved

    @pytest.mark.parametrize(
        "reserved",
        ["drain", "nape-ack", "daemon", "nape", "watchman", "bridge", "metabolize", "chronicle"],
    )
    def test_a_reserved_name_is_refused_and_the_reason_names_the_collision(self, reserved):
        """`drain` and `nape-ack` are the sharp two: round 3's `_may_reopen`
        treats a close by either as source-derived, so a seat wearing one
        would have its human acknowledgement reversed by the next scan. The
        producer labels are refused at the door rather than at the write,
        where a PermissionError reads as a bug."""
        from sovereign_stack import sse_server

        seat, refusal = sse_server.seat_from_scope(
            _sse_scope(headers=[(b"x-sovereign-seat", reserved.encode())])
        )
        assert seat is None
        assert refusal is not None
        assert reserved in refusal
        assert "collides with a reserved" in refusal

    def test_the_collision_check_is_case_insensitive(self):
        """`_may_reopen` and the producer check both casefold, so an uppercase
        spelling is the same collision."""
        from sovereign_stack import sse_server

        seat, refusal = sse_server.seat_from_scope(
            _sse_scope(headers=[(b"x-sovereign-seat", b"drain")])
        )
        assert seat is None and refusal
        # The shape rule rejects uppercase before the collision check ever
        # runs, so the two rules together leave no spelling of a reserved name
        # accepted. Both refusals, neither an acceptance.
        upper_seat, upper_refusal = sse_server.seat_from_scope(
            _sse_scope(headers=[(b"x-sovereign-seat", b"DRAIN")])
        )
        assert upper_seat is None and upper_refusal

    def test_a_reserved_name_refuses_the_connection_through_the_real_route(self, monkeypatch):
        from sovereign_stack import sse_server

        monkeypatch.setenv("SSE_ALLOW_UNAUTHENTICATED", "true")
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        opened = []
        monkeypatch.setattr(
            sse_server.sse, "connect_sse", lambda *a, **kw: opened.append(1), raising=True
        )
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "http.request"}

        asyncio.run(
            sse_server.app(_sse_scope(headers=[(b"x-sovereign-seat", b"drain")]), receive, send)
        )
        assert sent[0]["status"] == 400
        body = json.loads(sent[1]["body"])
        assert "drain" in body["detail"]
        assert not opened

    def test_an_ordinary_seat_name_is_still_accepted(self):
        """POSITIVE CONTROL. A denylist that refuses the legitimate case is
        not a gate."""
        from sovereign_stack import sse_server

        for ok in (b"hq-studio", b"grok-build", b"codex-astra", b"watch-2-3"):
            seat, refusal = sse_server.seat_from_scope(
                _sse_scope(headers=[(b"x-sovereign-seat", ok)])
            )
            assert refusal is None, ok
            assert seat == "seat:" + ok.decode()

    def test_a_namespaced_seat_is_still_judged_on_its_bare_name_by_the_producer_check(
        self, tmp_sovereign_root
    ):
        """The namespace must not become a way around producer separation.
        `_actor_identity` strips one namespace before comparing, so
        `seat:daemon` is still the daemon — belt to the door's braces."""
        root = tmp_sovereign_root
        sl.open_signal(
            source="halt", native_id="h.md", produced_at="2026-09-01T00:00:00Z", root=root
        )
        with pytest.raises(PermissionError):
            sl.ack_signal(sl.signal_id_for("halt", "h.md"), "seat:daemon", "acted", "nope", root)


# ══════════════════════════════════════════════════════════════════════════
# ROUND 4 — the nine closures Astra required of 619e409. CLOSURES ONLY.
# Each fixture is the reviewer's own, rebuilt from supplemental_probes.py.
# ══════════════════════════════════════════════════════════════════════════


class TestR1UncertainProvenanceWithholdsEverywhere:
    """R1, P1. "Counting uncertainty does not contain the text already
    returned." The reviewer put a designated body in a honk that omitted the
    claim id: list mode counted it and printed it, and the ack response
    printed it without even the count."""

    def _honk(self, root, claim_id, body):
        rec = {
            "honk_id": "synthetic",
            "timestamp": "2026-09-06T16:00:00Z",
            "pattern": "fixture",
            "observation": body,
        }
        if claim_id:
            rec["claim_id"] = claim_id
        _write_jsonl(root / "nape" / "honks.jsonl", [rec])

    def _ack(self, root):
        from sovereign_stack import dispatch_context as dc

        token = dc.set_caller_seat("seat:fixture-reviewer")
        try:
            return json.loads(
                sl.handle_signal_tool(
                    "signal_ack",
                    {
                        "signal_id": sl.signal_id_for("honk", "synthetic"),
                        "state": "acknowledged",
                        "reason": "fixture review",
                    },
                    root=root,
                )
            )
        finally:
            dc.reset_caller_seat(token)

    def test_an_unprovenanced_body_reaches_neither_surface(self, tmp_sovereign_root):
        """The reviewer's `N1_unprovenanced`."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        _designated_claim(root)
        self._honk(root, None, SYNTHETIC_BODY)
        listed = _list_honks(root)
        acked = self._ack(root)
        assert SYNTHETIC_BODY not in json.dumps([listed, acked])
        assert listed["unprovenanced_concerns"] == 1
        assert acked["unprovenanced_concerns"] == 1

    def test_a_legacy_row_is_reprovenanced_by_the_rescan(self, tmp_sovereign_root):
        """The reviewer's `N1_legacy`: strip origin from the ledger the way
        every pre-release row lacks it, then rescan. Ingestion was idempotent
        on STATE and never restored provenance, so the row stayed exposed —
        and under R1 it would have stayed blinded forever instead."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        cid = _designated_claim(root)
        self._honk(root, cid, SYNTHETIC_BODY)
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        path = sl.ledger_path(root)
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        for row in rows:
            row.pop("origin", None)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        assert sl.load_latest(root)[sl.signal_id_for("honk", "synthetic")].get("origin") is None

        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        restored = sl.load_latest(root)[sl.signal_id_for("honk", "synthetic")]
        assert restored["origin"]["claim_id"] == cid
        listed = _list_honks(root)
        acked = self._ack(root)
        assert SYNTHETIC_BODY not in json.dumps([listed, acked])
        assert listed["withheld_protected"] == 1
        assert acked["withheld_protected"] == 1

    def test_the_backfill_does_not_touch_the_lifecycle(self, tmp_sovereign_root):
        """A provenance write is not a lifecycle write. An append that quietly
        reopened an acked signal would be N7 again."""
        from sovereign_stack import dispatch_context as dc

        root = tmp_sovereign_root
        _guardian_ok(root)
        self._honk(root, "d" * 64, "an ordinary concern")
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        sid = sl.signal_id_for("honk", "synthetic")
        token = dc.set_caller_seat("seat:fixture-reviewer")
        try:
            sl.handle_signal_tool(
                "signal_ack",
                {"signal_id": sid, "state": "acted", "reason": "handled"},
                root=root,
            )
        finally:
            dc.reset_caller_seat(token)
        path = sl.ledger_path(root)
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        for row in rows:
            row.pop("origin", None)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        after = sl.load_latest(root)[sid]
        assert after["origin"]["claim_id"] == "d" * 64
        assert after["state"] == "acted"
        assert after["closed_by"] == "seat:fixture-reviewer"
        assert after["reason"] == "handled"

    def test_the_backfill_is_idempotent(self, tmp_sovereign_root):
        """It fires only when it changes the provenance verdict. A rescan of a
        healthy row must append nothing."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        self._honk(root, "e" * 64, "an ordinary concern")
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        before = len(sl.ledger_path(root).read_text().splitlines())
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        assert len(sl.ledger_path(root).read_text().splitlines()) == before

    def test_both_counts_are_always_present_on_the_ack_response(self, tmp_sovereign_root):
        """A count that appears only when it is interesting is a count a
        reader learns to read as zero."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        self._honk(root, "f" * 64, "an ordinary concern")
        _list_honks(root)
        acked = self._ack(root)
        assert acked["withheld_protected"] == 0
        assert acked["unprovenanced_concerns"] == 0
        assert acked["row"]["concern"] == "an ordinary concern"


class TestR2AnUninterpretableDesignationWithholdsEverything:
    """R2, P1. `fold_protected` SKIPS a row with no action or a non-string
    claim id — right for a recall surface, fail-open here. The reviewer
    replaced a real designation with `{"claim_id": "<same id>"}` and got an
    empty, healthy index: no error, zero withheld, the body printed."""

    def _prepare(self, root, replacement):
        _guardian_ok(root)
        cid = _designated_claim(root)
        _honk_about(root, cid, SYNTHETIC_BODY)
        _list_honks(root)
        if replacement is not None:
            _write_jsonl(sl.protected_index_path(root), replacement(cid))
        return cid

    @pytest.mark.parametrize(
        "row",
        [
            {"claim_id": "CID"},
            {"action": "protect"},
            {"action": "sideways", "claim_id": "CID"},
            {"action": "protect", "claim_id": 7},
            {"action": "protect", "claim_id": "   "},
            {"action": None, "claim_id": "CID"},
        ],
    )
    def test_a_structurally_invalid_designation_is_an_error(self, tmp_sovereign_root, row):
        root = tmp_sovereign_root
        self._prepare(root, lambda cid: [{k: (cid if v == "CID" else v) for k, v in row.items()}])
        out = _list_honks(root)
        assert SYNTHETIC_BODY not in json.dumps(out)
        assert out["signals"][0]["concern"] == sl.WITHHELD_CONCERN
        assert "protected_index_malformed" in out["error"]
        assert out["withheld_protected"] == 1

    def test_the_ack_response_is_gated_by_the_same_failure(self, tmp_sovereign_root):
        from sovereign_stack import dispatch_context as dc

        root = tmp_sovereign_root
        cid = self._prepare(root, lambda cid: [{"claim_id": cid}])
        assert cid
        token = dc.set_caller_seat("seat:fixture-reviewer")
        try:
            acked = json.loads(
                sl.handle_signal_tool(
                    "signal_ack",
                    {
                        "signal_id": sl.signal_id_for("honk", "about-designated-record"),
                        "state": "acknowledged",
                        "reason": "fixture review",
                    },
                    root=root,
                )
            )
        finally:
            dc.reset_caller_seat(token)
        assert SYNTHETIC_BODY not in json.dumps(acked)
        assert "protected_index_malformed" in acked["error"]

    def test_a_valid_designation_still_folds(self, tmp_sovereign_root):
        """POSITIVE CONTROL. The validator must not refuse the real thing —
        `designate_protected` writes the shape this now requires."""
        root = tmp_sovereign_root
        self._prepare(root, None)
        out = _list_honks(root)
        assert out["error"] is None
        assert out["withheld_protected"] == 1
        assert out["signals"][0]["concern"] == sl.WITHHELD_CONCERN


class TestR3APresentHeaderIsAlwaysValidated:
    """R3, P2. `_first_header` returns b'' for absent AND for present-and-empty,
    and round 3 treated both as "no header": the reviewer's empty-value session
    reached connect_sse and acked as the SERVER."""

    def test_an_empty_value_is_refused_not_treated_as_absent(self):
        from sovereign_stack import sse_server

        seat, refusal = sse_server.seat_from_scope(_sse_scope(headers=[(b"x-sovereign-seat", b"")]))
        assert seat is None
        assert refusal and "empty value" in refusal

    def test_a_whitespace_value_is_refused(self):
        from sovereign_stack import sse_server

        seat, refusal = sse_server.seat_from_scope(
            _sse_scope(headers=[(b"x-sovereign-seat", b"   ")])
        )
        assert seat is None and refusal

    def test_an_empty_value_refuses_before_connect_sse(self, monkeypatch):
        from sovereign_stack import sse_server

        monkeypatch.setenv("SSE_ALLOW_UNAUTHENTICATED", "true")
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        opened = []
        monkeypatch.setattr(
            sse_server.sse, "connect_sse", lambda *a, **kw: opened.append(1), raising=True
        )
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "http.request"}

        asyncio.run(sse_server.app(_sse_scope(headers=[(b"x-sovereign-seat", b"")]), receive, send))
        assert sent[0]["status"] == 400
        assert not opened

    def test_an_absent_header_still_falls_back(self):
        """POSITIVE CONTROL. Absence is not a statement; every existing client
        keeps working."""
        from sovereign_stack import sse_server

        assert sse_server.seat_from_scope(_sse_scope()) == (None, None)
        assert sse_server.seat_from_scope(
            _sse_scope(headers=[(b"authorization", b"Bearer x")])
        ) == (None, None)


class TestR4TheLockIsNotTheCertificate:
    """R4, P2. Round 3 flocked the certificate, so creating the lock created
    the certificate, so an EMPTY certificate had to read as absent — which made
    a destroyed one indistinguishable from a fresh root."""

    def test_the_lock_has_its_own_path(self):
        root = Path("/tmp/fixture-root")
        assert sl.scan_lock_path(root) != sl.scan_marker_path(root)
        assert sl.scan_lock_path(root).name == "last_scan.lock"

    def test_a_blank_existing_certificate_is_not_recertified(self, tmp_sovereign_root):
        """The reviewer's `N2_blank_existing_marker`."""
        root = tmp_sovereign_root
        sid = _scanned_root(root)
        marker = sl.scan_marker_path(root)
        marker.write_text("")
        out = sl.heartbeat_field(root, scan=True)
        assert out["error"] and out["total"] is None
        assert marker.read_text() == "", "the read reissued the certificate"
        assert sid in sl.load_latest(root)

    def test_both_files_truncated_is_an_error_not_a_healthy_zero(self, tmp_sovereign_root):
        """The reviewer's `N2_blank_marker_with_ledger_loss`: `error:null,
        ingestion:"ok", total:0` for a store whose signal was gone."""
        root = tmp_sovereign_root
        _scanned_root(root)
        sl.scan_marker_path(root).write_text("")
        sl.ledger_path(root).write_text("")
        out = sl.heartbeat_field(root, scan=True)
        assert out["error"]
        assert out["total"] is None
        assert out["ingestion"] != "ok"

    def test_a_fresh_root_still_initializes(self, tmp_sovereign_root):
        """POSITIVE CONTROL, and the reason round 3 read empty as absent. With
        the lock on its own path, the first read of a store that has never been
        scanned must still work."""
        root = tmp_sovereign_root
        _guardian_ok(root)
        assert not sl.scan_marker_path(root).exists()
        assert sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}) is None
        assert sl.scan_marker_path(root).exists()
        assert sl.heartbeat_field(root)["error"] is None

    def test_the_lock_is_created_without_creating_a_certificate(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        with sl._refresh_lock(root):
            assert sl.scan_lock_path(root).exists()
            assert not sl.scan_marker_path(root).exists()


class TestR5TheCertifiedPrefixRowCountIsExact:
    """R5, P2. Round 3 counted the WHOLE file and refused only a shortfall, so
    an understated count sailed through and a legitimate ack could push the
    whole-file count past an overstated claim and mask it."""

    def _certified(self, root):
        _guardian_ok(root)
        sid = sl.open_signal(
            source="halt", native_id="fixture", produced_at="2026-09-01T00:00:00Z", root=root
        )["signal_id"]
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        return sid

    def _restate(self, root, rows):
        path = sl.scan_marker_path(root)
        marker = json.loads(path.read_text())
        marker["ledger_rows"] = rows
        path.write_text(json.dumps(marker, sort_keys=True) + "\n")

    def test_an_understated_row_count_is_refused(self, tmp_sovereign_root, monkeypatch):
        """The reviewer's `N6_underreported_certified_rows`."""
        root = tmp_sovereign_root
        self._certified(root)
        self._restate(root, 0)
        for reader, payload in _read_all_three(root, monkeypatch).items():
            assert "ledger_rows_mismatch" in (payload.get("error") or ""), reader
            assert payload.get("total") is None, reader

    def test_a_legitimate_append_cannot_mask_an_overstatement(
        self, tmp_sovereign_root, monkeypatch
    ):
        """The reviewer's `N6_append_masks_prefix_row_contradiction`: claim two
        rows for a one-row prefix, then ack. The whole-file count reached two
        and the contradiction was accepted with total 0."""
        root = tmp_sovereign_root
        sid = self._certified(root)
        self._restate(root, 2)
        sl.ack_signal(sid, "seat:fixture-reviewer", "acted", "fixture", root)
        for reader, payload in _read_all_three(root, monkeypatch).items():
            assert "ledger_rows_mismatch" in (payload.get("error") or ""), reader
            assert payload.get("total") is None, reader

    def test_rows_appended_beyond_the_prefix_stay_legitimate(self, tmp_sovereign_root):
        """POSITIVE CONTROL (law #3). The certificate speaks only about its own
        prefix; an append-only ledger must stay checkable while it grows."""
        root = tmp_sovereign_root
        sid = self._certified(root)
        sl.ack_signal(sid, "seat:fixture-reviewer", "acted", "fixture", root)
        out = sl.heartbeat_field(root)
        assert out["error"] is None
        assert out["total"] == 0


class TestR6TheListingChecksContainmentBeforeReading:
    """R6, P2. The direct modes resolve their path; the LISTING opened every
    file the glob returned. The reviewer replaced a watch file with a symlink
    to an outside file and read its contents out of mode='status'."""

    def test_a_symlink_out_of_the_store_is_not_read(self, tmp_sovereign_root, monkeypatch):
        from sovereign_stack import post_fix_tools as pf

        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        wid = watch["watch_id"]
        inside = pf._watches_dir() / f"{wid}.json"
        outside = root / "outside.json"
        outside.write_text(json.dumps(dict(watch, fix_description="OUTSIDE_FIXTURE_MARKER")))
        inside.unlink()
        inside.symlink_to(outside)

        listing = _pf_call(monkeypatch, root, "status")
        assert "OUTSIDE_FIXTURE_MARKER" not in json.dumps(listing)
        assert wid not in json.dumps(listing)
        assert listing["count"] == 0
        assert listing["skipped_uncontained"] == 1

    def test_an_ordinary_listing_is_unaffected(self, tmp_sovereign_root, monkeypatch):
        """POSITIVE CONTROL."""
        root = tmp_sovereign_root
        watch = _make_watch(monkeypatch, root)
        listing = _pf_call(monkeypatch, root, "status")
        assert listing["count"] == 1
        assert listing["skipped_uncontained"] == 0
        assert listing["watches"][0]["watch_id"] == watch["watch_id"]


class TestR7OneAppendRespectsTheFileBound:
    """R7, P3. The bound was checked BEFORE appending an unbounded render, so
    one exception with a 2 MiB message produced a 2,097,902-byte file under a
    1,048,576-byte cap. A bound checked before writing something unbounded is
    not a bound."""

    def test_a_huge_exception_does_not_break_the_cap(self, tmp_sovereign_root, monkeypatch):
        """The reviewer's `N9_single_traceback_respects_byte_bound`."""
        root = tmp_sovereign_root
        _guardian_ok(root)

        def boom(*a, **kw):
            raise RuntimeError("X" * (2 * sl.DIAGNOSTICS_MAX_BYTES))

        monkeypatch.setattr(sl, "scan_honks", boom)
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        path = sl.diagnostics_path(root)
        assert path.stat().st_size <= sl.DIAGNOSTICS_MAX_BYTES

    def test_the_truncation_is_announced_in_the_entry(self, tmp_sovereign_root, monkeypatch):
        """A silently clipped traceback reads as a complete one that ended
        early."""
        root = tmp_sovereign_root
        _guardian_ok(root)

        def boom(*a, **kw):
            raise RuntimeError("Y" * (2 * sl.DIAGNOSTICS_ENTRY_MAX_BYTES))

        monkeypatch.setattr(sl, "scan_honks", boom)
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        text = sl.diagnostics_path(root).read_text()
        assert "entry truncated at" in text
        assert "Traceback (most recent call last)" in text

    def test_repeated_huge_appends_still_respect_the_cap(self, tmp_sovereign_root, monkeypatch):
        root = tmp_sovereign_root
        _guardian_ok(root)

        def boom(*a, **kw):
            raise RuntimeError("Z" * (2 * sl.DIAGNOSTICS_MAX_BYTES))

        monkeypatch.setattr(sl, "scan_honks", boom)
        for _ in range(40):
            sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        assert sl.diagnostics_path(root).stat().st_size <= sl.DIAGNOSTICS_MAX_BYTES

    def test_an_ordinary_traceback_is_kept_whole(self, tmp_sovereign_root, monkeypatch):
        """POSITIVE CONTROL. The cap must not clip the normal case."""
        root = tmp_sovereign_root
        _guardian_ok(root)

        def boom(*a, **kw):
            raise RuntimeError("fixture honk diagnosis token")

        monkeypatch.setattr(sl, "scan_honks", boom)
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        text = sl.diagnostics_path(root).read_text()
        assert "fixture honk diagnosis token" in text
        assert "entry truncated at" not in text


class TestR8TheAdvertisedRecoveryActuallyRecovers:
    """R8, P2. The reviewer followed round 3's instruction literally and landed
    in a SECOND refusal: the certificate survived the move, so the next read
    saw a valid certificate over an absent ledger and told the operator to move
    a file that was no longer there."""

    def test_following_the_instruction_reaches_initialization(self, tmp_sovereign_root):
        """Follows the refusal's own text, on a temporary root, to a clean
        start — and checks nothing was destroyed on the way."""
        root = tmp_sovereign_root
        sid = _scanned_root(root)
        assert sid
        sl.ledger_path(root).write_text("")
        refusal = sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []})
        assert refusal and "quarantine" in refusal

        stamp = "20260906T000000Z"
        quarantine = sl.quarantine_dir(root, stamp)
        quarantine.mkdir(parents=True, exist_ok=True)
        moved = []
        for artefact in (sl.ledger_path(root), sl.scan_marker_path(root), sl.scan_lock_path(root)):
            if artefact.exists():
                artefact.rename(quarantine / artefact.name)
                moved.append(artefact.name)
        assert "ledger.jsonl" in moved and "last_scan.json" in moved

        assert sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}) is None
        after = sl.heartbeat_field(root)
        assert after["error"] is None
        assert after["ingestion"] == "ok"
        # RECEIPTS PRESERVED. Nothing was deleted; the evidence is still there.
        assert (quarantine / "ledger.jsonl").exists()
        assert (quarantine / "last_scan.json").exists()

    def test_the_instruction_names_every_artefact_it_needs(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        text = sl.damaged_ledger_remedy(root)
        for artefact in (
            sl.ledger_path(root),
            sl.scan_marker_path(root),
            sl.scan_lock_path(root),
        ):
            assert str(artefact) in text
        assert str(sl.quarantine_dir(root)) in text
        assert "Nothing is deleted" in text

    def test_moving_only_the_ledger_still_refuses(self, tmp_sovereign_root):
        """The round-3 instruction, followed exactly, and why it was wrong: the
        surviving certificate keeps the store wedged."""
        root = tmp_sovereign_root
        _scanned_root(root)
        sl.ledger_path(root).write_text("")
        sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []})
        ledger = sl.ledger_path(root)
        ledger.rename(ledger.with_name(ledger.name + ".damaged"))
        assert sl.ensure_scanned(root, guardian_provider=lambda: {"issues": []}) is not None


class TestR9AConfigurationErrorIsNotAHealthyIngestion:
    """R9, P2. Round 3 put the configuration failure in `error` and left
    `ingestion: "ok"` with a numeric total beside it."""

    def _config(self, root, payload):
        _guardian_ok(root)
        sl.source_config_path(root).parent.mkdir(parents=True, exist_ok=True)
        sl.source_config_path(root).write_text(json.dumps(payload))
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        return json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))

    @pytest.mark.parametrize(
        "payload",
        [
            {"sources": {"guardian": "disabled"}},
            {"sources": {"guardain": "not_configured"}},
            {"sources": {"guardian": 7}},
            {"sources": []},
            {"nope": {}},
        ],
    )
    def test_an_invalid_declaration_is_config_error_with_a_null_total(
        self, tmp_sovereign_root, payload
    ):
        out = self._config(tmp_sovereign_root, payload)
        assert out["ingestion"] == "config_error"
        assert out["total"] is None
        assert out["total_configured"] is None
        assert out["error"]

    def test_the_unreadable_file_takes_the_same_path(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.source_config_path(root).parent.mkdir(parents=True, exist_ok=True)
        sl.source_config_path(root).write_text("{not json")
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        field = sl.heartbeat_field(root)
        assert field["ingestion"] == "config_error"
        assert field["total"] is None
        assert field["stale_24h"] is None and field["stale_7d"] is None

    def test_a_valid_declaration_is_still_healthy(self, tmp_sovereign_root):
        """POSITIVE CONTROL. A readable declaration must not take the error
        path, and an absent one must leave all seven sources in scope."""
        out = self._config(tmp_sovereign_root, {"sources": {"guardian": "not_configured"}})
        assert out["ingestion"] != "config_error"
        assert out["not_configured"] == ["guardian"]
        assert out["total_configured"] == 0

    def test_an_absent_declaration_is_still_healthy(self, tmp_sovereign_root):
        root = tmp_sovereign_root
        _guardian_ok(root)
        sl.scan_all(root, guardian_provider=lambda: {"issues": []})
        out = json.loads(sl.handle_signal_tool("signals_summary", {}, root=root))
        assert out["ingestion"] == "ok"
        assert out["total"] == 0
        assert len(out["total_configured_scope"]) == len(sl.SOURCES)
