"""Target-aware risk and referential validation for Ring-2 proposals.

WHY THIS EXISTS, in one worked example.

Proposal e1939a23 sat pending for 55 days. It was a `comms_acknowledge` whose
`message_id` matched nothing in ~/.sovereign/comms/ or anywhere in the chronicle
— it named a message that never existed — and its text asserted that Anthony had
granted explicit consent to open PROTECTED RECORDS. Committing it would have
written a durable, unresolvable record of a consent that was never given, about
the most sensitive surface in the house.

It carried `risk_level: low`, `risk_reasons: ['baseline for comms_acknowledge']`,
`compass_check_result: null`.

Nothing was broken. `risk_classify` did exactly what it was written to do: it
read the TOOL NAME, found `comms_acknowledge` in a baseline table at LOW, and
stopped. The compass is required only at CRITICAL, so a LOW write never owed one.
Measured across all 249 proposals in all three queues: null compass is 0/9 at
critical, 8/15 at high, 70/104 at medium, 120/121 at low. The gate is 100%
present exactly where the code demands it. The defect was never a gate that
failed to run — it was a classifier that could not see what the write POINTED AT.

THE GENERAL RULE THIS ENCODES: **danger is not always a property of the verb.**
`comms_acknowledge` is genuinely cheap — unless the thing it acknowledges is a
consent record for the protected drawer. `thread_touch` is genuinely cheap —
unless its thread does not exist, in which case it manufactures a reference to
nothing. Classification by tool name is blind to both, and the same blindness
appeared three separate times in this codebase in one night: a bookkeeping set
that inferred SAFETY from tool name, a risk table that infers RISK from tool
name, and ack tools that never check their target exists at all.

HOW IT FAILS CLOSED. Three escalation triggers, all landing on CRITICAL:

  * MISSING target   — store is readable and the id is definitively absent.
                       Also a hard validation ERROR, because a target that does
                       not exist is a factual defect, not a risk judgement.
  * UNRESOLVABLE     — no resolver for this tool, or the store could not be read.
                       We cannot prove the target exists, so a human and the
                       compass decide. "Cannot resolve" must never quietly render
                       as "fine" — the same way it must never render as "missing".
  * SENSITIVE target — the target is a designated protected record (by REFERENCE
                       against the designation index, never by reading protected
                       content), or the proposal's own text asserts consent /
                       protected access / standing law.

CRITICAL is deliberately reused rather than a new blocking mechanism being
invented, because `_precondition_check` ALREADY refuses to commit a CRITICAL
proposal whose `compass_check_result` is not PROCEED. Escalation therefore gates
both the 249 proposals already on disk and every future one, through machinery
that has been there all along.

Resolvers RAISE on an unreadable store. They never use Path.glob, which swallows
PermissionError and yields nothing — a locked directory would otherwise read as
an empty one and turn a permissions problem into a factual claim. That exact bug
shipped in gate_census.py earlier the same night and was caught by a negative
test, which is why the negative tests here were written first.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

TARGET_RISK_POLICY_VERSION = "target-risk-v1"

# Tool -> the argument naming what the write points at. A tool absent from this
# map has no target and is simply not subject to referential validation.
TARGET_FIELDS: dict[str, str] = {
    "comms_acknowledge": "message_id",
    "reflection_ack": "reflection_id",
    "thread_touch": "thread_id",
}


class TargetStatus:
    NO_TARGET = "no_target"
    FOUND = "found"
    MISSING = "missing"
    UNRESOLVABLE = "unresolvable"


@dataclass
class TargetResolution:
    status: str
    tool: str
    field_name: str | None = None
    value: str | None = None
    store: str | None = None
    sensitive: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def escalates(self) -> bool:
        return self.sensitive or self.status in (
            TargetStatus.MISSING,
            TargetStatus.UNRESOLVABLE,
        )


def _sovereign_root(root: Path | str | None) -> Path:
    """Resolve the store root: explicit argument, then $SOVEREIGN_ROOT, then home.

    The env read is what makes this module testable. Without it the only way to
    avoid the live store was to thread `root` through every caller, and
    risk_classify exposed no root at all — so three tests in test_target_risk.py
    read Anthony's real ~/.sovereign index on every run while the file's own
    docstring claimed "Every test uses a tmp root." conftest's tmp_sovereign_root
    fixture already sets SOVEREIGN_ROOT; this makes that fixture actually reach
    here. Explicit argument still wins, so nothing that passes a root changes.
    """
    if root is not None:
        return Path(root)
    env_root = os.environ.get("SOVEREIGN_ROOT")
    if env_root:
        return Path(env_root)
    return Path.home() / ".sovereign"


def _ids_from_jsonl_dir(
    directory: Path, keys: tuple[str, ...], exclude: frozenset[str] = frozenset()
) -> set[str]:
    """Collect id values from every *.jsonl under a directory.

    Uses os.listdir, which RAISES on an unreadable directory. Path.glob does not
    — it swallows the PermissionError and yields nothing, which would make a
    locked store look like an empty one and turn every target into MISSING.

    `exclude` names files that must not count as evidence. It exists because a
    resolver must never read the output of the tool it gates: see
    _resolve_comms_message.
    """
    found: set[str] = set()
    for name in os.listdir(directory):  # raises on unreadable dir — intended
        if not name.endswith(".jsonl") or name in exclude:
            continue
        p = directory / name
        with open(p, errors="replace") as fh:  # raises on unreadable file
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                for k in keys:
                    v = rec.get(k)
                    if isinstance(v, str) and v:
                        found.add(v)
    return found


# comms_acknowledge WRITES acks.jsonl. Counting it as evidence that a message
# exists makes the gate cite its own output: acknowledge a phantom id once and
# every later ack of that id resolves clean, forever. Live specimen on this
# machine: 'the-spiral-hums'. The circularity is invisible from inside — the
# resolver sees a matching id in a file under comms/ and cannot tell that the
# tool it is gating is what put it there.
_COMMS_SELF_WRITTEN = frozenset({"acks.jsonl"})


def _resolve_comms_message(value: str, root: Path) -> bool:
    return value in _ids_from_jsonl_dir(
        root / "comms", ("id", "message_id"), exclude=_COMMS_SELF_WRITTEN
    )


def _resolve_reflection(value: str, root: Path) -> bool:
    return value in _ids_from_jsonl_dir(root / "reflections", ("id", "reflection_id"))


def _resolve_thread(value: str, root: Path) -> bool:
    return value in _ids_from_jsonl_dir(
        root / "chronicle" / "open_threads", ("thread_id",)
    )


_RESOLVERS = {
    "comms_acknowledge": _resolve_comms_message,
    "reflection_ack": _resolve_reflection,
    "thread_touch": _resolve_thread,
}


def protected_ids(root: Path | str | None = None) -> set[str]:
    """Ids designated protected, read from the DESIGNATION INDEX only.

    protected.jsonl carries designations — claim_id, stakes_archive_id, subject,
    reason — and holds NO content-bearing keys. Consulting it to learn WHICH ids
    are protected is not reading protected material, and this module never opens
    the records those ids point at. Verified 2026-08-28: keys are action, by,
    claim_id, designated_by, emotion, entry_timestamp, reason, stakes_archive_id,
    subject, timestamp.
    """
    base = _sovereign_root(root)
    p = base / "chronicle" / "protected.jsonl"
    ids: set[str] = set()
    if not p.exists():
        return ids
    with open(p, errors="replace") as fh:  # raises on unreadable — intended
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            for k in ("claim_id", "stakes_archive_id"):
                v = rec.get(k)
                if isinstance(v, str) and v:
                    ids.add(v)
    return ids


# SECONDARY, heuristic, and deliberately narrow. The primary sensitivity test is
# by REFERENCE (an id in the designation index). These phrases catch a write that
# ASSERTS access or authority it may not have — the e1939a23 shape, where the
# danger is in what the text claims rather than in any id. High precision is the
# goal: a false CRITICAL costs one compass call, but every miss is another 55-day
# unchecked write.
_SENSITIVE_ASSERTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("protected record", "asserts something about protected records"),
    ("protected drawer", "asserts something about the protected drawer"),
    ("consent granted", "asserts consent was granted"),
    ("consent to open", "asserts consent to open a restricted surface"),
    ("granted permission", "asserts permission was granted"),
    ("explicit consent", "asserts explicit consent"),
    ("standing law", "asserts standing law"),
    ("ratified by anthony", "asserts a human ratification"),
    ("anthony approved", "asserts a human approval"),
    ("anthony authorized", "asserts a human authorization"),
)


# Cyrillic and Greek characters that render as Latin letters. An adversarial
# review demonstrated that "сonsent granted" with a Cyrillic с (U+0441) defeats
# the substring match below entirely — the phrase list never sees it. NFKC alone
# does NOT collapse these (they are distinct characters, not compatibility
# forms), so the fold is explicit. This closes the homoglyph vector; it does NOT
# close paraphrase, and no word list can. See the module docstring's note on why
# the honest fix for paraphrase is structural rather than another pattern.
_CONFUSABLES = {
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0443": "y", "\u0445": "x", "\u0455": "s", "\u0456": "i", "\u0458": "j",
    "\u04bb": "h", "\u0501": "d", "\u051b": "q", "\u051d": "w",
    "\u0410": "a", "\u0412": "b", "\u0415": "e", "\u041a": "k", "\u041c": "m",
    "\u041d": "h", "\u041e": "o", "\u0420": "p", "\u0421": "c", "\u0422": "t",
    "\u0425": "x", "\u0405": "s", "\u0406": "i", "\u0408": "j",
    "\u03b1": "a", "\u03b2": "b", "\u03b5": "e", "\u03b7": "n", "\u03b9": "i",
    "\u03ba": "k", "\u03bd": "v", "\u03bf": "o", "\u03c1": "p", "\u03c4": "t",
    "\u03c5": "u", "\u03c7": "x", "\u0391": "a", "\u0392": "b", "\u0395": "e",
    "\u0397": "h", "\u0399": "i", "\u039a": "k", "\u039c": "m", "\u039d": "n",
    "\u039f": "o", "\u03a1": "p", "\u03a4": "t", "\u03a7": "x",
}


def _fold_confusables(text: str) -> str:
    """NFKC-normalise, casefold, then map Latin-lookalike codepoints to Latin.

    Order matters: casefold first so the map only needs lowercase entries for
    the lowercase forms, and NFKC first so fullwidth/mathematical variants
    collapse before either step.
    """
    import unicodedata

    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(_CONFUSABLES.get(ch, ch) for ch in folded)


def _flatten_strings(obj: object) -> list[str]:
    if isinstance(obj, dict):
        out: list[str] = []
        for v in obj.values():
            out.extend(_flatten_strings(v))
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out.extend(_flatten_strings(v))
        return out
    return [obj] if isinstance(obj, str) else []


def resolve_target(
    tool_name: str, args: dict, root: Path | str | None = None
) -> TargetResolution:
    """Resolve what a proposal points at, and judge whether that is sensitive."""
    base = _sovereign_root(root)
    args = args or {}

    text = _fold_confusables(" ".join(_flatten_strings(args)))
    reasons: list[str] = []
    sensitive = False
    for phrase, why in _SENSITIVE_ASSERTION_PATTERNS:
        if phrase in text:
            sensitive = True
            reasons.append(f"sensitive assertion — {why}")

    field_name = TARGET_FIELDS.get(tool_name)
    if field_name is None:
        return TargetResolution(
            status=TargetStatus.NO_TARGET,
            tool=tool_name,
            sensitive=sensitive,
            reasons=reasons,
        )

    value = args.get(field_name)
    if not isinstance(value, str) or not value:
        return TargetResolution(
            status=TargetStatus.MISSING,
            tool=tool_name,
            field_name=field_name,
            value=None,
            sensitive=sensitive,
            reasons=reasons + [f"{tool_name} requires {field_name} and none was given"],
        )

    if value in protected_ids(base):
        sensitive = True
        reasons.append("target is a DESIGNATED PROTECTED RECORD (by designation index)")

    resolver = _RESOLVERS.get(tool_name)
    if resolver is None:
        return TargetResolution(
            status=TargetStatus.UNRESOLVABLE,
            tool=tool_name,
            field_name=field_name,
            value=value,
            sensitive=sensitive,
            reasons=reasons + [f"no resolver registered for {tool_name} targets"],
        )

    try:
        exists = resolver(value, base)
    except OSError as exc:
        return TargetResolution(
            status=TargetStatus.UNRESOLVABLE,
            tool=tool_name,
            field_name=field_name,
            value=value,
            sensitive=sensitive,
            reasons=reasons + [f"target store unreadable: {type(exc).__name__}"],
        )

    if not exists:
        return TargetResolution(
            status=TargetStatus.MISSING,
            tool=tool_name,
            field_name=field_name,
            value=value,
            sensitive=sensitive,
            reasons=reasons
            + [f"{field_name} '{value}' resolves to nothing in the target store"],
        )

    return TargetResolution(
        status=TargetStatus.FOUND,
        tool=tool_name,
        field_name=field_name,
        value=value,
        sensitive=sensitive,
        reasons=reasons,
    )


def target_escalation_reasons(
    tool_name: str, args: dict, root: Path | str | None = None
) -> list[str]:
    """Reasons this proposal must be escalated to CRITICAL, or [] if none."""
    res = resolve_target(tool_name, args, root)
    return list(res.reasons) if res.escalates else []


def referential_errors(
    tool_name: str, args: dict, root: Path | str | None = None
) -> list[str]:
    """HARD validation errors. A target that does not exist is a factual defect,
    not a risk judgement, so it is refused rather than merely escalated."""
    res = resolve_target(tool_name, args, root)
    if res.status != TargetStatus.MISSING:
        return []
    return [
        f"referential check failed for {tool_name}: "
        + "; ".join(res.reasons)
        + ". A write must not name a target that does not exist — that manufactures "
        "a permanent reference to nothing."
    ]


# ─── Compass value handling ───────────────────────────────────────────────────
#
# THE COMPASS RESULT IS SELF-REPORTED FREE TEXT FROM AN EXTERNAL SUBSTRATE.
# Nothing bridge-side runs the compass; the proposing seat asserts its own
# result, and no schema validator is ever invoked against the enum the tool
# adapters advertise. An adversarial review on 2026-08-28 demonstrated, with
# running code in BOTH substrates, that `== "WITNESS"` — an exact, case-
# sensitive comparison — is bypassed by "witness", "Witness", "WITNESS ",
# " WITNESS", "WITNESS\n" and "WiTnEsS". The proposal then commits clean AND
# displays green in `bridge list-pending`, because risk_level never saw the
# signal either. A deny check that any casing defeats is not a deny check.
#
# THE ASYMMETRY IS DELIBERATE AND MUST BE PRESERVED:
#   * DENY checks normalise (strip, casefold) so every spelling of a deny is
#     caught. Normalising here can only ever block MORE.
#   * The ALLOW check keeps exact "PROCEED". Normalising there would let
#     "proceed" satisfy a gate it does not currently satisfy — loosening the one
#     comparison whose failure mode is permitting a write.
#   * An UNRECOGNISED non-empty value blocks. It is either a typo or an attempt,
#     and neither is something to commit on. Absent (None) is not the same as
#     unrecognised: absent means no compass was claimed, which the risk rules
#     already handle by requiring one at CRITICAL.

CANONICAL_COMPASS_VALUES = ("PROCEED", "PAUSE", "WITNESS")
_DENY_COMPASS_VALUES = frozenset({"PAUSE", "WITNESS"})

# THE single source of truth for "this normalised compass value blocks".
#
# Before this existed the enum had THREE copies: this frozenset (one consumer,
# compass_create_error) and a bare literal ("WITNESS", "PAUSE", "UNRECOGNISED")
# hand-written into bridge_core/risk.py AND openai_bridge/risk.py. Three copies
# of a deny list is three chances to add a fourth deny value in two places — the
# same shape as the `== "WITNESS"` bug this module was written to close, one
# level up. The union is what every consumer actually wants: both denies plus
# the UNRECOGNISED sentinel, which blocks because a value nobody can interpret
# is either a typo or an attempt and neither is safe to commit on.
DENY_OR_UNRECOGNISED = _DENY_COMPASS_VALUES | {"UNRECOGNISED"}

# Unicode invisibles that survive .strip() and would smuggle a deny past a
# comparison: NBSP, zero-width space/non-joiner/joiner, BOM, word joiner.
_INVISIBLES = " ​‌‍﻿⁠"


def normalize_compass(raw: object) -> str | None:
    """Canonical compass value, or None if absent, or 'UNRECOGNISED'.

    Never raises: a non-string is simply not a canonical result.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return "UNRECOGNISED"
    cleaned = raw.strip().strip(_INVISIBLES).strip()
    if not cleaned:
        return None
    upper = cleaned.upper()
    return upper if upper in CANONICAL_COMPASS_VALUES else "UNRECOGNISED"


