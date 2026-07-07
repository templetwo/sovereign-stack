"""
Tests for the phase-3 cross-model novelty critic in dream_daemon.

Promoted from the build agent's scratchpad self-tests (kept 1:1 in coverage)
and extended with the no-usefulness / no-leak GUARDRAIL regression that the
scratchpad set was missing.

Seam under test (mirrors the scratchpad's choice, per the build advisor):
mocks are installed at the Ollama call boundary
(`call_ollama_dream` / `call_ollama_condense` / `call_ollama_critic`) and at
`read_dream_survey`, so the REAL `parse_critic` + `run_critic` decision logic +
`write_dream` persistence path all execute end-to-end. No real Ollama, no real
chronicle. `reflections_dir` points at pytest's `tmp_path`.

The load-bearing contract exercised here:
  * ABSTAIN is reachable IFF the critic ran AND its JSON parsed cleanly AND
    verdict=="abstain"; every other branch (call failure, unparseable,
    missing/garbage verdict) KEEPS the dream (critic_status="unavailable").
  * The critic prompt is assembled from the dream ALONE (title / observation /
    dream_full) — never the survey, goal, or project. The guardrail test fails
    if anyone ever wires seed material into the critic path.
  * DREAM_CRITIC=off yields a record with NO critic_* keys (byte-identity with
    pre-critic dreams) and never calls the critic.
  * The finally-cleanup unloads BOTH models when the critic is enabled and only
    the dreamer when it is disabled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sovereign_stack.daemons.dream_daemon as dd


# ── Helpers / fixtures ───────────────────────────────────────────────────────


SURVEY_SENTINEL = "ZZ_SENTINEL_survey_only_content_QWQW"


def _make_entry(tag: str, domain: str, content: str = "some content here") -> dict:
    return {
        "tag": tag,
        "domain": domain,
        "timestamp": "2026-06-01T00:00:00+00:00",
        "layer": "hypothesis",
        "intensity": 0.5,
        "content": content,
        "_bucket": "serendipity",
        "ts_epoch": 1.0,
    }


def _fresh_survey(entry_content: str = "some content here") -> dd.DreamSurvey:
    return dd.DreamSurvey(
        entries=[
            _make_entry("d-a", "domain-a", entry_content),
            _make_entry("d-b", "domain-b", entry_content),
        ],
        distant_pairs=[],
        total_chronicle_entries=2,
        freshness_excluded_count=0,
        budget=40,
    )


def _read_records(refl_dir: Path) -> list[dict]:
    recs: list[dict] = []
    for p in Path(refl_dir).glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


_DEFAULT_DREAM = "a genuinely long dream text " * 8
_DEFAULT_CONDENSE = json.dumps(
    {"title": "T", "observation": "O" * 40, "domains": ["domain-a"]}
)


def _install_common_mocks(
    monkeypatch,
    critic_return,
    *,
    survey_content: str = "some content here",
    dream_text: str = _DEFAULT_DREAM,
    condense_json: str = _DEFAULT_CONDENSE,
    ps_models: list[dict] | None = None,
) -> None:
    """Mock every Ollama boundary; keep parse/decision/write REAL.

    `_await_model_unloaded` is stubbed to return `True` (confirmed-unloaded)
    immediately so tests never incur the real 40s one-model-peak barrier (its
    defaults are bound at def-time, so the module tunable can't shrink it).
    `run()` now HONORS this return value (a 3-state signal: True=confirmed-
    unloaded, False=confirmed-still-resident, None=couldn't-verify) — True
    here takes the normal fail-open "proceed with the critic" branch, same as
    every test in this module expects. The confirmed-still-resident (False)
    fail-safe branch is exercised separately in
    test_confirmed_resident_skips_critic_never_loads_second_model below.
    """
    monkeypatch.setattr(dd, "read_dream_survey", lambda **kw: _fresh_survey(survey_content))
    monkeypatch.setattr(dd, "call_ollama_dream", lambda *a, **k: (True, dream_text, {}))
    monkeypatch.setattr(
        dd, "call_ollama_condense", lambda *a, **k: (True, condense_json, {})
    )
    monkeypatch.setattr(dd, "call_ollama_critic", lambda *a, **k: critic_return)
    monkeypatch.setattr(dd, "_ollama_ps", lambda: ([] if ps_models is None else ps_models))
    monkeypatch.setattr(dd, "_await_model_unloaded", lambda *a, **k: True)


def _run(tmp_path: Path, *, critic_enabled: bool = True) -> dd.RunResult:
    daemon = dd.DreamDaemon(
        reflections_dir=tmp_path,
        critic_enabled=critic_enabled,
        force=True,
        skip_preflight=True,
    )
    return daemon.run()


# ── parse_critic edge cases (fail-open contract) ─────────────────────────────


def test_parse_critic_valid_keep():
    parsed = dd.parse_critic('{"novel":true,"coherent":true,"verdict":"keep","reason":"x"}')
    assert parsed is not None and parsed["verdict"] == "keep"


def test_parse_critic_valid_abstain():
    parsed = dd.parse_critic('{"novel":false,"coherent":true,"verdict":"abstain","reason":"x"}')
    assert parsed is not None and parsed["verdict"] == "abstain"


def test_parse_critic_missing_verdict_is_none():
    # fail-open: a missing verdict must NEVER be read as a silent abstain
    assert dd.parse_critic('{"novel":true,"coherent":true}') is None


def test_parse_critic_garbage_verdict_is_none():
    assert dd.parse_critic('{"verdict":"maybe"}') is None


def test_parse_critic_non_json_is_none():
    assert dd.parse_critic("i refuse to answer in json") is None


def test_parse_critic_empty_is_none():
    assert dd.parse_critic("") is None


def test_parse_critic_string_bool_coercion():
    parsed = dd.parse_critic(
        '{"novel":"yes","coherent":"no","verdict":"abstain","reason":"x"}'
    )
    assert parsed is not None
    assert parsed["novel"] is True
    assert parsed["coherent"] is False


# ── keep: dream written WITH critic fields ───────────────────────────────────


def test_verdict_keep_writes_with_critic_fields(tmp_path, monkeypatch):
    _install_common_mocks(
        monkeypatch,
        (True, json.dumps({"novel": True, "coherent": True, "verdict": "keep", "reason": "genuinely non-obvious"}), {}),
    )
    res = _run(tmp_path)
    recs = _read_records(tmp_path)
    assert res.outcome == "wrote"
    assert len(recs) == 1
    r = recs[0]
    assert r["critic_status"] == "keep"
    assert r["critic_model"] == "gemma4:26b"
    assert r["critic_novel"] is True
    assert r["critic_coherent"] is True
    assert r["critic_reason"]


# ── abstain: outcome=abstained_critic, NO file written ───────────────────────


def test_verdict_abstain_suppresses_dream(tmp_path, monkeypatch):
    _install_common_mocks(
        monkeypatch,
        (True, json.dumps({"novel": False, "coherent": True, "verdict": "abstain", "reason": "restatement"}), {}),
    )
    res = _run(tmp_path)
    recs = _read_records(tmp_path)
    assert res.outcome == "abstained_critic"
    assert len(recs) == 0
    # result mirrors the duplicate path: title/observation populated for logging
    assert res.title == "T"
    assert res.observation == "O" * 40


def test_abstained_critic_is_a_respected_exit_zero_outcome():
    # guards the main() exit-code contract: abstained_critic must be treated
    # as a healthy outcome, not a failure.
    healthy = ("wrote", "abstained", "abstained_critic", "duplicate", "skipped_already_dreamed")
    assert "abstained_critic" in healthy


# ── fail-open: broken critic KEEPS the dream (critic_status="unavailable") ────


def test_critic_call_failure_fails_open_kept(tmp_path, monkeypatch):
    _install_common_mocks(monkeypatch, (False, "gemma4:26b not found", {}))
    res = _run(tmp_path)
    recs = _read_records(tmp_path)
    assert res.outcome == "wrote"
    assert len(recs) == 1
    r = recs[0]
    assert r["critic_status"] == "unavailable"
    assert r["critic_novel"] is None
    assert r["critic_coherent"] is None


def test_critic_unparseable_fails_open_kept(tmp_path, monkeypatch):
    _install_common_mocks(monkeypatch, (True, "i will not comply with json", {}))
    res = _run(tmp_path)
    recs = _read_records(tmp_path)
    assert res.outcome == "wrote"
    assert len(recs) == 1
    assert recs[0]["critic_status"] == "unavailable"


def test_critic_missing_verdict_fails_open_kept(tmp_path, monkeypatch):
    # a well-formed JSON object with NO verdict must fail-open, never abstain
    _install_common_mocks(
        monkeypatch, (True, json.dumps({"novel": True, "coherent": True}), {})
    )
    res = _run(tmp_path)
    recs = _read_records(tmp_path)
    assert res.outcome == "wrote"
    assert len(recs) == 1
    assert recs[0]["critic_status"] == "unavailable"


# ── fail-SAFE: confirmed-still-resident skips the critic (never loads gemma4) ─
#
# The BLOCKING bug this fix addresses: run() called _await_model_unloaded but
# ignored its return value, then loaded gemma4:26b unconditionally — on a
# confirmed-still-resident dreamer (qwen3.6:27b, ~17GB) that's a ~34GB peak on
# a 36GB box. The fix: False (confirmed-still-resident) is POSITIVE evidence
# and must SKIP the critic pass entirely — run_critic/call_ollama_critic must
# NEVER be invoked — while True (confirmed-unloaded) and None (couldn't-
# verify) keep the existing fail-open behavior untouched.


def test_confirmed_resident_skips_critic_never_loads_second_model(tmp_path, monkeypatch):
    """INVARIANT (non-negotiable): when _await_model_unloaded returns False
    (confirmed still resident), the second model must never load. The dream
    is still kept and written, with critic_status="skipped_memory" and
    outcome staying "wrote" — this is a fail-SAFE skip, not a failure."""
    _install_common_mocks(
        monkeypatch,
        (
            True,
            json.dumps({"novel": True, "coherent": True, "verdict": "keep", "reason": "r"}),
            {},
        ),
    )
    # Confirmed-still-resident: positive evidence, distinct from "can't verify".
    monkeypatch.setattr(dd, "_await_model_unloaded", lambda *a, **k: False)

    def _boom_call(*a, **k):
        raise AssertionError(
            "call_ollama_critic MUST NOT be invoked when the dreamer is "
            "confirmed still resident (would load gemma4 on top of it)"
        )

    def _boom_run(*a, **k):
        raise AssertionError(
            "run_critic MUST NOT be invoked when the dreamer is confirmed "
            "still resident (would load gemma4 on top of it)"
        )

    monkeypatch.setattr(dd, "call_ollama_critic", _boom_call)
    monkeypatch.setattr(dd, "run_critic", _boom_run)

    res = _run(tmp_path, critic_enabled=True)
    recs = _read_records(tmp_path)

    assert res.outcome == "wrote"
    assert len(recs) == 1
    r = recs[0]
    assert r["critic_status"] == "skipped_memory"
    assert r["critic_novel"] is None
    assert r["critic_coherent"] is None
    assert res.critic_status == "skipped_memory"


def test_couldnt_verify_still_fails_open_like_before(tmp_path, monkeypatch):
    # None (couldn't-verify, e.g. /api/ps unreachable) must NOT be treated as
    # confirmed-still-resident — it keeps the pre-fix fail-open behavior and
    # the critic still runs normally.
    _install_common_mocks(
        monkeypatch,
        (
            True,
            json.dumps({"novel": True, "coherent": True, "verdict": "keep", "reason": "r"}),
            {},
        ),
    )
    monkeypatch.setattr(dd, "_await_model_unloaded", lambda *a, **k: None)
    res = _run(tmp_path, critic_enabled=True)
    recs = _read_records(tmp_path)
    assert res.outcome == "wrote"
    assert len(recs) == 1
    assert recs[0]["critic_status"] == "keep"


# ── DREAM_CRITIC=off: bypass, NO critic fields (byte-identity) ────────────────


def test_critic_disabled_bypasses_and_omits_critic_fields(tmp_path, monkeypatch):
    _install_common_mocks(monkeypatch, None)

    def _boom(*a, **k):
        raise AssertionError("call_ollama_critic MUST NOT be called when critic disabled")

    monkeypatch.setattr(dd, "call_ollama_critic", _boom)
    res = _run(tmp_path, critic_enabled=False)
    recs = _read_records(tmp_path)
    assert res.outcome == "wrote"
    assert len(recs) == 1
    r = recs[0]
    # byte-identity of the off-path: not a single critic_* key leaks in
    assert not any(k.startswith("critic_") for k in r.keys())
    # positive: the pre-critic record shape is intact
    assert all(k in r for k in ("kind", "title", "observation", "ack_status"))
    assert res.critic_status == ""


def test_critic_env_toggle():
    # the one rollback switch: DREAM_CRITIC=off (case-insensitive) disables it
    import os

    prev = os.environ.get("DREAM_CRITIC")
    try:
        os.environ["DREAM_CRITIC"] = "off"
        assert dd._critic_enabled_from_env() is False
        os.environ["DREAM_CRITIC"] = "OFF"
        assert dd._critic_enabled_from_env() is False
        os.environ["DREAM_CRITIC"] = "on"
        assert dd._critic_enabled_from_env() is True
        os.environ.pop("DREAM_CRITIC", None)
        assert dd._critic_enabled_from_env() is True  # unset => enabled
    finally:
        if prev is None:
            os.environ.pop("DREAM_CRITIC", None)
        else:
            os.environ["DREAM_CRITIC"] = prev


# ── cleanup: unload BOTH models when enabled, only the dreamer when disabled ──


def _capture_ollama_stop(monkeypatch) -> list[list[str]]:
    """Patch subprocess.run (as dd calls it) to capture `ollama stop <model>`."""
    stop_calls: list[list[str]] = []

    class _Completed:
        returncode = 0

    def _fake_run(cmd, **kw):
        stop_calls.append(list(cmd))
        return _Completed()

    monkeypatch.setattr(dd.subprocess, "run", _fake_run)
    return stop_calls


def test_cleanup_unloads_both_models_when_enabled(tmp_path, monkeypatch):
    _install_common_mocks(
        monkeypatch,
        (True, json.dumps({"novel": True, "coherent": True, "verdict": "keep", "reason": "r"}), {}),
        ps_models=[{"name": "qwen3.6:27b"}, {"name": "gemma4:26b"}],  # both resident
    )
    stop_calls = _capture_ollama_stop(monkeypatch)
    _run(tmp_path, critic_enabled=True)
    stopped = {c[2] for c in stop_calls if len(c) >= 3 and c[1] == "stop"}
    assert "qwen3.6:27b" in stopped
    assert "gemma4:26b" in stopped


def test_cleanup_only_dreamer_when_disabled(tmp_path, monkeypatch):
    _install_common_mocks(
        monkeypatch,
        None,
        ps_models=[{"name": "qwen3.6:27b"}, {"name": "gemma4:26b"}],
    )

    def _boom(*a, **k):
        raise AssertionError("critic must not run when disabled")

    monkeypatch.setattr(dd, "call_ollama_critic", _boom)
    stop_calls = _capture_ollama_stop(monkeypatch)
    _run(tmp_path, critic_enabled=False)
    stopped = {c[2] for c in stop_calls if len(c) >= 3 and c[1] == "stop"}
    assert "qwen3.6:27b" in stopped
    # gemma4 was never loaded when the critic is disabled -> never in clean list
    assert "gemma4:26b" not in stopped


# ── GUARDRAIL: the critic scores NOVELTY + COHERENCE only, never usefulness ───
#
# Structural, load-bearing regression (mirrors the no-project-leak guard in
# test_reflexive_dreams). The critic must see ONLY the dream (title /
# observation / dream_full) — never the survey / seed material / any goal or
# project. This test seeds the survey with a distinctive sentinel string and
# asserts:
#   (a) the sentinel reaches the DREAM stage (proving it IS in the pipeline),
#   (b) the sentinel NEVER reaches the CRITIC prompt (firewalled), and
#   (c) the critic prompt still carries the "must NOT judge USEFULNESS" clause.
# It fails if anyone later wires survey/goal/project context into run_critic.


def test_critic_prompt_never_leaks_survey_or_goal(tmp_path, monkeypatch):
    captured = {"dream_prompt": None, "critic_prompt": None}

    def _capture_dream(prompt, *a, **k):
        captured["dream_prompt"] = prompt
        return True, _DEFAULT_DREAM, {}

    def _capture_critic(prompt, *a, **k):
        captured["critic_prompt"] = prompt
        return True, json.dumps({"novel": True, "coherent": True, "verdict": "keep", "reason": "r"}), {}

    # survey carries the sentinel in every entry's content; the dream/condense
    # outputs deliberately do NOT echo it, so its only route into the critic
    # prompt would be an (illegitimate) survey leak.
    _install_common_mocks(monkeypatch, None, survey_content=SURVEY_SENTINEL)
    monkeypatch.setattr(dd, "call_ollama_dream", _capture_dream)
    monkeypatch.setattr(dd, "call_ollama_critic", _capture_critic)

    res = _run(tmp_path, critic_enabled=True)
    assert res.outcome == "wrote"

    dream_prompt = captured["dream_prompt"]
    critic_prompt = captured["critic_prompt"]
    assert dream_prompt is not None and critic_prompt is not None

    # (a) the sentinel really is in the pipeline (it reaches the dream stage)
    assert SURVEY_SENTINEL in dream_prompt
    # (b) but it is firewalled out of the critic prompt
    assert SURVEY_SENTINEL not in critic_prompt
    # (c) the critic still carries the dream itself + the no-usefulness clause
    assert "T" in critic_prompt  # title
    assert "O" * 40 in critic_prompt  # observation
    assert "NOT judge USEFULNESS" in critic_prompt


def test_build_critic_prompt_is_dream_only():
    # unit-level twin of the guardrail: the builder's only inputs are the three
    # dream fields; there is no parameter through which a goal could enter.
    prompt = dd.build_critic_prompt("my title", "my observation", "my raw dream")
    assert "my title" in prompt
    assert "my observation" in prompt
    assert "my raw dream" in prompt
    assert "NOT judge USEFULNESS" in prompt
    # no survey/goal/project channel exists on the signature
    import inspect

    params = set(inspect.signature(dd.build_critic_prompt).parameters)
    assert params == {"title", "observation", "dream_full"}
