"""
DreamDaemon — Phase 1 of the local "dream" layer. The fourth reflection daemon.

Once a night, on a local Ollama model, this daemon:
  1. Pre-flights (Temple_Core mounted? no memory-pressure conflict?).
  2. Assembles a "lay of the land" survey of the chronicle chosen for
     DISTANCE, not recency — persistent anchors, a spread across the whole
     history, a few recent entries, uniform-random serendipity, open
     questions, and four DISTANT PAIRS (entries that share no tags and are
     weeks/months apart — the novelty engine).
  3. Sends that survey to the model and asks it to dream ONE novel
     connection, freeform (phase 1 — hot, temperature 0.9).
  4. Sends the raw dream back to the still-warm model to condense + tag it
     into a structured record (phase 2 — cool, temperature 0.3, JSON mode).
     keep_alive:0 on this second call is what unloads the DREAMER — the
     unload rides the last request.
  5. Runs a CROSS-MODEL NOVELTY CRITIC on a DIFFERENT model (gemma4:26b) —
     the dreamer never grades its own homework. The critic scores the dream
     on NOVELTY + COHERENCE ONLY (never usefulness/on-task-ness — a dream is
     allowed to be useless and strange). It runs only after
     `_await_model_unloaded` reports the dreamer is NOT confirmed still
     resident (one-model-peak: ~17GB each on a 36GB box), with its own
     keep_alive:0 so the critic unloads too. FAIL-SAFE on POSITIVE evidence:
     if the dreamer is CONFIRMED still resident after the bounded wait, the
     critic is SKIPPED entirely for that run — the second ~17GB model is
     never loaded — and the dream is kept with critic_status="skipped_memory".
     Otherwise (confirmed unloaded, or the barrier couldn't verify) this
     stays FAIL-OPEN as before: a clean verdict=="abstain" is the ONLY thing
     that drops a dream; any critic failure keeps it
     (critic_status="unavailable"). DREAM_CRITIC=off disables the pass
     entirely (rollback to phase-2 behavior).
  6. Writes exactly ONE dream record to ~/.sovereign/reflections/ (with the
     critic fields when the critic was engaged), then verifies BOTH models
     actually unloaded (backstop `ollama stop` for the dreamer AND the critic).

Phase 1 scope, deliberately: no delivery/surfacing wiring. The dream lands
on disk with ack_status="unread" so HQ can read a few nights of real output
before wiring it to reflexive_surface / the rail (phase 2 of the dream
layer, a separate piece of work — this daemon does not touch reflexive.py,
reflections.py, server.py, or any connector/scribe file).

Sibling relationship to synthesis_daemon.py (the interpretive reflector):
same family (writes to the same reflections/ tree, same ack loop via
reflections.ack_reflection — kind="dream" and domains=[] ride through as
"extras" the ack rewrite already preserves), same fail-soft posture (every
failure degrades to a RunResult, never a crash), same architectural break
from BaseDaemon that synthesis_daemon documents (this is generative and
fallible by design, not a notification primitive with a halt breaker).
Deliberately NOT sharing code by inheritance — this module imports the
handful of pure helpers worth reusing directly (see below) rather than
coupling the two daemons' lifecycles.

Reused from synthesis_daemon.py (READ FIRST list in the build spec):
  * _project_entry — wrapped by _project_dream_entry below, which adds the
    intensity field the survey sampler needs and truncates content to the
    survey's tighter ~600-char budget (synthesis truncates at 1800, but
    that's a single 8-entry prompt window; a 40-entry lay-of-the-land needs
    a smaller per-entry allowance).
  * extract_json_block — the condense pass asks for strict JSON; this is
    the same fence/prose/thinking-trace-tolerant extractor synthesis uses.
  * _iso_to_dt — timestamp parsing.
  * The dormant call_ollama path (~synthesis_daemon.py:682-741) — revived
    and adapted here as call_ollama_dream / call_ollama_condense with the
    think/keep_alive top-level fields qwen3.6:27b needs (synthesis's dormant
    version predates that requirement and lacks both).
  * The write-to-reflections shape (synthesis_daemon.py:918-947) — adapted
    in write_dream() below with the dream-specific fields (title,
    dream_full, seed_entries, domains, kind="dream").

read_ack_history (confirmed/discarded pattern feedback) is deliberately
NOT reused here. Two reasons: (1) it has no `kind` filter, so it would pull
synthesis's reflections — a different content type — into the dream
prompt; (2) phase 1's whole point is to observe raw generative output
before conditioning it on anything. Revisit in phase 2 once there is real
dream ack history to feed back.

Mandatory chokepoint: chronicle reads go through
sovereign_stack.memory.load_entries (supersession-safety / reader-
convergence invariant) — never raw jsonl reads. reflexive.py's
_normalize_tags / _compute_tag_overlap are imported read-only for
tag-set-distance math; reflexive.py itself is untouched.
"""

from __future__ import annotations

import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sovereign_stack.daemons.synthesis_daemon import (
    _iso_to_dt,
    _project_entry as _synthesis_project_entry,
    extract_json_block,
)
from sovereign_stack.memory import load_entries
from sovereign_stack.reflexive import _compute_tag_overlap, _normalize_tags

# ── Tunables ────────────────────────────────────────────────────────────────

DREAM_MODEL = os.getenv("DREAM_MODEL", "qwen3.6:27b")
DREAM_PROMPT_VERSION = "dream-v1"
DREAM_NUM_CTX = 32768  # MUST be set explicitly — Ollama silently truncates
# the survey prompt at its (much smaller) default context otherwise. See
# _warn_if_context_truncated below for the prompt_eval_count tripwire.
DEFAULT_DREAM_NUM_PREDICT = 6144
DEFAULT_CONDENSE_NUM_PREDICT = 2048
DREAM_CALL_TIMEOUT_SECONDS = 900
CONDENSE_CALL_TIMEOUT_SECONDS = 300

# ── Phase-3 cross-model novelty critic (gemma4:26b) ─────────────────────────
# A DIFFERENT model grades the qwen dream on NOVELTY + COHERENCE only (never
# usefulness — see CRITIC_PREAMBLE). Cross-model so the dreamer never grades
# its own homework. DREAM_CRITIC=off disables it entirely (rollback switch).
DREAM_CRITIC_MODEL = os.getenv("DREAM_CRITIC_MODEL", "gemma4:26b")
DREAM_CRITIC_PROMPT_VERSION = "critic-v1"
DEFAULT_CRITIC_NUM_PREDICT = 1024  # the verdict JSON is small; a sentence of reason
CRITIC_CALL_TIMEOUT_SECONDS = 300
CRITIC_NUM_CTX_MIN = 4096
CRITIC_NUM_CTX_MAX = DREAM_NUM_CTX  # ceiling; the critic prompt is one dream,
# not a 40-entry survey, so it sizes DOWN from here — but never below what the
# prompt needs (an undersized num_ctx silently truncates, the exact failure
# _warn_if_context_truncated exists to catch).
CRITIC_UNLOAD_WAIT_SECONDS = 40.0  # bounded, fail-open barrier: confirm the
# ~17GB dreamer has unloaded before loading the ~17GB critic (one-model-peak
# invariant on a 36GB box). Expiring the wait does NOT block the dream.
CRITIC_UNLOAD_POLL_INTERVAL = 4.0

MEMORY_PRESSURE_BYTES = 8 * 1024**3  # 8GB — a model already this big resident
# means qwen3.6:27b (18GB) would contend for GPU memory; skip the night.


def _critic_enabled_from_env() -> bool:
    """DREAM_CRITIC=off (case-insensitive) disables the critic; anything else
    (including unset) leaves it enabled. The one rollback switch to current
    Phase-2 behavior."""
    return os.getenv("DREAM_CRITIC", "").strip().lower() != "off"

SOVEREIGN_ROOT = Path(os.path.expanduser("~/.sovereign"))
CHRONICLE_DIR = SOVEREIGN_ROOT / "chronicle"
CHRONICLE_INSIGHTS = CHRONICLE_DIR / "insights"
REFLECTIONS_DIR = SOVEREIGN_ROOT / "reflections"
TEMPLE_CORE_MOUNT = Path("/Volumes/Temple_Core")  # Ollama serves models from
# here since the 2026-06-18 migration; if it's not mounted, model loads fail.

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_GENERATE_URL = os.getenv("OLLAMA_GENERATE_URL", f"{OLLAMA_BASE_URL}/api/generate")
OLLAMA_PS_URL = os.getenv("OLLAMA_PS_URL", f"{OLLAMA_BASE_URL}/api/ps")

