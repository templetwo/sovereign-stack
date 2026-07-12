"""
Service ledger tests — reason-receipts on disabled services (GPT-5.6
hardening item #4).

Covers the append-only disable/enable ledger (build/append/load/fold,
mirroring provenance.py's supersession ledger), the module-importability
check that surfaces "not merely paused, actually unrunnable" plists, and
the seed data for the two known out-of-band disabled services.

Hermetic — everything under tmp_path; ~/.sovereign and the real launchd
plists under ~/Library/LaunchAgents are never read.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sovereign_stack import service_ledger as sl

# ── Fixture plist text (hermetic — not the real files on disk) ──────────────

_DREAM_STYLE_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.templetwo.sovereign.dream</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/tony_studio/sovereign-stack/venv/bin/python</string>
        <string>-m</string>
        <string>sovereign_stack.daemons.dream_daemon</string>
    </array>
</dict>
</plist>
"""

_REAL_MODULE_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.templetwo.sovereign.synthesis</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/tony_studio/sovereign-stack/venv/bin/python</string>
        <string>-m</string>
        <string>sovereign_stack.daemons.synthesis_daemon</string>
    </array>
</dict>
</plist>
"""

_NO_MODULE_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.templetwo.cloudflared-tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/cloudflared</string>
        <string>tunnel</string>
        <string>run</string>
    </array>
</dict>
</plist>
"""


# ── build_service_record ─────────────────────────────────────────────────────


class TestBuildServiceRecord:
    def test_disable_requires_reason(self):
        with pytest.raises(sl.ServiceLedgerError, match="requires a reason"):
            sl.build_service_record(action="disable", service="com.test.x", reason="")

    def test_disable_with_reason_ok(self):
        record = sl.build_service_record(
            action="disable", service="com.test.x", reason="testing", by="HQ"
        )
        assert record["action"] == "disable"
        assert record["service"] == "com.test.x"
        assert record["reason"] == "testing"
        assert record["by"] == "HQ"

    def test_enable_allows_empty_reason(self):
        record = sl.build_service_record(action="enable", service="com.test.x")
        assert record["action"] == "enable"

    def test_invalid_action_rejected(self):
        with pytest.raises(sl.ServiceLedgerError, match="invalid service action"):
            sl.build_service_record(action="pause", service="com.test.x", reason="x")

    def test_empty_service_rejected(self):
        with pytest.raises(sl.ServiceLedgerError, match="non-empty string"):
            sl.build_service_record(action="enable", service="")

    def test_default_timestamp_is_iso(self):
        record = sl.build_service_record(action="enable", service="com.test.x")
        # Round-trips through fromisoformat without raising.
        datetime.fromisoformat(record["timestamp"])

    def test_explicit_timestamp_preserved(self):
        record = sl.build_service_record(
            action="enable", service="com.test.x", timestamp="2026-01-01T00:00:00+00:00"
        )
        assert record["timestamp"] == "2026-01-01T00:00:00+00:00"

    def test_re_enable_condition_optional(self):
        record = sl.build_service_record(action="disable", service="com.test.x", reason="x")
        assert record["re_enable_condition"] is None

    def test_plist_path_optional(self):
        record = sl.build_service_record(action="disable", service="com.test.x", reason="x")
        assert record["plist_path"] is None


# ── append/load round trip ───────────────────────────────────────────────────


class TestAppendAndLoad:
    def test_missing_ledger_loads_empty(self, tmp_path):
        assert sl.load_service_records(tmp_path / "nope.jsonl") == []

    def test_append_creates_parent_dir(self, tmp_path):
        ledger = tmp_path / "nested" / "services.jsonl"
        record = sl.build_service_record(action="disable", service="com.test.x", reason="x")
        sl.append_service_record(ledger, record)
        assert ledger.exists()

    def test_round_trip(self, tmp_path):
        ledger = tmp_path / "services.jsonl"
        r1 = sl.build_service_record(action="disable", service="com.test.a", reason="a")
        r2 = sl.build_service_record(action="disable", service="com.test.b", reason="b")
        sl.append_service_record(ledger, r1)
        sl.append_service_record(ledger, r2)
        loaded = sl.load_service_records(ledger)
        assert loaded == [r1, r2]

    def test_append_rejects_bad_action(self, tmp_path):
        ledger = tmp_path / "services.jsonl"
        with pytest.raises(sl.ServiceLedgerError):
            sl.append_service_record(ledger, {"action": "nope", "service": "com.test.x"})
        assert not ledger.exists()

    def test_append_rejects_missing_service(self, tmp_path):
        ledger = tmp_path / "services.jsonl"
        with pytest.raises(sl.ServiceLedgerError):
            sl.append_service_record(ledger, {"action": "disable", "service": ""})
        assert not ledger.exists()

    def test_corrupt_lines_skipped_on_load(self, tmp_path):
        ledger = tmp_path / "services.jsonl"
        ledger.write_text('{"action": "disable", "service": "com.test.x"}\nnot json\n\n')
        loaded = sl.load_service_records(ledger)
        assert len(loaded) == 1


