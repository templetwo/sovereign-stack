"""Lived-ground-truth (v1.7.2): receipt-exemption keyed on vantage, the
emotional layer, and the bridge-dispatch path.

Locks the load-bearing guarantees of the lived-ground-truth patch:

  - the THREE lived vantages exempt a 0.9 ground_truth entry from the
    unreceipted-ground-truth nag (nape low honk + season hygiene);
  - NOTHING else exempts — absent vantage, a seat tag, external_web_verified,
    and the deferred model_observation all STILL nag (ruling-1 + ruling-4
    regression guards; that guard is the whole point of the exemption);
  - the emotional fields survive the MCP dispatch (the bridge bug: the
    dispatch dropped un-named metadata) and are stored first-class;
  - emotional_intensity NEVER affects recall ordering (the hard rule);
  - light validation rejects malformed emotional fields.
"""

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from sovereign_stack import seasons
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.nape_daemon import NapeDaemon
from sovereign_stack.provenance import LIVED_VANTAGES
from tests.test_nape_autohook import _isolated_server

SESSION = "lived-gt-test"
GT = "ground_truth"
NAG = "unreceipted_ground_truth"
NO_RECEIPT = "⟁ Insight recorded [ground_truth]: /chronicle/insights/x/s.jsonl"
WITH_RECEIPT = NO_RECEIPT + " (receipts: 1 verified, 0 attested)"


def _nag_honks(daemon):
    return [h for h in daemon.current_honks(SESSION) if h["pattern"] == NAG]