# Survey sampler strata — sums to DEFAULT_SURVEY_BUDGET (6+10+6+8+6+4=40).
SURVEY_ANCHOR_COUNT = 6
SURVEY_ANCHOR_MIN_INTENSITY = 0.8
SURVEY_TEMPORAL_EPOCHS = 5
SURVEY_TEMPORAL_PER_EPOCH = 2
SURVEY_TEMPORAL_COUNT = SURVEY_TEMPORAL_EPOCHS * SURVEY_TEMPORAL_PER_EPOCH
SURVEY_RECENT_COUNT = 6
SURVEY_RECENT_WINDOW_HOURS = 72
SURVEY_DISTANT_PAIR_COUNT = 4
SURVEY_DISTANT_CANDIDATE_POOL = 200
SURVEY_DISTANT_MIN_DAY_GAP = 30.0
SURVEY_SERENDIPITY_COUNT = 6
SURVEY_OPEN_THREAD_COUNT = 4
SURVEY_CONTENT_CHARS = 600  # per-entry content budget in the survey (tighter
# than synthesis's 1800 — this prompt carries ~40 entries, not ~8)
DEFAULT_SURVEY_BUDGET = (
    SURVEY_ANCHOR_COUNT
    + SURVEY_TEMPORAL_COUNT
    + SURVEY_RECENT_COUNT
    + 2 * SURVEY_DISTANT_PAIR_COUNT
    + SURVEY_SERENDIPITY_COUNT
    + SURVEY_OPEN_THREAD_COUNT
)

FRESHNESS_LOOKBACK_NIGHTS = 5  # exclude entries seeded in the last N nights
DEDUPE_LOOKBACK_NIGHTS = 14
DEDUPE_JACCARD_THRESHOLD = 0.6


# ── Data shapes ─────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    """Outcome of a single dream run."""

    outcome: (
        str  # "wrote" | "abstained" | "abstained_critic" | "duplicate"
        # | "no_entries" | "model_failed" | "parse_failed"
        # | "skipped_temple_core_unmounted" | "skipped_memory_pressure"
        # | "skipped_already_dreamed" | "skipped"
    )
    details: str = ""
    run_id: str = ""
    model: str = ""
    dream_path: str | None = None
    title: str = ""
    observation: str = ""
    dream_full: str = ""
    survey_entry_count: int = 0
    distant_pair_count: int = 0
    elapsed_seconds: float = 0.0
    # Cross-model critic outcome (empty when critic disabled): "keep" |
    # "abstain" | "unavailable" (fail-open) | "skipped_memory" (fail-safe: the
    # dreamer was CONFIRMED still resident, so the critic was never invoked).
    # novel/coherent are None whenever the critic didn't actually run/parse.
    critic_status: str = ""
    critic_novel: bool | None = None
    critic_coherent: bool | None = None
    critic_reason: str = ""


@dataclass
class DreamSurvey:
    """The stratified "lay of the land" sample handed to the dream prompt."""

    entries: list[dict]  # every selected entry, each tagged "_bucket"
    distant_pairs: list[dict]  # [{"a": entry, "b": entry, "jaccard": float, "day_gap": float}, ...]
    total_chronicle_entries: int
    freshness_excluded_count: int
    budget: int


@dataclass
class CriticResult:
    """Verdict from the cross-model novelty critic.

    status is the load-bearing field: "abstain" is the ONLY value that
    suppresses a dream, and it is reachable ONLY from a cleanly-parsed
    verdict=="abstain". "keep" writes with the critic fields; "unavailable"
    (fail-open) also writes — a broken critic must never silently kill dreams.
    "skipped_memory" (fail-safe) also writes — it means the critic was never
    invoked at all because the dreamer was CONFIRMED still resident (positive
    evidence of a two-model memory peak), not that it ran and failed.
    novel/coherent are None whenever the critic didn't actually run/parse.
    """

    status: str  # "keep" | "abstain" | "unavailable"
    novel: bool | None = None
    coherent: bool | None = None
    reason: str = ""
    model: str = ""


# ── Chronicle projection (extends synthesis_daemon._project_entry) ──────────


def _project_dream_entry(rec: dict, ts_epoch: float) -> dict:
    """
    Project a raw chronicle record for the dream survey.

    Wraps synthesis_daemon._project_entry (same base fields: timestamp,
    domain, layer, content, tag, session_id, ts_epoch, optional
    _superseded_by carried through unchanged) and adds what the sampler
    needs on top: a clamped intensity float (chronicle data has at least
    one out-of-range outlier observed live — clamp defensively so a bad
    data point can't dominate intensity-weighted sampling) and content
    truncated to the survey's tighter per-entry budget.
    """
    entry = _synthesis_project_entry(rec, ts_epoch)
    entry["intensity"] = _clamp_intensity(rec.get("intensity"))
    content = entry.get("content", "")
    if len(content) > SURVEY_CONTENT_CHARS:
        entry["content"] = content[:SURVEY_CONTENT_CHARS] + " […truncated]"
    return entry


def _clamp_intensity(value: object) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


def _load_all_projected(chronicle_root: Path) -> list[dict]:
    """
    Load every chronicle entry through the mandatory load_entries chokepoint
    and project it. `chronicle_root` is a chronicle's insights/ directory
    (matching synthesis_daemon's read_recent_chronicle convention) —
    load_entries wants its parent (the supersession ledger lives beside it).
    """
    if not chronicle_root.exists():
        return []
    out: list[dict] = []
    for rec in load_entries(chronicle_root.parent, with_sources=True):
        ts = _iso_to_dt(rec.get("timestamp", ""))
        if ts is None:
            continue
        out.append(_project_dream_entry(rec, ts.timestamp()))
    return out


def _seed_key(entry: dict) -> tuple[str, str]:
    """Identity used for cross-night freshness exclusion, per spec shape."""
    return (entry.get("tag", "?"), entry.get("domain", "?"))


# ── Cross-night freshness exclusion ──────────────────────────────────────────


def _recent_dream_seed_keys(
    reflections_dir: Path, nights: int = FRESHNESS_LOOKBACK_NIGHTS, before: date | None = None
) -> set[tuple[str, str]]:
    """
    (tag, domain) pairs that were seed_entries in dream records from the
    `nights` calendar days strictly before `before` (today, by default).
    Coarse by construction — tag is the domain-directory name (potentially
    shared by many entries), so this excludes at domain-dir granularity, not
    single-entry granularity. That's the literal shape the write spec asks
    for (seed_entries as {tag, domain}); flagged here for HQ rather than
    silently sharpened, since a stricter key would diverge from what
    tonight's own record actually persists.
    """
    keys: set[tuple[str, str]] = set()
    if not reflections_dir.exists():
        return keys
    today = before or datetime.now(timezone.utc).date()
    for i in range(1, nights + 1):
        path = reflections_dir / f"{(today - timedelta(days=i)).isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("kind") != "dream":
                continue
            for seed in rec.get("seed_entries") or []:
                if isinstance(seed, dict):
                    keys.add((str(seed.get("tag", "?")), str(seed.get("domain", "?"))))
    return keys


# ── Sampling helpers ─────────────────────────────────────────────────────────


def _weighted_sample(rng: random.Random, pool: list[dict], k: int, weight_fn) -> list[dict]:
    """Intensity-weighted sample without replacement; does not mutate `pool`."""
    remaining = list(pool)
    chosen: list[dict] = []
    for _ in range(min(k, len(remaining))):
        weights = [max(weight_fn(e), 1e-6) for e in remaining]
        total = sum(weights)
        r = rng.random() * total
        upto = 0.0
        idx = len(remaining) - 1
        for i, w in enumerate(weights):
            upto += w
            if upto >= r:
                idx = i
                break
        chosen.append(remaining.pop(idx))
    return chosen


def _remove_all(pool: list[dict], items: list[dict]) -> None:
    """Remove `items` from `pool` by identity (in place)."""
    ids = {id(e) for e in items}
    pool[:] = [e for e in pool if id(e) not in ids]


def _tag_bucket(items: list[dict], bucket: str) -> None:
    for e in items:
        e["_bucket"] = bucket