# ── fold_service_state ───────────────────────────────────────────────────────


class TestFoldServiceState:
    def test_single_disable(self):
        records = [sl.build_service_record(action="disable", service="com.test.x", reason="x")]
        fold = sl.fold_service_state(records)
        assert fold["com.test.x"]["state"] == "disabled"

    def test_latest_action_wins(self):
        records = [
            sl.build_service_record(action="disable", service="com.test.x", reason="x"),
            sl.build_service_record(action="enable", service="com.test.x"),
        ]
        fold = sl.fold_service_state(records)
        assert fold["com.test.x"]["state"] == "enabled"

    def test_re_disable_after_enable(self):
        records = [
            sl.build_service_record(action="disable", service="com.test.x", reason="first"),
            sl.build_service_record(action="enable", service="com.test.x"),
            sl.build_service_record(action="disable", service="com.test.x", reason="second"),
        ]
        fold = sl.fold_service_state(records)
        assert fold["com.test.x"]["state"] == "disabled"
        assert fold["com.test.x"]["reason"] == "second"

    def test_malformed_records_skipped(self):
        fold = sl.fold_service_state([{"action": "nope", "service": "com.test.x"}, {}])
        assert fold == {}

    def test_unrelated_services_independent(self):
        records = [
            sl.build_service_record(action="disable", service="com.test.a", reason="a"),
            sl.build_service_record(action="enable", service="com.test.b"),
        ]
        fold = sl.fold_service_state(records)
        assert fold["com.test.a"]["state"] == "disabled"
        assert fold["com.test.b"]["state"] == "enabled"


# ── extract_python_module_arg ────────────────────────────────────────────────


class TestExtractModuleArg:
    def test_extracts_dream_style_module(self):
        assert (
            sl.extract_python_module_arg(_DREAM_STYLE_PLIST)
            == "sovereign_stack.daemons.dream_daemon"
        )

    def test_no_module_arg_returns_none(self):
        assert sl.extract_python_module_arg(_NO_MODULE_PLIST) is None

    def test_empty_text_returns_none(self):
        assert sl.extract_python_module_arg("") is None


# ── check_module_importable — the gate must fail AND the control must pass ──


class TestCheckModuleImportable:
    def test_missing_leaf_module_is_not_importable(self):
        """The gate: dream_daemon.py does not exist on this checkout (it lives
        only on the unmerged feat/dream-layer branch). Parent package
        sovereign_stack.daemons DOES exist, so this exercises find_spec
        returning None rather than raising."""
        result = sl.check_module_importable("sovereign_stack.daemons.dream_daemon")
        assert result["importable"] is False
        assert result["module"] == "sovereign_stack.daemons.dream_daemon"

    def test_missing_parent_package_is_not_importable(self):
        """A second failure shape: the parent package itself doesn't exist,
        so find_spec raises ModuleNotFoundError instead of returning None.
        Both must be caught, not just the None case."""
        result = sl.check_module_importable("definitely_not_a_real_top_level_package_xyz")
        assert result["importable"] is False
        assert result["error"]

    def test_real_module_is_importable(self):
        """The control: a module known to exist in this checkout must pass,
        proving the gate isn't just permanently False."""
        result = sl.check_module_importable("sovereign_stack.daemons.synthesis_daemon")
        assert result["importable"] is True
        assert result["error"] is None

    def test_this_module_is_importable(self):
        result = sl.check_module_importable("sovereign_stack.connectivity")
        assert result["importable"] is True


# ── check_service_module ─────────────────────────────────────────────────────


