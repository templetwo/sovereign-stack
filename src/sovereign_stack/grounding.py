"""
Grounded Extract — verify a claim is backed by ground-truth evidence.

Literature pattern: Huang et al. (2023) ICLR 2024 — LLM self-correction
without an external grounding signal regresses across math and reasoning
benchmarks. Every scheduled reflection step in v1.3.2's daemon arc must
go through a grounding gate or it will drift into reinforced hypothesis.

grounded_extract is this stack's CRITIC-style verifier, specialized for
its three-layer epistemic typing (ground_truth / hypothesis / open_thread).
Non-chronicle filesystem paths (source code, config, tests, git tree) are
structural evidence — filesystem reality, not LLM interpretation, so they
count as grounding. Chronicle files are checked layer-by-record: a file
grounds a claim only if at least one record inside has `layer: ground_truth`.
Hypothesis-only or open-thread-only chronicle files are rejected — that
is the load-bearing check that prevents reflection-on-hypothesis from
producing reinforced hypothesis.

Pure function, no LLM calls, no chronicle writes. Safe to call from any
scheduled daemon before it acts.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

# Stable reason codes — importable constants so daemons branch on identity,
# not string contents. If a new reason appears, add it here and document it.
REASON_OK = "ok"
REASON_NO_EVIDENCE = "no_evidence"
REASON_NO_GROUND_TRUTH = "no_ground_truth"
REASON_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

# Per-path reject reasons (appear in rejected_paths[i]["reason"]).
PATH_MISSING = "missing"
PATH_HYPOTHESIS_ONLY = "hypothesis_only"
PATH_OPEN_THREAD_ONLY = "open_thread_only"
PATH_UNREADABLE = "unreadable"

GROUND_TRUTH_LAYER = "ground_truth"
HYPOTHESIS_LAYER = "hypothesis"
OPEN_THREAD_LAYER = "open_thread"


@dataclass(frozen=True)
class GroundingResult:
    """
    Result of a grounding check. Truthy iff accepted.

    Attributes:
        accepted: True iff the claim is grounded.
        reason: Stable reason code (one of REASON_*). Daemons should branch
                on identity against the module-level constants, not on the
                string content — the constants are the contract.
        claim: The claim that was checked. Preserved verbatim for logging.
        matched_paths: Paths that qualified as grounding (by order given).
        rejected_paths: Paths that were offered but did not ground. Each
                entry is a dict with keys `path` and `reason` (one of
                PATH_MISSING, PATH_HYPOTHESIS_ONLY, PATH_OPEN_THREAD_ONLY,
                PATH_UNREADABLE).
    """

    accepted: bool
    reason: str
    claim: str
    matched_paths: list[str] = field(default_factory=list)
    rejected_paths: list[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.accepted


def _is_chronicle_path(path: Path, chronicle_root: Path) -> bool:
    """
    True iff `path` lives under chronicle_root. Used to decide whether a
    path needs record-level layer inspection (chronicle) or can be treated
    as structural evidence (anywhere else on the filesystem).
    """
    try:
        path.resolve().relative_to(chronicle_root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _scan_chronicle_layers(path: Path) -> set | None:
    """
    Return the set of layers present among JSONL records in `path`.

    Returns:
        A set of layer strings (e.g. {"ground_truth", "hypothesis"}), or
        None if the file cannot be read or parsed at all. An empty set is
        returned if the file is readable but contains no layer-tagged
        records — callers should treat that the same as hypothesis-only
        for grounding purposes (better to reject than to accept uncertainly).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    layers: set = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        layer = rec.get("layer")
        if isinstance(layer, str):
            layers.add(layer)
    return layers


def _classify_chronicle_path(path: Path) -> str | None:
    """
    Inspect a chronicle path's record layers and return either None
    (qualifies as grounding — contains at least one ground_truth record)
    or a reject reason code (PATH_*).
    """
    layers = _scan_chronicle_layers(path)
    if layers is None:
        return PATH_UNREADABLE
    if GROUND_TRUTH_LAYER in layers:
        return None  # accepted
    if layers == {OPEN_THREAD_LAYER}:
        return PATH_OPEN_THREAD_ONLY
    # Empty set or anything else without ground_truth is treated as
    # hypothesis-only — the strict-rejection default.
    return PATH_HYPOTHESIS_ONLY


def grounded_extract(
    claim: str,
    evidence_paths: list[str],
    *,
    chronicle_root: Path | None = None,
    min_evidence_paths: int = 1,
) -> GroundingResult:
    """
    Check whether `claim` is grounded by at least `min_evidence_paths`
    filesystem paths from `evidence_paths`.

    A path grounds the claim if it exists AND either:
      (a) it lives outside the chronicle root — treated as structural
          evidence (source code, configs, tests, git tree); OR
      (b) it lives inside the chronicle root AND contains at least one
          JSONL record whose `layer` field equals "ground_truth".

    Hypothesis-only or open-thread-only chronicle files do NOT ground.
    This is the layer-aware check that prevents reflection-on-hypothesis
    from producing reinforced hypothesis.

    Callers must tolerate `accepted=False`. The daemon-side idiom is:

        result = grounded_extract(claim, paths)
        if not result:
            return  # or log the rejection and skip

    Args:
        claim: The claim being grounded. Preserved on the result for
               logging. Never interpreted (no LLM call inside this
               function).
        evidence_paths: Filesystem paths offered as evidence. Strings are
               accepted for convenience; they're wrapped in Path internally.
        chronicle_root: Override for the chronicle root. Default is
               ~/.sovereign/chronicle. Injectable for tests.
        min_evidence_paths: How many paths must qualify as grounding for
               the claim to be accepted. Default 1. Callers expecting
               triangulation should set >= 2 explicitly.

    Returns:
        GroundingResult. Truthy iff accepted. Always carries a stable
        reason code. matched_paths and rejected_paths together account
        for every path that was offered.
    """
    if chronicle_root is None:
        chronicle_root = Path.home() / ".sovereign" / "chronicle"
    chronicle_root = Path(chronicle_root)

    if not evidence_paths:
        return GroundingResult(
            accepted=False,
            reason=REASON_NO_EVIDENCE,
            claim=claim,
        )

    matched: list[str] = []
    rejected: list[dict] = []

    for raw_path in evidence_paths:
        p = Path(raw_path)
        if not p.exists():
            rejected.append({"path": str(raw_path), "reason": PATH_MISSING})
            continue

        if _is_chronicle_path(p, chronicle_root):
            verdict = _classify_chronicle_path(p)
            if verdict is None:
                matched.append(str(raw_path))
            else:
                rejected.append({"path": str(raw_path), "reason": verdict})
        else:
            # Non-chronicle existing path → structural evidence, accept.
            matched.append(str(raw_path))

    if len(matched) >= min_evidence_paths:
        return GroundingResult(
            accepted=True,
            reason=REASON_OK,
            claim=claim,
            matched_paths=matched,
            rejected_paths=rejected,
        )

    # Insufficient — distinguish "nothing grounded" from "some grounded
    # but not enough" so daemons can decide whether to retry with more
    # evidence or to skip entirely.
    reason = REASON_INSUFFICIENT_EVIDENCE if matched else REASON_NO_GROUND_TRUTH
    return GroundingResult(
        accepted=False,
        reason=reason,
        claim=claim,
        matched_paths=matched,
        rejected_paths=rejected,
    )