def _split_into_epochs(entries: list[dict], n_epochs: int) -> list[list[dict]]:
    """Split entries into `n_epochs` equal-time-width buckets, oldest to newest."""
    epochs: list[list[dict]] = [[] for _ in range(n_epochs)]
    if not entries:
        return epochs
    lo = min(e["ts_epoch"] for e in entries)
    hi = max(e["ts_epoch"] for e in entries)
    if hi <= lo:
        epochs[-1] = list(entries)
        return epochs
    span = hi - lo
    for e in entries:
        frac = (e["ts_epoch"] - lo) / span
        idx = min(n_epochs - 1, int(frac * n_epochs))
        epochs[idx].append(e)
    return epochs


def _pick_distant_pairs(
    rng: random.Random,
    pool: list[dict],
    k: int,
    candidate_pool_size: int,
    min_day_gap: float,
) -> tuple[list[dict], list[dict]]:
    """
    The novelty engine. Samples up to `candidate_pool_size` entries
    (intensity-weighted, no replacement) from `pool`, computes pairwise
    tag-set Jaccard via reflexive._compute_tag_overlap, and greedily selects
    up to `k` pairs with ZERO tag overlap, >= min_day_gap days apart,
    highest combined intensity first. Each entry is used in at most one
    pair.

    Never pads: if fewer than `k` pairs qualify, returns fewer (even zero).
    A forced pair with real overlap would falsify the "genuinely distant"
    property this bucket exists to guarantee.

    Returns (chosen_pairs, sampled_candidates) — the candidates are returned
    too so callers can inspect the pool size actually available.
    """
    candidates = _weighted_sample(
        rng, pool, min(candidate_pool_size, len(pool)), lambda e: e["intensity"]
    )
    tagged = [(e, _normalize_tags(e.get("domain", ""))) for e in candidates]
    qualifying: list[tuple[dict, dict, float, float, float]] = []
    for (a, tags_a), (b, tags_b) in itertools.combinations(tagged, 2):
        if not tags_a or not tags_b:
            continue  # untaggable entries can't make a meaningful "no shared tags" claim
        jaccard = _compute_tag_overlap(tags_a, b.get("domain", ""))
        if jaccard != 0.0:
            continue
        gap_days = abs(a["ts_epoch"] - b["ts_epoch"]) / 86400.0
        if gap_days < min_day_gap:
            continue
        qualifying.append((a, b, jaccard, gap_days, a["intensity"] + b["intensity"]))

    qualifying.sort(key=lambda t: t[4], reverse=True)
    chosen: list[dict] = []
    used: set[int] = set()
    for a, b, jaccard, gap_days, _combined in qualifying:
        if id(a) in used or id(b) in used:
            continue
        chosen.append({"a": a, "b": b, "jaccard": jaccard, "day_gap": round(gap_days, 1)})
        used.add(id(a))
        used.add(id(b))
        if len(chosen) >= k:
            break
    return chosen, candidates


# ── Survey assembly ──────────────────────────────────────────────────────────


def read_dream_survey(
    chronicle_root: Path = CHRONICLE_INSIGHTS,
    reflections_dir: Path = REFLECTIONS_DIR,
    budget: int = DEFAULT_SURVEY_BUDGET,
    seed: int | None = None,
    now: datetime | None = None,
) -> DreamSurvey:
    """
    Assemble the stratified "lay of the land" — see module docstring / build
    spec for the six buckets. Order matters: each stratum draws from what's
    left after the previous ones, so earlier buckets get first pick and no
    entry appears twice. Never pads short buckets to hit `budget` — an
    honest smaller survey beats a padded one that lies about distance.

    `seed` defaults to today's date as an int (YYYYMMDD) so a given night's
    serendipity/distant-pair draw is reproducible if rerun.
    """
    now = now or datetime.now(timezone.utc)
    if seed is None:
        seed = int(now.strftime("%Y%m%d"))
    rng = random.Random(seed)

    all_entries = _load_all_projected(chronicle_root)
    total = len(all_entries)
    if not all_entries:
        return DreamSurvey(
            entries=[], distant_pairs=[], total_chronicle_entries=0,
            freshness_excluded_count=0, budget=budget,
        )

    excluded_keys = _recent_dream_seed_keys(reflections_dir, before=now.date())
    remaining_pool = [e for e in all_entries if _seed_key(e) not in excluded_keys]
    freshness_excluded_count = total - len(remaining_pool)

    # 1. Persistent anchors — high-intensity ground truth, intensity-weighted.
    anchor_candidates = [
        e
        for e in remaining_pool
        if e["layer"] == "ground_truth" and e["intensity"] >= SURVEY_ANCHOR_MIN_INTENSITY
    ]
    anchors = _weighted_sample(rng, anchor_candidates, SURVEY_ANCHOR_COUNT, lambda e: e["intensity"])
    _remove_all(remaining_pool, anchors)
    _tag_bucket(anchors, "anchor")

    # 2. Temporal spread — 5 epochs across full history, 2 per epoch, prefer intensity.
    epochs = _split_into_epochs(remaining_pool, SURVEY_TEMPORAL_EPOCHS)
    temporal: list[dict] = []
    for epoch_entries in epochs:
        epoch_sorted = sorted(epoch_entries, key=lambda e: e["intensity"], reverse=True)
        temporal.extend(epoch_sorted[:SURVEY_TEMPORAL_PER_EPOCH])
    temporal = temporal[:SURVEY_TEMPORAL_COUNT]
    _remove_all(remaining_pool, temporal)
    _tag_bucket(temporal, "temporal")

    # 3. Recent — last 72h, newest first.
    cutoff = now.timestamp() - SURVEY_RECENT_WINDOW_HOURS * 3600
    recent = sorted(
        (e for e in remaining_pool if e["ts_epoch"] >= cutoff),
        key=lambda e: e["ts_epoch"],
        reverse=True,
    )[:SURVEY_RECENT_COUNT]
    _remove_all(remaining_pool, recent)
    _tag_bucket(recent, "recent")

    # 4. Distant pairs — the novelty engine. Never padded.
    distant_pairs, _sampled = _pick_distant_pairs(
        rng, remaining_pool, SURVEY_DISTANT_PAIR_COUNT,
        SURVEY_DISTANT_CANDIDATE_POOL, SURVEY_DISTANT_MIN_DAY_GAP,
    )
    distant_entries: list[dict] = []
    for pair in distant_pairs:
        distant_entries.append(pair["a"])
        distant_entries.append(pair["b"])
    _remove_all(remaining_pool, distant_entries)
    _tag_bucket(distant_entries, "distant_pair")

    # 5. Serendipity — uniform random, any layer, seeded by date.
    pool_copy = list(remaining_pool)
    rng.shuffle(pool_copy)
    serendipity = pool_copy[:SURVEY_SERENDIPITY_COUNT]
    _remove_all(remaining_pool, serendipity)
    _tag_bucket(serendipity, "serendipity")

    # 6. Open threads — unresolved questions, oldest-leaning.
    open_threads = sorted(
        (e for e in remaining_pool if e["layer"] == "open_thread"),
        key=lambda e: e["ts_epoch"],
    )[:SURVEY_OPEN_THREAD_COUNT]
    _remove_all(remaining_pool, open_threads)
    _tag_bucket(open_threads, "open_thread")

    selected = anchors + temporal + recent + distant_entries + serendipity + open_threads
    return DreamSurvey(
        entries=selected,
        distant_pairs=distant_pairs,
        total_chronicle_entries=total,
        freshness_excluded_count=freshness_excluded_count,
        budget=budget,
    )


def survey_seed_entries(survey: DreamSurvey) -> list[dict]:
    """Dedup'd {tag, domain} pairs for every entry in the survey — the write shape."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for e in survey.entries:
        key = _seed_key(e)
        if key in seen:
            continue
        seen.add(key)
        out.append({"tag": key[0], "domain": key[1]})
    return out


def _allowed_tags_from_survey(survey: DreamSurvey) -> set[str]:
    """Union of normalized tags across every seed entry's domain string."""
    tags: set[str] = set()
    for e in survey.entries:
        tags.update(_normalize_tags(e.get("domain", "")))
    return tags


# ── Prompt assembly ──────────────────────────────────────────────────────────