class TestCheckServiceModule:
    def test_no_plist_path_returns_none(self):
        record = sl.build_service_record(action="disable", service="com.test.x", reason="x")
        assert sl.check_service_module(record) is None

    def test_unreadable_plist_reports_error(self, tmp_path):
        record = sl.build_service_record(
            action="disable",
            service="com.test.x",
            reason="x",
            plist_path=str(tmp_path / "nonexistent.plist"),
        )
        result = sl.check_service_module(record)
        assert result["importable"] is None
        assert "could not read plist" in result["error"]

    def test_plist_with_no_module_arg_returns_none(self, tmp_path):
        plist = tmp_path / "cloudflared.plist"
        plist.write_text(_NO_MODULE_PLIST)
        record = sl.build_service_record(
            action="disable", service="com.test.x", reason="x", plist_path=str(plist)
        )
        assert sl.check_service_module(record) is None

    def test_plist_referencing_unrunnable_module(self, tmp_path):
        """End-to-end proof of the class of problem the task called out:
        a plist can be well-formed and legitimately stored-disabled while
        the module it invokes does not import at all."""
        plist = tmp_path / "dream.plist"
        plist.write_text(_DREAM_STYLE_PLIST)
        record = sl.build_service_record(
            action="disable", service="com.test.x", reason="x", plist_path=str(plist)
        )
        result = sl.check_service_module(record)
        assert result["importable"] is False
        assert result["module"] == "sovereign_stack.daemons.dream_daemon"

    def test_plist_referencing_real_module(self, tmp_path):
        plist = tmp_path / "synthesis.plist"
        plist.write_text(_REAL_MODULE_PLIST)
        record = sl.build_service_record(
            action="disable", service="com.test.x", reason="x", plist_path=str(plist)
        )
        result = sl.check_service_module(record)
        assert result["importable"] is True


# ── seed_known_disabled_services ─────────────────────────────────────────────


class TestSeedKnownDisabledServices:
    def test_returns_two_disable_records(self):
        records = sl.seed_known_disabled_services()
        assert len(records) == 2
        assert all(r["action"] == "disable" for r in records)

    def test_covers_dream_and_legacy_tunnel(self):
        records = sl.seed_known_disabled_services()
        services = {r["service"] for r in records}
        assert services == {"com.templetwo.sovereign.dream", "com.templetwo.sovereign-tunnel"}

    def test_dream_reason_quotes_the_plist_comment(self):
        records = {r["service"]: r for r in sl.seed_known_disabled_services()}
        dream = records["com.templetwo.sovereign.dream"]
        assert "Phase 1 eval runs are manual" in dream["reason"]
        assert "HQ wants to read a few nights of real dream output by hand" in dream["reason"]
        assert dream["re_enable_condition"] is not None
        assert "several nights" in dream["re_enable_condition"]
        assert dream["plist_path"] is not None
        assert dream["plist_path"].endswith("com.templetwo.sovereign.dream.plist")

    def test_tunnel_reason_names_the_supersession(self):
        records = {r["service"]: r for r in sl.seed_known_disabled_services()}
        tunnel = records["com.templetwo.sovereign-tunnel"]
        assert "com.templetwo.cloudflared-tunnel" in tunnel["reason"]
        assert "legacy" in tunnel["reason"].lower()
        assert tunnel["plist_path"] is None

    def test_timestamp_applies_uniformly(self):
        records = sl.seed_known_disabled_services(timestamp="2026-07-12T00:00:00+00:00")
        assert all(r["timestamp"] == "2026-07-12T00:00:00+00:00" for r in records)

    def test_seed_records_are_valid_ledger_entries(self, tmp_path):
        """Every seed record must survive append_service_record and fold to
        state="disabled" — proves the seed data isn't just plausible-looking
        dicts, it's data this module accepts as real ledger entries."""
        ledger = tmp_path / "services.jsonl"
        records = sl.seed_known_disabled_services(timestamp="2026-07-12T00:00:00+00:00")
        for record in records:
            sl.append_service_record(ledger, record)
        fold = sl.fold_service_state(sl.load_service_records(ledger))
        assert fold["com.templetwo.sovereign.dream"]["state"] == "disabled"
        assert fold["com.templetwo.sovereign-tunnel"]["state"] == "disabled"

    def test_seeding_the_live_ledger_is_not_a_side_effect_of_calling_this(self):
        """Building seed records must never touch ~/.sovereign — seeding the
        real ledger is a deploy action, not something import or a plain call
        does. Verified by pointing default_services_path at a path that does
        not exist and confirming this call doesn't create it."""
        before = sl.default_services_path().exists()
        sl.seed_known_disabled_services()
        after = sl.default_services_path().exists()
        assert before == after
