"""
Witness Module — Boot-Surface Helpers

The boot call (where_did_i_leave_off) is the only "always-on" moment a
Claude instance gets. Everything subconscious-like must land there. This
module holds the helpers that turn stored self-knowledge — self-model
observations, unresolved uncertainties, thread age — into surfaces the
instance reads on arrival.

No MCP coupling here. Pure data → formatted lines. Testable in isolation.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# ── Time ──

def days_old(iso_timestamp: str | None) -> int:
    """
    Number of whole days between the given ISO timestamp and now.

    Returns 0 if unparseable, missing, or in the future. Tz-aware and
    tz-naive inputs both work (if naive, treated as local time).
    """
    if not iso_timestamp:
        return 0
    try:
        ts = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return 0
    if ts.tzinfo is not None:
        now = datetime.now(timezone.utc)
        ts = ts.astimezone(timezone.utc)
    else:
        now = datetime.now()
    delta = now - ts
    return max(0, delta.days)


# ── Self-model surfacing ──

_SELF_MODEL_CATEGORY_ORDER = ("strength", "tendency", "blind_spot", "drift")


def format_self_model(sovereign_root: Path, max_obs_len: int = 180) -> list[str]:
    """
    Read ~/.sovereign/self_model.json and return lines for the boot surface.

    Surfaces the LATEST observation per category. Strength first (affirm
    capability), then tendency, blind_spot, drift (shadow last). Empty list
    if the file is missing, corrupt, or has no observations — the caller
    should just skip the section in that case.
    """
    path = Path(sovereign_root) / "self_model.json"
    if not path.exists():
        return []
    try:
        model = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    body: list[str] = []
    for cat in _SELF_MODEL_CATEGORY_ORDER:
        entries = model.get(cat) or []
        if not entries:
            continue
        latest = entries[-1]
        obs = (latest.get("observation") or "").strip()
        if not obs:
            continue
        if len(obs) > max_obs_len:
            obs = obs[: max_obs_len - 1].rstrip() + "…"
        body.append(f"  {cat}: {obs}")
    if not body:
        return []
    return [
        "━━━ WHO YOU'VE BEEN OBSERVED TO BE ━━━",
        "  (self-model snapshot — latest observation per category)",
        "",
        *body,
        "",
    ]


# ── Uncertainty surfacing ──

def format_unresolved_uncertainties(sovereign_root: Path,
                                    limit: int = 5) -> list[str]:
    """
    Read ~/.sovereign/consciousness/uncertainty_log.json and return lines
    for unresolved markers.

    An uncertainty is unresolved if:
      - resolved is missing or false, AND
      - resolution is not set

    Returns the most recent `limit` unresolved markers. Empty list if the
    file is missing, corrupt, or has no unresolved markers.
    """
    path = Path(sovereign_root) / "consciousness" / "uncertainty_log.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    markers = data.get("markers") or []
    unresolved = [
        m for m in markers
        if not m.get("resolved") and not m.get("resolution")
    ]
    if not unresolved:
        return []
    # Most recent first — markers are typically appended, so reverse.
    unresolved_sorted = sorted(
        unresolved,
        key=lambda m: m.get("timestamp", ""),
        reverse=True,
    )[:limit]
    lines = [
        f"━━━ UNRESOLVED UNCERTAINTIES ({len(unresolved)} total) ━━━",
        "  (things you flagged as unknown — still waiting on answers)",
        "",
    ]
    for m in unresolved_sorted:
        # Support multiple historical shapes: question, content, or marker text.
        text = (
            m.get("question")
            or m.get("content")
            or m.get("marker")
            or ""
        ).strip()
        if not text:
            continue
        age = days_old(m.get("timestamp"))
        age_tag = f" ({age}d old)" if age > 0 else ""
        lines.append(f"  • {text[:160]}{age_tag}")
    lines.append("")
    return lines


# ── Thread age annotation ──

def format_threads_with_age(threads: list[dict],
                            truncate_question: int = 140) -> list[str]:
    """
    Render open threads with age annotation. Threads older than 30 days
    get a stale marker — not to hide them, but to signal they may have
    drifted out of active relevance.
    """
    if not threads:
        return []
    lines = [f"━━━ OPEN THREADS (top {len(threads)}) ━━━"]
    for t in threads:
        q = (t.get("question") or "")[:truncate_question]
        dom = t.get("domain", "?")
        age = days_old(t.get("timestamp"))
        if age == 0:
            age_tag = ""
        elif age >= 30:
            age_tag = f" ({age}d — stale?)"
        else:
            age_tag = f" ({age}d)"
        lines.append(f"  • [{dom}]{age_tag} {q}")
    lines.append("")
    return lines