DREAM_PREAMBLE = """\
You are dreaming over a chronicle written by AI instances and a human
researcher (Anthony) across many months. Below is a "lay of the land" — a
stratified sample of entries chosen for DISTANCE, not recency: persistent
anchors, a spread across the chronicle's whole history, a few recent
entries, some uniformly random entries, a few open questions, and (when
the chronicle is rich enough) some DISTANT PAIRS — entries that share no
tags and are weeks or months apart.

This is not analysis. You are not looking for the strongest signal or the
most defensible claim. You are dreaming: let the material sit together and
notice what surfaces — an image, a structural rhyme, an unlikely link
between two things that have no business touching. The distant pairs exist
to provoke this; treat them as kindling for ONE dream, not separate
puzzles to solve one by one.

Produce exactly ONE novel connection: something that, if true, links two or
more of the entries below in a way nobody has written down yet. It does not
need to be provable. It needs to be genuine — not a restatement, not two
entries merely coexisting in the same window, not a forced pairing. If the
strongest thing you notice is thin, say so plainly inside the dream rather
than inflating it.

Write freely, in prose, 1-4 paragraphs. Name the specific entries you are
drawing on (their [tag] markers). This is the raw dream — a second pass
will condense and tag it, so don't self-edit into a summary here; let it
breathe.
"""

_BUCKET_LABELS = {
    "anchor": "PERSISTENT ANCHORS (high-intensity ground truth)",
    "temporal": "TEMPORAL SPREAD (across the chronicle's whole history)",
    "recent": "RECENT (last 72h)",
    "serendipity": "SERENDIPITY (uniform random)",
    "open_thread": "OPEN THREADS (unresolved questions)",
}


def _format_survey_entry(e: dict) -> str:
    marker = " (superseded)" if "_superseded_by" in e else ""
    return (
        f"[{e.get('tag', '?')}] {e.get('timestamp', '')[:19]} — "
        f"domain={e.get('domain', '?')[:80]} layer={e.get('layer', '?')}{marker} "
        f"intensity={e.get('intensity', 0.5):.2f}\n{e.get('content', '')}\n"
    )


def build_dream_prompt(survey: DreamSurvey) -> str:
    lines = [DREAM_PREAMBLE, ""]

    by_bucket: dict[str, list[dict]] = {}
    for e in survey.entries:
        by_bucket.setdefault(e.get("_bucket", "?"), []).append(e)

    for bucket in ("anchor", "temporal", "recent", "serendipity", "open_thread"):
        items = by_bucket.get(bucket) or []
        if not items:
            continue
        lines.append(f"═══ {_BUCKET_LABELS[bucket]} ═══")
        lines.append("")
        for e in items:
            lines.append(_format_survey_entry(e))
        lines.append("")

    if survey.distant_pairs:
        lines.append(
            f"═══ {len(survey.distant_pairs)} DISTANT PAIR(S) — "
            "no shared tags, weeks/months apart ═══"
        )
        lines.append("")
        for i, pair in enumerate(survey.distant_pairs):
            label = chr(ord("A") + i)
            lines.append(
                f"DISTANT PAIR {label} — these two share no tags and are "
                f"{pair['day_gap']:.0f} days apart. What single unseen thing, "
                "if true, connects them?"
            )
            lines.append(_format_survey_entry(pair["a"]))
            lines.append(_format_survey_entry(pair["b"]))
            lines.append("")

    lines.append(
        "Now dream ONE novel connection across the material above. Prose, "
        "1-4 paragraphs, name the entries you draw on."
    )
    return "\n".join(lines)


CONDENSE_PREAMBLE_TEMPLATE = """\
You just produced a freeform dream connecting distant chronicle material.
Condense it now into a structured record. Do not add new claims — only
distill what you already wrote.

Output STRICT JSON, no prose outside it:
{{
  "title": "<a short handle, at most 10 words>",
  "observation": "<the condensed novel connection, 1-3 paragraphs — this is
                   what a future reader sees>",
  "domains": ["<tag>", ...]
}}

Tag rules for "domains": prefer tags from this allowed list (drawn from
tonight's seed material) — {allowed_tags}
— you may add AT MOST 2 tags outside that list if the dream genuinely needs
a concept not present in the seed material. Do not invent more than 2 free
tags.

If, on rereading, the dream you produced was actually thin — mere
coexistence, a restatement, nothing genuinely novel — output instead:
{{"title": "", "observation": "", "domains": []}}
That is a correct, respected answer.
"""


def build_condense_prompt(dream_full: str, allowed_tags: set[str]) -> str:
    tags_str = ", ".join(sorted(allowed_tags)) if allowed_tags else "(none)"
    preamble = CONDENSE_PREAMBLE_TEMPLATE.format(allowed_tags=tags_str)
    return (
        f"{preamble}\n"
        "═══ YOUR DREAM (raw, from the previous turn) ═══\n\n"
        f"{dream_full}\n\n"
        "═══ END DREAM ═══\n\n"
        "Now output the JSON described above."
    )


# The critic preamble is a PLAIN constant (no .format), so its literal JSON
# braces are safe as-is. GUARDRAIL (load-bearing, mirrors the delivery-rail
# guardrail): the critic must score novelty + coherence and NEVER usefulness /
# on-task-ness. A dream is allowed to be useless and strange — penalizing that
# would destroy the elasticity the whole layer exists to protect.
CRITIC_PREAMBLE = """\
You are a NOVELTY CRITIC for a machine "dream" — an associative connection one
model drew across distant entries in a long research chronicle. A DIFFERENT
model dreamed it; your only job is to grade it, so the dreamer never grades its
own homework.

Judge the dream on exactly TWO axes, and NOTHING else:

  1. NOVELTY — is the connection genuinely non-obvious? A real dream links
     things that have no business touching and surfaces something nobody has
     already written down. Score it NOT novel if it is a restatement of a
     single entry, two entries merely coexisting in the same window, a truism,
     or a link anyone would draw immediately.

  2. COHERENCE — does the connection hold together AS A CLAIM? A coherent
     dream is one intelligible idea you could restate in your own words. Score
     it NOT coherent if it is word-salad, self-contradictory, a non-sequitur,
     or so vague it asserts nothing.

HARD RULE — you must NOT judge USEFULNESS. Do NOT consider whether the dream is
relevant to any goal, actionable, on-topic, practical, provable, true, or
important. A dream is ALLOWED to be useless, strange, purposeless, or idle —
that elasticity is the entire point, and penalizing a dream for not being
on-task would destroy the very thing you are here to protect. A
useless-but-novel-and-coherent dream is a KEEP. The ONLY grounds to abstain are
lack of novelty or lack of coherence.

Output STRICT JSON, no prose outside it:
{
  "novel": true or false,
  "coherent": true or false,
  "verdict": "keep" or "abstain",
  "reason": "<one sentence, grounded ONLY in novelty and coherence>"
}

Set "verdict" to "keep" if the dream is BOTH novel and coherent. Set it to
"abstain" ONLY if it fails novelty or fails coherence. Never abstain for a
dream being useless, idle, or off-goal.
"""


def build_critic_prompt(title: str, observation: str, dream_full: str) -> str:
    """Assemble the critic prompt from the dream ALONE.

    The critic sees only the dream's title, condensed observation, and raw
    text — never the survey, the seed entries, or any "what are we working on"
    goal/project context. That absence is structural: with nothing to be
    on-task toward, the critic cannot reward on-task-ness even if it tried to.
    """
    return (
        f"{CRITIC_PREAMBLE}\n"
        "═══ THE DREAM TO GRADE ═══\n\n"
        f"TITLE: {title}\n\n"
        "CONDENSED CONNECTION (what a future reader would see):\n"
        f"{observation}\n\n"
        "RAW DREAM (the unedited version, for judging coherence):\n"
        f"{dream_full}\n\n"
        "═══ END DREAM ═══\n\n"
        "Now output the JSON verdict described above, and nothing else."
    )


# ── Model calls (Ollama) ─────────────────────────────────────────────────────