class TestNapeExemption:
    """The nape low honk skips the three lived vantages and nothing else."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.d = NapeDaemon(root=self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, args, result=NO_RECEIPT):
        self.d.observe("record_insight", args, result, SESSION)

    @pytest.mark.parametrize("vantage", sorted(LIVED_VANTAGES))
    def test_lived_vantage_exempts(self, vantage):
        self._record(
            {"layer": GT, "intensity": 0.95, "vantage": vantage,
             "domain": "memory", "content": "grief carried through his father's art"}
        )
        assert _nag_honks(self.d) == [], f"{vantage} should be receipt-exempt"

    def test_absent_vantage_still_nags(self):
        # Ruling-1 regression guard: an unmarked 0.9 ground_truth claim is the
        # default-receipt-expected case and MUST still honk.
        self._record({"layer": GT, "intensity": 0.95, "domain": "x", "content": "X causes Y"})
        assert len(_nag_honks(self.d)) == 1

    @pytest.mark.parametrize(
        "vantage",
        ["external_web_verified", "implementation_verified", "hq_filesystem",
         "bridge_runtime", "model_observation"],
    )
    def test_non_lived_vantage_still_nags(self, vantage):
        # Ruling-4 (seat tags) + ruling-1 (model_observation deferred, NOT exempt):
        # everything outside LIVED_VANTAGES is still held to the receipt expectation.
        self._record(
            {"layer": GT, "intensity": 0.95, "vantage": vantage, "domain": "x", "content": "claim"}
        )
        assert len(_nag_honks(self.d)) == 1, f"{vantage} must NOT be exempt"

    def test_lived_vantage_with_verified_receipt_also_silent(self):
        self._record(
            {"layer": GT, "intensity": 0.95, "vantage": "human_observation",
             "domain": "memory", "content": "x"},
            result=WITH_RECEIPT,
        )
        assert _nag_honks(self.d) == []

    def test_below_sentinel_intensity_never_nags(self):
        self._record({"layer": GT, "intensity": 0.5, "domain": "x", "content": "x"})
        assert _nag_honks(self.d) == []

    def test_model_observation_is_not_in_the_exempt_set(self):
        # Belt-and-suspenders on ruling 1: the constant itself must exclude it.
        assert "model_observation" not in LIVED_VANTAGES
        assert LIVED_VANTAGES == frozenset(
            {"human_observation", "human_attestation", "witnessed_account"}
        )


class TestDispatchStoresEmotion:
    """The MCP dispatch must forward the emotional fields (the bridge bug:
    un-named metadata was dropped before reaching the chronicle)."""

    def test_emotion_fields_survive_dispatch(self):
        with _isolated_server(SESSION) as (srv, tmp_root):
            args = {
                "domain": "memory", "content": "grief account", "layer": GT,
                "intensity": 0.9, "vantage": "human_observation",
                "observed_emotion": ["grief", "protective_love"],
                "emotional_intensity": 0.95,
                "emotion_source": "anthony_declared",
                "emotion_note": "carried through his father's art and resemblance",
            }
            result = asyncio.run(srv._dispatch_tool("record_insight", args))
            assert result and result[0].type == "text"
            files = list((tmp_root / "chronicle" / "insights" / "memory").glob("*.jsonl"))
            assert files, "dispatch wrote no insight under the memory domain"
            entry = json.loads(files[0].read_text().splitlines()[-1])
            assert entry["observed_emotion"] == ["grief", "protective_love"]
            assert entry["emotional_intensity"] == 0.95
            assert entry["emotion_source"] == "anthony_declared"
            assert entry["emotion_note"].startswith("carried through")
            assert entry["vantage"] == "human_observation"


class TestEmotionValidationAndOrdering:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.m = ExperientialMemory(root=str(Path(self.tmp) / "chronicle"))

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bad_emotion_source_rejected(self):
        with pytest.raises(ValueError):
            self.m.record_insight("memory", "x", emotion_source="sad")

    def test_emotional_intensity_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            self.m.record_insight("memory", "x", emotional_intensity=1.5)

    def test_observed_emotion_must_be_list_of_strings(self):
        with pytest.raises(ValueError):
            self.m.record_insight("memory", "x", observed_emotion="grief")

    def test_valid_emotion_fields_stored(self):
        p = self.m.record_insight(
            "memory", "x", layer=GT, observed_emotion=["grief"],
            emotional_intensity=0.9, emotion_source="anthony_declared", emotion_note="n",
        )
        entry = json.loads(Path(str(p)).read_text().splitlines()[-1])
        assert entry["observed_emotion"] == ["grief"]
        assert entry["emotional_intensity"] == 0.9
        assert entry["emotion_source"] == "anthony_declared"

    def test_emotional_intensity_never_changes_recall_order(self):
        # Hard rule: surfacing is governed by operational intensity / timestamp,
        # never by emotional_intensity. Older entry carries the HIGHER felt-weight.
        self.m.record_insight(
            "memory", "older", layer=GT, emotional_intensity=1.0,
            vantage="human_observation", session_id="s1",
        )
        time.sleep(0.01)
        self.m.record_insight(
            "memory", "newer", layer=GT, emotional_intensity=0.0,
            vantage="human_observation", session_id="s2",
        )
        results = self.m.recall_insights(domain="memory", limit=10)
        assert results[0]["content"] == "newer", "recall must stay timestamp-ordered"


class TestSeasonHygieneExemption:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "chronicle"
        self.m = ExperientialMemory(root=str(self.root))

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _review(self):
        # Isolate policies/families to tmp (absent files read as empty) so the
        # pass never touches the live registry.
        return seasons.season_review(
            chronicle_root=str(self.root),
            policies_path=str(Path(self.tmp) / "policies.jsonl"),
            families_path=str(Path(self.tmp) / "families.jsonl"),
        )

    def test_lived_sentinel_exempt_nonlived_flagged(self):
        self.m.record_insight(
            "memory", "grief account", layer=GT, intensity=0.95,
            vantage="human_observation", session_id="lived",
        )
        assert "unreceipted ground_truth sentinel" not in self._review()

        self.m.record_insight(
            "research", "X causes Y", layer=GT, intensity=0.95,
            vantage="external_web_verified", session_id="tech",
        )
        assert "unreceipted ground_truth sentinel" in self._review()