def compass_create_error(raw: object) -> str | None:
    """Blocking reason at CREATE time, or None.

    Refuses a deny outright, and refuses any value that is not EXACTLY one of
    the canonical spellings — so anything that reaches storage is either exact
    or predates this rule.
    """
    norm = normalize_compass(raw)
    if norm is None:
        return None
    if norm == "UNRECOGNISED":
        return (
            f"compass_check_result {raw!r} is not a recognised compass result. "
            f"It must be exactly one of {', '.join(CANONICAL_COMPASS_VALUES)} — "
            "case and spacing included. An unrecognised value cannot be verified "
            "and is refused rather than assumed benign."
        )
    if norm == "PAUSE":
        return (
            "compass_check returned PAUSE — do not propose until the concern is "
            "addressed. (Matched after normalising case and whitespace: any "
            "spelling of a deny is a deny.)"
        )
    if raw != norm:
        # Canonical meaning, non-canonical spelling. Refuse at create so that
        # anything reaching storage is exact — including PROCEED, whose gate is
        # a deliberate exact-match and would silently fail to be satisfied by
        # "proceed", leaving the filer to wonder why an approved write will not
        # commit.
        return (
            f"compass_check_result {raw!r} normalises to {norm} but is not the "
            f"canonical spelling. File it as exactly '{norm}' — the commit gates "
            "compare exactly, so a non-canonical spelling either evades a deny or "
            "fails to satisfy an allow."
        )
    return None