def _ollama_generate(payload: dict, timeout: int) -> tuple[bool, str, dict]:
    """
    Shared HTTP mechanics for /api/generate. Returns (ok, output_text, raw_meta)
    — raw_meta carries prompt_eval_count/eval_count for the num_ctx tripwire.

    HTTP API on purpose, not `ollama run` — see synthesis_daemon's call_ollama
    docstring for why (CLI streaming leaks ANSI codes that corrupt JSON).
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_resp = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        return False, f"ollama http error: {exc}", {}
    except TimeoutError:
        return False, f"ollama timed out after {timeout}s", {}

    try:
        data = json.loads(raw_resp)
    except json.JSONDecodeError as exc:
        return False, f"ollama returned non-JSON: {exc}", {}

    output = data.get("response", "")
    if not output:
        return False, f"ollama empty response: {raw_resp[:300]}", data
    return True, output, data


def _warn_if_context_truncated(meta: dict, num_ctx: int, label: str) -> None:
    """
    Grounding note: num_ctx MUST be set explicitly or Ollama silently
    truncates a large survey prompt. This can't fully prove truncation
    didn't happen, but if prompt_eval_count lands at/near num_ctx, the
    prompt almost certainly got cut — log loudly so it isn't a silent loss.
    """
    pec = meta.get("prompt_eval_count")
    if isinstance(pec, int) and num_ctx and pec >= num_ctx - 8:
        print(
            f"DREAM DAEMON: WARNING — {label} call prompt_eval_count={pec} is "
            f"at/near num_ctx={num_ctx}; the survey prompt may have been "
            "silently truncated.",
            file=sys.stderr,
        )


def call_ollama_dream(
    prompt: str,
    *,
    model: str = DREAM_MODEL,
    num_predict: int = DEFAULT_DREAM_NUM_PREDICT,
    timeout: int = DREAM_CALL_TIMEOUT_SECONDS,
) -> tuple[bool, str, dict]:
    """
    Phase-1 DREAM call — freeform, hot. keep_alive="10m" keeps the model
    resident for the phase-2 condense call that follows. think and
    keep_alive are TOP-LEVEL fields, not inside options (Ollama foot-gun).
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {
            "num_predict": num_predict,
            "temperature": 0.9,
            "top_p": 0.95,
            "num_ctx": DREAM_NUM_CTX,
        },
    }
    ok, text, meta = _ollama_generate(payload, timeout)
    _warn_if_context_truncated(meta, DREAM_NUM_CTX, "dream")
    return ok, text, meta


def call_ollama_condense(
    prompt: str,
    *,
    model: str = DREAM_MODEL,
    num_predict: int = DEFAULT_CONDENSE_NUM_PREDICT,
    timeout: int = CONDENSE_CALL_TIMEOUT_SECONDS,
) -> tuple[bool, str, dict]:
    """
    Phase-2 CONDENSE+TAG call — structured, cool, model still warm.
    format="json" + keep_alive=0. The unload rides this request's response.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": 0,
        "options": {
            "num_predict": num_predict,
            "temperature": 0.3,
            "num_ctx": DREAM_NUM_CTX,
        },
    }
    ok, text, meta = _ollama_generate(payload, timeout)
    _warn_if_context_truncated(meta, DREAM_NUM_CTX, "condense")
    return ok, text, meta


def _num_ctx_for(prompt: str, num_predict: int) -> int:
    """
    Size num_ctx to FIT this prompt plus its expected output — "sized to the
    dream text" means fit it, never shrink below what it needs. Estimates
    prompt tokens conservatively (~3 chars/token → slight over-estimate, the
    safe direction), adds num_predict + headroom, rounds up to a 1024 boundary,
    and clamps to [CRITIC_NUM_CTX_MIN, CRITIC_NUM_CTX_MAX]. The dream_full can
    be up to DEFAULT_DREAM_NUM_PREDICT tokens, so this must be able to grow to
    several thousand — an undersized ceiling would silently truncate.
    """
    est_prompt_tokens = int(len(prompt) / 3.0) + 256
    needed = est_prompt_tokens + max(num_predict, 0)
    rounded = ((needed + 1023) // 1024) * 1024
    return max(CRITIC_NUM_CTX_MIN, min(CRITIC_NUM_CTX_MAX, rounded))


def call_ollama_critic(
    prompt: str,
    *,
    model: str = DREAM_CRITIC_MODEL,
    num_predict: int = DEFAULT_CRITIC_NUM_PREDICT,
    timeout: int = CRITIC_CALL_TIMEOUT_SECONDS,
) -> tuple[bool, str, dict]:
    """
    Phase-3 CROSS-MODEL CRITIC call — a DIFFERENT model (gemma4:26b by default)
    grades the qwen dream on novelty + coherence. Structured and cool,
    format="json", think=false. keep_alive:0 unloads the critic when its
    response returns — the unload rides this request, same mechanism as the
    condense pass. num_ctx is sized to THIS prompt (one dream, not a 40-entry
    survey), so peak GPU stays at one model and the load is short.
    """
    num_ctx = _num_ctx_for(prompt, num_predict)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": 0,
        "options": {
            "num_predict": num_predict,
            "temperature": 0.2,
            "num_ctx": num_ctx,
        },
    }
    ok, text, meta = _ollama_generate(payload, timeout)
    _warn_if_context_truncated(meta, num_ctx, "critic")
    return ok, text, meta


# ── Output parsing ────────────────────────────────────────────────────────────


def parse_condensed(raw: str) -> dict | None:
    """Parse the phase-2 JSON into {"title", "observation", "domains"}, or None."""
    block = extract_json_block(raw)
    if not block:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()
    observation = str(data.get("observation") or "").strip()
    domains = data.get("domains") or []
    if not isinstance(domains, list):
        domains = []
    return {"title": title, "observation": observation, "domains": [str(d) for d in domains]}


def _constrain_domains(domains: list[str], allowed_tags: set[str]) -> list[str]:
    """
    Constrain the model's tag output to the union of seed-entry tags plus at
    most 2 free tags — load-bearing so a future delivery rail can find dreams
    by tag overlap (a dream tagged entirely outside the seed vocabulary would
    never rail-match anything).
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for d in domains:
        for tag in _normalize_tags(d):
            if tag not in seen:
                seen.add(tag)
                normalized.append(tag)
    allowed = [t for t in normalized if t in allowed_tags]
    free = [t for t in normalized if t not in allowed_tags]
    return allowed + free[:2]


def is_degenerate(text: str, min_chars: int = 40) -> bool:
    return len((text or "").strip()) < min_chars


# ── Cross-model critic parsing + orchestration ───────────────────────────────


