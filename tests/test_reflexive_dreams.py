"""
Tests for the resonant-dreams delivery rail (phase 2, feat/dream-layer).

Covers the 5th ReflexiveSurface bucket (`resonant_dreams`), its scoring
helper `_score_and_sort_dreams`, the PerTurnPriors dream section
(DREAM_K=1, anti-repeat, full_content + fallibility label), and the
reader-level `reflections.list_dreams` age/ack filtering.

Two seams matter and are exercised deliberately:

  * `ReflexiveSurface.surface()` reads dreams from
    `self.sovereign_root / "reflections"` via an EXPLICIT path — NOT the
    module-level `reflections.REFLECTIONS_DIR`. So every integration test
    through surface()/inject() writes dream jsonl into
    `sovereign_root/reflections`. Patching REFLECTIONS_DIR would leave the
    bucket empty and pass vacuously. REFLECTIONS_DIR is patched ONLY for the
    direct `list_dreams` reader tests, per the module docstring convention.

  * The fail-soft wrapper in surface() only fires when the dream read
    RAISES. A missing dir / no dreams returns [] WITHOUT entering the except
    block, so those don't cover the guard. The fail-soft tests monkeypatch
    the name `sovereign_stack.reflexive.list_dreams` (the imported binding in
    reflexive's namespace) to raise.

The guardrail under test: a dream's score must depend on its OWN domain
tags + novelty ONLY — never on the caller's project/goal string. The
no-project-leak test is written to FAIL if `project=None` is reverted to
`project` in `_score_and_sort_dreams`.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack.reflections import list_dreams
from sovereign_stack.reflexive import PerTurnPriors, ReflexiveSurface


# ── Helpers / fixtures ───────────────────────────────────────────────────────


def _write_dream(
    reflections_dir: Path,
    *,
    did: str,
    domains: list[str],
    observation: str = "a novel connection between two distant entries",
    title: str = "dream title",
    ack_status: str = "unread",
    timestamp: datetime | str | None = None,
    dream_full: str = "",
) -> Path:
    """Append one dream record in dream_daemon.write_dream() shape.

    Returns the file path. File is named YYYY-MM-DD.jsonl from the
    timestamp, matching the daemon's on-disk layout.
    """
    ts = timestamp if timestamp is not None else datetime.now(timezone.utc)
    if isinstance(ts, datetime):
        ts_iso = ts.isoformat()
        day = ts.strftime("%Y-%m-%d")
    else:
        ts_iso = ts
        day = str(ts)[:10]
    path = reflections_dir / f"{day}.jsonl"
    record = {
        "id": did,
        "kind": "dream",
        "timestamp": ts_iso,
        "model": "claude-sonnet-4-6",
        "prompt_version": "v-test",
        "run_id": "test-run",
        "title": title,
        "observation": observation,
        "dream_full": dream_full or observation,
        "domains": list(domains),
        "connection_type": "dream",
        "confidence": "low",
        "entries_referenced": [],
        "ack_status": ack_status,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


@pytest.fixture
def dream_env():
    """A sovereign root with chronicle/, handoffs/, reflections/ dirs.

    Yields (sovereign_root, reflections_dir). The reflections_dir is the
    real path surface() reads from — write dreams there.
    """
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "chronicle").mkdir()
    (root / "handoffs").mkdir()
    refl = root / "reflections"
    refl.mkdir()
    yield root, refl
    shutil.rmtree(tmp, ignore_errors=True)


def _surface(root: Path) -> ReflexiveSurface:
    return ReflexiveSurface(sovereign_root=root)


def _priors(root: Path) -> PerTurnPriors:
    return PerTurnPriors(surface=_surface(root), sovereign_root=root)


def _dream_by_id(dreams: list[dict], did: str) -> dict | None:
    for d in dreams:
        if d.get("id") == did:
            return d
    return None


# ── Reader-level: list_dreams age + ack filtering (patch REFLECTIONS_DIR) ─────


class TestListDreamsReader:
    def test_ack_excluded_at_reader_level(self, tmp_path, monkeypatch):
        """Default ack_statuses=(unread, engage): confirm/discard never
        reach any caller. A settled dream stops pushing at the reader."""
        refl = tmp_path / "reflections"
        refl.mkdir()
        _write_dream(refl, did="d_unread", domains=["compass"], ack_status="unread")
        _write_dream(refl, did="d_engage", domains=["compass"], ack_status="engage")
        _write_dream(refl, did="d_confirm", domains=["compass"], ack_status="confirm")
        _write_dream(refl, did="d_discard", domains=["compass"], ack_status="discard")

        monkeypatch.setattr("sovereign_stack.reflections.REFLECTIONS_DIR", refl)
        ids = {d["id"] for d in list_dreams()}
        assert ids == {"d_unread", "d_engage"}

    def test_age_filter_drops_old_dreams(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        refl = tmp_path / "reflections"
        refl.mkdir()
        _write_dream(refl, did="d_fresh", domains=["compass"], timestamp=now)
        _write_dream(
            refl, did="d_old", domains=["compass"], timestamp=now - timedelta(days=100)
        )
        monkeypatch.setattr("sovereign_stack.reflections.REFLECTIONS_DIR", refl)
        ids = {d["id"] for d in list_dreams(max_age_days=45)}
        assert ids == {"d_fresh"}

    def test_non_dream_reflection_ignored(self, tmp_path, monkeypatch):
        refl = tmp_path / "reflections"
        refl.mkdir()
        _write_dream(refl, did="d_dream", domains=["compass"])
        # A plain (non-dream) reflection line in the same tree.
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with (refl / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "id": "r_plain",
                        "kind": "reflection",
                        "connection_type": "convergence",
                        "observation": "not a dream",
                        "ack_status": "unread",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
                + "\n"
            )
        monkeypatch.setattr("sovereign_stack.reflections.REFLECTIONS_DIR", refl)
        ids = {d["id"] for d in list_dreams()}
        assert ids == {"d_dream"}

    def test_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "sovereign_stack.reflections.REFLECTIONS_DIR", tmp_path / "nope"
        )
        assert list_dreams() == []


# ── surface() bucket-5 scoring: domain-match, exclude, unread-boost, no leak ──


class TestSurfaceDreamScoring:
    def test_domain_match_surfaces_dream(self, dream_env):
        root, refl = dream_env
        _write_dream(refl, did="d1", domains=["compass"])
        out = _surface(root).surface(domain_tags=["compass"])
        ids = {d["id"] for d in out["resonant_dreams"]}
        assert "d1" in ids

    def test_off_domain_dream_excluded(self, dream_env):
        """Strict zero-overlap drop: a dream in untagged territory is
        dropped even though it is fresh and unread."""
        root, refl = dream_env
        _write_dream(refl, did="d_match", domains=["compass"])
        _write_dream(refl, did="d_off", domains=["biology"])
        out = _surface(root).surface(domain_tags=["compass"])
        ids = {d["id"] for d in out["resonant_dreams"]}
        assert ids == {"d_match"}

    def test_unread_boost_is_half_point_after_overlap_filter(self, dream_env):
        """+0.5 while unread, applied AFTER the overlap filter. Same
        domain + same day → recency identical → the boost is the ONLY
        difference, so the delta is exactly 0.5."""
        root, refl = dream_env
        now = datetime.now(timezone.utc)
        _write_dream(
            refl, did="d_unread", domains=["compass"], ack_status="unread", timestamp=now
        )
        _write_dream(
            refl, did="d_engage", domains=["compass"], ack_status="engage", timestamp=now
        )
        out = _surface(root).surface(domain_tags=["compass"])
        du = _dream_by_id(out["resonant_dreams"], "d_unread")
        de = _dream_by_id(out["resonant_dreams"], "d_engage")
        assert du is not None and de is not None
        assert du["_score"] - de["_score"] == pytest.approx(0.5)

    def test_no_project_leak_in_dream_score(self, dream_env):
        """THE GUARDRAIL. Two dreams identical in domain / ack / day; the
        only difference is dream A's observation name-drops the caller's
        project string (distinct from the domain tag, so tag_overlap is
        untouched). Scores MUST be equal — project_match is withheld for
        dreams. This FAILS if `project=None` is reverted to `project` in
        _score_and_sort_dreams (dream A would gain a +0.5 on-task bonus)."""
        root, refl = dream_env
        now = datetime.now(timezone.utc)
        _write_dream(
            refl,
            did="d_namedrop",
            domains=["compass"],
            observation="this dream is all about the phasegpt effort",
            timestamp=now,
        )
        _write_dream(
            refl,
            did="d_neutral",
            domains=["compass"],
            observation="an unrelated overnight connection about entropy",
            timestamp=now,
        )
        out = _surface(root).surface(domain_tags=["compass"], project="phasegpt")
        a = _dream_by_id(out["resonant_dreams"], "d_namedrop")
        b = _dream_by_id(out["resonant_dreams"], "d_neutral")
        assert a is not None and b is not None
        assert a["_score"] == b["_score"], (
            "project string leaked into dream score — guardrail broken"
        )

    def test_surface_bucket_not_capped_at_one(self, dream_env):
        """The bucket itself returns up to limit_per_bucket dreams; the
        k=1 cap lives at the priors layer, not here."""
        root, refl = dream_env
        for i in range(3):
            _write_dream(refl, did=f"d{i}", domains=["compass"])
        out = _surface(root).surface(domain_tags=["compass"], limit_per_bucket=5)
        assert len(out["resonant_dreams"]) == 3


# ── PerTurnPriors dream section: DREAM_K=1, anti-repeat, full_content ─────────


class TestPriorsDreamSection:
    def test_dream_k_capped_at_one(self, dream_env):
        """Three matching dreams → exactly one dream section in priors,
        regardless of caller k."""
        root, refl = dream_env
        for i in range(3):
            _write_dream(refl, did=f"d{i}", domains=["compass"])
        result = _priors(root).inject(
            domain_tags=["compass"], k=3, max_tokens=4000, dry_run=True
        )
        dream_sigs = [s for s in result["included_items"] if s.startswith("dream:")]
        assert len(dream_sigs) == 1

    def test_anti_repeat_demotes_on_second_call(self, dream_env):
        """A just-surfaced dream lands in skipped_stale on the next call
        (dream:{id} signature + the freshness window)."""
        root, refl = dream_env
        _write_dream(refl, did="d_solo", domains=["compass"])
        priors = _priors(root)

        first = priors.inject(domain_tags=["compass"], dry_run=False)
        assert "dream:d_solo" in first["included_items"]

        second = priors.inject(domain_tags=["compass"], dry_run=False)
        assert "dream:d_solo" in second["skipped_stale"]
        assert "dream:d_solo" not in second["included_items"]

    def test_full_content_expands_observation_and_keeps_fallible_label(self, dream_env):
        """full_content=True carries the whole observation; the default
        truncates it. The fallibility label is present either way."""
        root, refl = dream_env
        obs = "STARTMARK " + ("filler " * 50) + "ENDMARK"
        _write_dream(refl, did="d_long", domains=["compass"], observation=obs)

        full = _priors(root).inject(
            domain_tags=["compass"], max_tokens=4000, dry_run=True, full_content=True
        )
        truncated = _priors(root).inject(
            domain_tags=["compass"], max_tokens=4000, dry_run=True, full_content=False
        )

        assert "STARTMARK" in full["block"]
        assert "ENDMARK" in full["block"], "full_content must carry the whole observation"

        assert "STARTMARK" in truncated["block"]
        assert "ENDMARK" not in truncated["block"], "default must truncate the observation"

        assert "fallible, machine-generated" in full["block"]
        assert "fallible, machine-generated" in truncated["block"]

    def test_dream_source_reported(self, dream_env):
        root, refl = dream_env
        _write_dream(refl, did="d1", domains=["compass"])
        result = _priors(root).inject(domain_tags=["compass"], dry_run=True)
        assert "dream" in result["sources"]

    def test_no_dreams_without_tags(self, dream_env):
        """Priors only surface dreams inside the tag-scoped section; with
        no tags there is no domain to match, so no dream surfaces."""
        root, refl = dream_env
        _write_dream(refl, did="d1", domains=["compass"])
        result = _priors(root).inject(domain_tags=[], dry_run=True)
        assert not [s for s in result["included_items"] if s.startswith("dream:")]


# ── Fail-soft ────────────────────────────────────────────────────────────────


class TestFailSoft:
    def test_surface_fail_soft_when_list_dreams_raises(self, dream_env, monkeypatch):
        """surface() must not raise if the dream read blows up — the
        bucket degrades to []. Patch the imported binding in reflexive's
        namespace so the call site inside surface() hits the raiser."""
        root, refl = dream_env
        _write_dream(refl, did="d1", domains=["compass"])

        def _boom(*a, **k):
            raise RuntimeError("reflections tree is on fire")

        monkeypatch.setattr("sovereign_stack.reflexive.list_dreams", _boom)
        out = _surface(root).surface(domain_tags=["compass"])
        assert out["resonant_dreams"] == []

    def test_prior_for_turn_fail_soft_when_list_dreams_raises(
        self, dream_env, monkeypatch
    ):
        """inject() must never raise on a dream-read failure."""
        root, refl = dream_env
        _write_dream(refl, did="d1", domains=["compass"])

        def _boom(*a, **k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr("sovereign_stack.reflexive.list_dreams", _boom)
        result = _priors(root).inject(domain_tags=["compass"], dry_run=True)
        assert not [s for s in result["included_items"] if s.startswith("dream:")]

    def test_surface_no_reflections_dir(self, dream_env):
        """A missing reflections dir returns [] cleanly (no raise)."""
        root, refl = dream_env
        shutil.rmtree(refl)
        out = _surface(root).surface(domain_tags=["compass"])
        assert out["resonant_dreams"] == []