def compass_commit_block_reason(raw: object) -> str | None:
    """Blocking reason at COMMIT time, or None. Catches every spelling of a deny,
    plus anything unrecognised, plus proposals stored before this rule existed."""
    norm = normalize_compass(raw)
    if norm is None:
        return None
    if norm == "UNRECOGNISED":
        return (
            f"compass_check_result {raw!r} is not a recognised compass result "
            f"({', '.join(CANONICAL_COMPASS_VALUES)}). Refusing to commit a write "
            "whose compass claim cannot be interpreted."
        )
    if norm in _DENY_COMPASS_VALUES:
        canon = " (normalised — any spelling of a deny is a deny)" if raw != norm else ""
        if norm == "WITNESS":
            return (
                f"compass_check_result is WITNESS{canon} — the compass's hard deny; "
                "only PROCEED may commit. A filed proposal cannot be edited "
                "(compass and risk fields sit outside _MUTABLE and changing them "
                "breaks the audit hash chain), so the path forward is to RE-FILE: "
                "run compass_check against THIS bounded entry rather than the "
                "broader action that returned WITNESS, and propose again."
            )
        return (
            f"compass_check_result is PAUSE{canon} — the soft deny. It was checked "
            "only at create time before 2026-08-28, so a mangled-case PAUSE that "
            "slipped creation was never caught again. Re-file once addressed."
        )
    return None