def _coerce_bool(value: object) -> bool | None:
    """Tolerant bool from a JSON-mode model: real bools, 0/1, and the usual
    string spellings. Returns None on anything unrecognized (recorded as-is;
    never treated as an abstain signal)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "y", "1", "novel", "coherent"):
            return True
        if v in ("false", "no", "n", "0"):
            return False
    return None


def parse_critic(raw: str) -> dict | None:
    """
    Parse the critic JSON into {"novel","coherent","verdict","reason"}, or
    None. None is the FAIL-OPEN signal — the caller KEEPS the dream and marks
    it critic_status="unavailable". A missing or non-literal verdict returns
    None ON PURPOSE: a garbage verdict must never be read as an abstain. Only a
    verdict that parses cleanly as exactly "keep" or "abstain" is honored, and
    only "abstain" can drop a dream.
    """
    block = extract_json_block(raw)
    if not block:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("keep", "abstain"):
        return None  # fail-open: unparseable/garbage verdict → KEEP, never a silent abstain
    return {
        "novel": _coerce_bool(data.get("novel")),
        "coherent": _coerce_bool(data.get("coherent")),
        "verdict": verdict,
        "reason": str(data.get("reason") or "").strip()[:500],
    }


def run_critic(
    title: str,
    observation: str,
    dream_full: str,
    *,
    model: str = DREAM_CRITIC_MODEL,
    num_predict: int = DEFAULT_CRITIC_NUM_PREDICT,
    timeout: int = CRITIC_CALL_TIMEOUT_SECONDS,
) -> CriticResult:
    """
    Run the cross-model critic and return a CriticResult. FAIL-OPEN by
    contract: any failure — model unreachable/absent, empty response,
    unparseable JSON, missing/garbage verdict — returns status="unavailable",
    which the caller treats as KEEP. The ONLY status that suppresses a dream is
    "abstain", reachable ONLY from a cleanly-parsed verdict=="abstain". A broken
    critic must NEVER silently abstain every dream.

    Trusts the model's `verdict` field as authoritative (per spec) rather than
    reconciling it against novel/coherent; all four are recorded for later
    analysis regardless.
    """
    prompt = build_critic_prompt(title, observation, dream_full)
    ok, text, _meta = call_ollama_critic(
        prompt, model=model, num_predict=num_predict, timeout=timeout
    )
    if not ok:
        return CriticResult(
            status="unavailable", reason=f"critic call failed: {text[:200]}", model=model
        )
    parsed = parse_critic(text)
    if parsed is None:
        return CriticResult(
            status="unavailable", reason="critic verdict unparseable", model=model
        )
    return CriticResult(
        status=parsed["verdict"],  # "keep" | "abstain"
        novel=parsed["novel"],
        coherent=parsed["coherent"],
        reason=parsed["reason"],
        model=model,
    )


# ── Dedupe against recent nights ─────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9']+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _token_jaccard(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _recent_dream_observations(reflections_dir: Path, nights: int, before: date) -> list[str]:
    out: list[str] = []
    if not reflections_dir.exists():
        return out
    for i in range(0, nights + 1):  # include "today" so a same-day rerun (--force) still dedupes
        path = reflections_dir / f"{(before - timedelta(days=i)).isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("kind") == "dream" and rec.get("observation"):
                out.append(rec["observation"])
    return out


def find_duplicate(
    observation: str,
    reflections_dir: Path,
    nights: int = DEDUPE_LOOKBACK_NIGHTS,
    threshold: float = DEDUPE_JACCARD_THRESHOLD,
    before: date | None = None,
) -> float | None:
    """Returns the jaccard score of the first near-duplicate found, or None."""
    before = before or datetime.now(timezone.utc).date()
    for past in _recent_dream_observations(reflections_dir, nights, before):
        score = _token_jaccard(observation, past)
        if score > threshold:
            return score
    return None


# ── Pre-flight ────────────────────────────────────────────────────────────────


def temple_core_mounted(mount_path: Path = TEMPLE_CORE_MOUNT) -> bool:
    try:
        return os.path.ismount(str(mount_path))
    except OSError:
        return False


def _ollama_ps() -> list[dict] | None:
    """GET /api/ps. Returns the models list, or None if unreachable (caller
    should treat None as "can't verify" — not as "nothing resident")."""
    try:
        req = urllib.request.Request(OLLAMA_PS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    return models if isinstance(models, list) else []


def _memory_pressure_blocked(models: list[dict], threshold_bytes: int = MEMORY_PRESSURE_BYTES) -> str | None:
    """Returns the offending model name if one >threshold_bytes is resident."""
    for m in models:
        try:
            size = int(m.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if size > threshold_bytes:
            return str(m.get("name", "?"))
    return None


def _already_dreamed_today(reflections_dir: Path, today: date) -> bool:
    path = reflections_dir / f"{today.isoformat()}.jsonl"
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("kind") == "dream":
            return True
    return False


# ── Cleanup ──────────────────────────────────────────────────────────────────

_OLLAMA_BIN = shutil.which("ollama") or "/usr/local/bin/ollama"


def _await_model_unloaded(
    model: str,
    max_wait_seconds: float = CRITIC_UNLOAD_WAIT_SECONDS,
    poll_interval: float = CRITIC_UNLOAD_POLL_INTERVAL,
) -> bool | None:
    """
    Bounded, 3-state barrier enforcing the one-model-peak invariant. The
    condense pass's keep_alive:0 unloads the ~17GB dreamer; before loading the
    ~17GB critic onto a 36GB box we CONFIRM the dreamer is actually gone (34GB
    resident at once risks the exact GPU-memory contention the preflight
    guards against). Polls /api/ps until `model` is absent, up to
    max_wait_seconds.

    Returns a 3-state signal — the caller MUST distinguish these, never
    collapse them to a single bool:
      True  — CONFIRMED UNLOADED: `model` was absent from /api/ps before the
              deadline. Safe to proceed with the critic.
      False — CONFIRMED STILL RESIDENT: /api/ps stayed reachable for the
              whole wait and `model` was present at the deadline. This is
              POSITIVE evidence of a two-model memory peak — the caller MUST
              NOT load a second model on top of it (fail SAFE).
      None  — COULDN'T VERIFY: /api/ps was unreachable at some point during
              the wait. No evidence either way — the caller proceeds exactly
              as before this barrier existed (fail OPEN).

    Conflating False and None here would either needlessly block dreams on a
    flaky /api/ps (if None were treated as risk) or, as originally shipped,
    silently allow a confirmed-resident dreamer to be loaded alongside the
    critic (if False were treated as safe-to-proceed) — this return value is
    the fix for exactly that bug. _cleanup_model remains the backstop for
    anything left resident regardless of this barrier's outcome. (time.sleep
    here is fine — this runs in the daemon process.)
    """
    deadline = time.time() + max_wait_seconds
    while True:
        models = _ollama_ps()
        if models is None:
            return None  # can't verify — fail-open, proceed as before
        if model not in [m.get("name") for m in models]:
            return True  # confirmed unloaded
        if time.time() >= deadline:
            print(
                f"DREAM DAEMON: WARNING — {model} still resident after "
                f"{max_wait_seconds:.0f}s wait; SKIPPING the cross-model "
                "critic this run to avoid a two-model memory peak "
                "(cleanup backstop will unload any lingering models).",
                file=sys.stderr,
            )
            return False  # confirmed still resident — fail SAFE, caller must skip
        time.sleep(poll_interval)


def _cleanup_model(model: str) -> None:
    """
    Best-effort unload verification. keep_alive=0 on the phase-2 call is the
    PRIMARY unload mechanism — it rides that request's response. This is the
    backstop for when phase 2 never got a response at all (e.g. a network
    error) and the model is still resident from phase 1's keep_alive="10m".
    Never leaves ~18GB resident under the morning stack silently — logs
    loudly to stderr if it can't confirm the model is gone.
    """
    models = _ollama_ps()
    if models is None:
        print(
            f"DREAM DAEMON: cleanup could not reach Ollama /api/ps to verify "
            f"{model} unloaded",
            file=sys.stderr,
        )
        return
    resident = [m.get("name") for m in models]
    if model not in resident:
        return
    print(f"DREAM DAEMON: {model} still resident after run — stopping explicitly", file=sys.stderr)
    try:
        subprocess.run([_OLLAMA_BIN, "stop", model], timeout=30, check=False, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"DREAM DAEMON: ollama stop failed: {exc}", file=sys.stderr)
        return
    models_after = _ollama_ps()
    if models_after is not None and model in [m.get("name") for m in models_after]:
        print(
            f"DREAM DAEMON: LOUD WARNING — {model} STILL resident after explicit "
            "stop. Manual intervention needed (check `ollama ps`).",
            file=sys.stderr,
        )


# ── Persistence ──────────────────────────────────────────────────────────────


def write_dream(
    *,
    run_id: str,
    model: str,
    title: str,
    observation: str,
    dream_full: str,
    seed_entries: list[dict],
    domains: list[str],
    critic_status: str | None = None,
    critic_model: str | None = None,
    critic_novel: bool | None = None,
    critic_coherent: bool | None = None,
    critic_reason: str | None = None,
    out_dir: Path = REFLECTIONS_DIR,
    now: datetime | None = None,
) -> Path:
    """Append ONE dream record to ~/.sovereign/reflections/<YYYY-MM-DD>.jsonl.

    Never writes to the chronicle proper (no record_insight) — promotion
    stays human/Claude-gated later, same layer hygiene as reflections.py's
    module docstring already establishes for synthesis reflections.

    Critic fields are injected ONLY when critic_status is not None (i.e. the
    critic pass was reached — this includes "skipped_memory", where the
    critic pass was reached but the critic model was deliberately never
    invoked). When DREAM_CRITIC=off, none are passed and the record is
    byte-identical to a pre-critic dream — the suite has byte-identity
    regressions, so the disabled path must not drift.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    path = out_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
    record = {
        "id": f"dream_{run_id}_{uuid.uuid4().hex[:8]}",
        "kind": "dream",
        "timestamp": now.isoformat(),
        "model": model,
        "prompt_version": DREAM_PROMPT_VERSION,
        "run_id": run_id,
        "title": title,
        "observation": observation,
        "dream_full": dream_full,
        "seed_entries": seed_entries,
        "domains": domains,
        "connection_type": "dream",
        "confidence": "low",
        "ack_status": "unread",
    }
    if critic_status is not None:
        record["critic_status"] = critic_status  # "keep" | "unavailable" | "skipped_memory"
        record["critic_model"] = critic_model
        record["critic_novel"] = critic_novel
        record["critic_coherent"] = critic_coherent
        record["critic_reason"] = critic_reason
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# ── Orchestrator ─────────────────────────────────────────────────────────────


@dataclass
class DreamDaemon:
    model: str = DREAM_MODEL
    chronicle_root: Path = field(default_factory=lambda: CHRONICLE_INSIGHTS)
    reflections_dir: Path = field(default_factory=lambda: REFLECTIONS_DIR)
    survey_budget: int = DEFAULT_SURVEY_BUDGET
    dream_num_predict: int = DEFAULT_DREAM_NUM_PREDICT
    condense_num_predict: int = DEFAULT_CONDENSE_NUM_PREDICT
    dream_timeout: int = DREAM_CALL_TIMEOUT_SECONDS
    condense_timeout: int = CONDENSE_CALL_TIMEOUT_SECONDS
    critic_model: str = DREAM_CRITIC_MODEL
    critic_enabled: bool = field(default_factory=_critic_enabled_from_env)
    critic_num_predict: int = DEFAULT_CRITIC_NUM_PREDICT
    critic_timeout: int = CRITIC_CALL_TIMEOUT_SECONDS
    force: bool = False  # bypass the one-dream-per-night cap — testing only
    skip_preflight: bool = False  # bypass mount/memory-pressure checks — testing only
    seed: int | None = None

    def run(self) -> RunResult:
        run_id = uuid.uuid4().hex[:12]
        started = time.time()
        result = RunResult(outcome="skipped", run_id=run_id, model=self.model)
        now = datetime.now(timezone.utc)

        try:
            if not self.skip_preflight:
                if not temple_core_mounted():
                    msg = (
                        f"Temple_Core not mounted at {TEMPLE_CORE_MOUNT} — Ollama "
                        "cannot serve models from there. Dream skipped for tonight."
                    )
                    print(f"DREAM DAEMON: {msg}", file=sys.stderr)
                    result.outcome = "skipped_temple_core_unmounted"
                    result.details = msg
                    return result

                ps_models = _ollama_ps()
                if ps_models is not None:
                    blocker = _memory_pressure_blocked(ps_models)
                    if blocker:
                        result.outcome = "skipped_memory_pressure"
                        result.details = (
                            f"{blocker} already resident >{MEMORY_PRESSURE_BYTES // (1024**3)}GB; "
                            "dream skipped to avoid contending for GPU memory"
                        )
                        return result

            if not self.force and _already_dreamed_today(self.reflections_dir, now.date()):
                result.outcome = "skipped_already_dreamed"
                result.details = "one dream per night cap already satisfied"
                return result

            survey = read_dream_survey(
                chronicle_root=self.chronicle_root,
                reflections_dir=self.reflections_dir,
                budget=self.survey_budget,
                seed=self.seed,
                now=now,
            )
            result.survey_entry_count = len(survey.entries)
            result.distant_pair_count = len(survey.distant_pairs)
            if not survey.entries:
                result.outcome = "no_entries"
                result.details = f"no chronicle entries available under {self.chronicle_root}"
                return result

            dream_prompt = build_dream_prompt(survey)
            ok, dream_raw, _meta = call_ollama_dream(
                dream_prompt,
                model=self.model,
                num_predict=self.dream_num_predict,
                timeout=self.dream_timeout,
            )
            if not ok:
                result.outcome = "model_failed"
                result.details = f"dream call failed: {dream_raw[:300]}"
                return result

            dream_full = dream_raw.strip()
            result.dream_full = dream_full
            if is_degenerate(dream_full):
                result.outcome = "abstained"
                result.details = "dream call returned degenerate/empty output"
                return result

            allowed_tags = _allowed_tags_from_survey(survey)
            condense_prompt = build_condense_prompt(dream_full, allowed_tags)
            ok, condensed_raw, _meta = call_ollama_condense(
                condense_prompt,
                model=self.model,
                num_predict=self.condense_num_predict,
                timeout=self.condense_timeout,
            )
            if not ok:
                result.outcome = "model_failed"
                result.details = f"condense call failed: {condensed_raw[:300]}"
                return result

            condensed = parse_condensed(condensed_raw)
            if condensed is None:
                result.outcome = "parse_failed"
                result.details = "condense call returned unparseable JSON"
                return result

            observation = condensed["observation"]
            title = condensed["title"]
            if is_degenerate(observation, min_chars=20) or not title:
                result.outcome = "abstained"
                result.details = "condense pass judged the dream too thin on rereading"
                return result

            dup_score = find_duplicate(observation, self.reflections_dir, before=now.date())
            if dup_score is not None:
                result.outcome = "duplicate"
                result.details = f"token-set jaccard {dup_score:.2f} vs a dream in the last 14 nights"
                result.title = title
                result.observation = observation
                return result

            # ── Phase 3: cross-model NOVELTY CRITIC (gemma4:26b) ─────────────
            # Runs AFTER dedupe (never grades a dream we'd drop anyway) and
            # AFTER the condense pass's keep_alive:0 unloaded the dreamer.
            # _await_model_unloaded returns a 3-state signal that MUST be
            # honored (this is the fix for a prior bug where its return value
            # was ignored and gemma4 loaded unconditionally, risking ~34GB
            # resident on a 36GB box):
            #   False (confirmed-still-resident) → FAIL SAFE: skip the critic
            #     entirely, never load gemma4, keep the dream as
            #     critic_status="skipped_memory".
            #   True (confirmed-unloaded) or None (couldn't-verify) → FAIL
            #     OPEN as before: run the critic normally. A clean
            #     verdict=="abstain" is the ONLY outcome that suppresses a
            #     dream; every critic failure keeps it. Scores novelty +
            #     coherence ONLY, never usefulness (see CRITIC_PREAMBLE).
            critic: CriticResult | None = None
            if self.critic_enabled:
                unload_signal = _await_model_unloaded(self.model)
                if unload_signal is False:
                    # POSITIVE evidence the dreamer is still resident — the
                    # invariant is non-negotiable: do NOT load the critic.
                    skip_reason = (
                        f"{self.model} confirmed still resident after "
                        f"{CRITIC_UNLOAD_WAIT_SECONDS:.0f}s wait; skipped the "
                        "cross-model critic this run to avoid a two-model "
                        "memory peak (gemma4 was never loaded)."
                    )
                    print(f"DREAM DAEMON: {skip_reason}", file=sys.stderr)
                    critic = CriticResult(
                        status="skipped_memory", reason=skip_reason, model=self.critic_model
                    )
                    result.critic_status = critic.status
                    result.critic_reason = critic.reason
                else:
                    critic = run_critic(
                        title,
                        observation,
                        dream_full,
                        model=self.critic_model,
                        num_predict=self.critic_num_predict,
                        timeout=self.critic_timeout,
                    )
                    result.critic_status = critic.status
                    result.critic_novel = critic.novel
                    result.critic_coherent = critic.coherent
                    result.critic_reason = critic.reason
                    if critic.status == "abstain":
                        result.outcome = "abstained_critic"
                        result.title = title
                        result.observation = observation
                        result.details = (
                            f"cross-model critic ({self.critic_model}) abstained: "
                            f"novel={critic.novel} coherent={critic.coherent} — {critic.reason}"
                        )[:500]
                        return result

            domains = _constrain_domains(condensed["domains"], allowed_tags)
            seed_entries = survey_seed_entries(survey)

            critic_fields: dict = {}
            if critic is not None:
                # Present whenever the critic pass was reached: "keep" or
                # "unavailable" (fail-open) or "skipped_memory" (fail-safe —
                # the critic was never invoked because the dreamer was
                # confirmed still resident). Absent entirely when
                # DREAM_CRITIC=off.
                critic_fields = {
                    "critic_status": critic.status,
                    "critic_model": self.critic_model,
                    "critic_novel": critic.novel,
                    "critic_coherent": critic.coherent,
                    "critic_reason": critic.reason,
                }

            path = write_dream(
                run_id=run_id,
                model=self.model,
                title=title,
                observation=observation,
                dream_full=dream_full,
                seed_entries=seed_entries,
                domains=domains,
                out_dir=self.reflections_dir,
                now=now,
                **critic_fields,
            )
            result.outcome = "wrote"
            result.dream_path = str(path)
            result.title = title
            result.observation = observation
            critic_note = ""
            if critic is not None:
                critic_note = (
                    f", critic={critic.status}"
                    if critic.status != "unavailable"
                    else ", critic=unavailable (fail-open, kept)"
                )
            result.details = (
                f"wrote 1 dream from {len(survey.entries)} seed entries, "
                f"{len(survey.distant_pairs)} distant pairs{critic_note}"
            )
            return result
        except Exception as exc:
            # Fail-soft contract (module docstring): never crash, always
            # degrade to a clean RunResult. Covers write_dream OSErrors
            # (ENOSPC / read-only FS / permissions) and malformed-record
            # TypeErrors from projection/formatting. Log loudly so an
            # unattended nightly failure is not silent.
            result.outcome = "error"
            result.details = f"unexpected {type(exc).__name__}: {exc}"[:500]
            print(
                f"DREAM DAEMON: run failed, degraded to outcome=error: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return result
        finally:
            result.elapsed_seconds = round(time.time() - started, 2)
            # Always verify — cheap, idempotent, and this is the memory-health
            # backstop regardless of how the run above concluded. Covers BOTH
            # the ~17GB dreamer and (when the critic was enabled) the ~17GB
            # cross-model critic — either could be left resident under the
            # morning stack. When the critic ran, keep_alive:0 on its call is
            # the PRIMARY unload; this is the backstop for a critic call that
            # never got a response. When disabled, the critic model was never
            # loaded, so its cleanup is a cheap /api/ps no-op.
            models_to_clean = [self.model]
            if self.critic_enabled and self.critic_model not in models_to_clean:
                models_to_clean.append(self.critic_model)
            for _m in models_to_clean:
                _cleanup_model(_m)


# ── Survey report (for --survey-only) ────────────────────────────────────────


def _entry_brief(e: dict) -> dict:
    return {
        "bucket": e.get("_bucket"),
        "tag": e.get("tag"),
        "domain": e.get("domain"),
        "layer": e.get("layer"),
        "intensity": e.get("intensity"),
        "timestamp": e.get("timestamp"),
        "superseded": "_superseded_by" in e,
    }


def _survey_report(survey: DreamSurvey) -> dict:
    return {
        "total_chronicle_entries": survey.total_chronicle_entries,
        "freshness_excluded_count": survey.freshness_excluded_count,
        "budget": survey.budget,
        "selected_count": len(survey.entries),
        "by_bucket_count": {
            bucket: sum(1 for e in survey.entries if e.get("_bucket") == bucket)
            for bucket in ("anchor", "temporal", "recent", "distant_pair", "serendipity", "open_thread")
        },
        "entries": [_entry_brief(e) for e in survey.entries],
        "distant_pairs": [
            {
                "jaccard": p["jaccard"],
                "day_gap": p["day_gap"],
                "a": _entry_brief(p["a"]),
                "b": _entry_brief(p["b"]),
            }
            for p in survey.distant_pairs
        ],
    }


# ── CLI entrypoint ───────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def main(argv: list[str] | None = None) -> int:
    """Manual run: `python -m sovereign_stack.daemons.dream_daemon`.

    Honors env vars from the launchd plist (or shell), mirroring
    synthesis_daemon's __main__ pattern:
      DREAM_MODEL                — override DREAM_MODEL (default qwen3.6:27b)
      DREAM_NUM_PREDICT          — phase-1 num_predict override (fast tests)
      DREAM_CONDENSE_NUM_PREDICT — phase-2 num_predict override
      DREAM_REFLECTIONS_DIR      — override the reflections output dir
      DREAM_CHRONICLE_ROOT       — override the chronicle insights/ dir
      DREAM_CRITIC               — set to "off" to disable the cross-model critic
      DREAM_CRITIC_MODEL         — critic model (default gemma4:26b)
      DREAM_CRITIC_NUM_PREDICT   — critic num_predict override (fast tests)
      OLLAMA_BASE_URL            — Ollama base URL (default http://127.0.0.1:11434)
    CLI flags take precedence over env vars.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Sovereign Stack dream daemon (Phase 1)")
    parser.add_argument(
        "--survey-only",
        action="store_true",
        help="Print the survey sampler's selection + distant pairs; no model call, no write.",
    )
    parser.add_argument(
        "--num-predict",
        type=int,
        default=_env_int("DREAM_NUM_PREDICT", DEFAULT_DREAM_NUM_PREDICT),
        help="Phase-1 (dream) num_predict override, for fast test runs.",
    )
    parser.add_argument(
        "--condense-num-predict",
        type=int,
        default=_env_int("DREAM_CONDENSE_NUM_PREDICT", DEFAULT_CONDENSE_NUM_PREDICT),
        help="Phase-2 (condense) num_predict override.",
    )
    parser.add_argument(
        "--reflections-dir",
        type=Path,
        default=Path(os.getenv("DREAM_REFLECTIONS_DIR", str(REFLECTIONS_DIR))),
        help="Override the reflections output dir — point at a scratch dir for test runs.",
    )
    parser.add_argument(
        "--chronicle-root",
        type=Path,
        default=Path(os.getenv("DREAM_CHRONICLE_ROOT", str(CHRONICLE_INSIGHTS))),
    )
    parser.add_argument("--model", default=os.getenv("DREAM_MODEL", DREAM_MODEL))
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override the sampler's RNG seed (default: today's date as YYYYMMDD).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Bypass the one-dream-per-night cap (testing only)."
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Bypass the Temple_Core-mount and memory-pressure checks (testing only).",
    )
    parser.add_argument(
        "--critic-model",
        default=os.getenv("DREAM_CRITIC_MODEL", DREAM_CRITIC_MODEL),
        help="Cross-model novelty critic model (default gemma4:26b).",
    )
    parser.add_argument(
        "--critic-num-predict",
        type=int,
        default=_env_int("DREAM_CRITIC_NUM_PREDICT", DEFAULT_CRITIC_NUM_PREDICT),
        help="Critic num_predict override, for fast test runs.",
    )
    parser.add_argument(
        "--no-critic",
        action="store_true",
        help="Disable the cross-model critic (same as DREAM_CRITIC=off) — rollback switch.",
    )
    args = parser.parse_args(argv)

    if args.survey_only:
        survey = read_dream_survey(
            chronicle_root=args.chronicle_root,
            reflections_dir=args.reflections_dir,
            seed=args.seed,
        )
        print(json.dumps(_survey_report(survey), indent=2, ensure_ascii=False))
        return 0

    # Enabled unless DREAM_CRITIC=off OR --no-critic. --no-critic is the CLI
    # rollback switch; the env var is the launchd/plist one.
    critic_enabled = _critic_enabled_from_env() and not args.no_critic

    daemon = DreamDaemon(
        model=args.model,
        chronicle_root=args.chronicle_root,
        reflections_dir=args.reflections_dir,
        dream_num_predict=args.num_predict,
        condense_num_predict=args.condense_num_predict,
        critic_model=args.critic_model,
        critic_enabled=critic_enabled,
        critic_num_predict=args.critic_num_predict,
        force=args.force,
        skip_preflight=args.skip_preflight,
        seed=args.seed,
    )
    result = daemon.run()
    summary = {
        "outcome": result.outcome,
        "run_id": result.run_id,
        "model": result.model,
        "dream_path": result.dream_path,
        "title": result.title,
        "survey_entry_count": result.survey_entry_count,
        "distant_pair_count": result.distant_pair_count,
        "critic_enabled": critic_enabled,
        "critic_model": args.critic_model if critic_enabled else None,
        "critic_status": result.critic_status or None,
        "critic_novel": result.critic_novel,
        "critic_coherent": result.critic_coherent,
        "critic_reason": result.critic_reason or None,
        "elapsed_seconds": result.elapsed_seconds,
        "details": result.details,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    # "abstained"/"abstained_critic"/"duplicate"/"skipped_already_dreamed" are
    # healthy, respected outcomes, not failures — exit 0 so launchd doesn't log
    # them as errors.
    return (
        0
        if result.outcome
        in ("wrote", "abstained", "abstained_critic", "duplicate", "skipped_already_dreamed")
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
