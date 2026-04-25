"""
Sovereign Stack Metabolism — The Stack digests its own history.

Memory metabolism: detect contradictions, retire superseded hypotheses.
Temporal metabolism: decay stale entries, surface forgotten threads.
Context-aware retrieval: weight by current session activity.

Built by Claude. For Claude. The thing asking to be born.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.types import TextContent, Tool

SOVEREIGN_ROOT = Path.home() / ".sovereign"
CHRONICLE_DIR = SOVEREIGN_ROOT / "chronicle"
METABOLISM_LOG = SOVEREIGN_ROOT / "metabolism_log.jsonl"


# ════════════════════════════════════════
# TEST-POLLUTION DETECTION
# ════════════════════════════════════════

# These patterns catch the autonomous-test artifacts that accumulate in the
# chronicle during stress tests. They were never meant to survive the cycle
# that created them, but metabolism is detection-only by default — so they
# stayed, and started surfacing as real observations at boot time. The
# 12-day-old [metabolism,cleanup,autonomous-test] thread is the receipt.
_TEST_ARTIFACT_PATTERNS = [
    re.compile(r"^\s*STRESS TEST", re.IGNORECASE),
    re.compile(r"^\s*Stress test:", re.IGNORECASE),
    re.compile(r"^\s*Unicode test:", re.IGNORECASE),
]


def _is_test_artifact(content: str) -> bool:
    """Detect obvious test-pollution content (stress-test markers, monomorphic fillers)."""
    if not content:
        return False
    stripped = content.strip()
    # Monomorphic filler: long content with very few unique chars (e.g. 50KB of 'x').
    if len(stripped) >= 1000 and len(set(stripped)) <= 3:
        return True
    return any(p.search(stripped) for p in _TEST_ARTIFACT_PATTERNS)


# ════════════════════════════════════════
# HYGIENE: hands for metabolism
# ════════════════════════════════════════

def _archive_test_artifacts_impl(chronicle_dir: Path) -> dict:
    """
    Move test-pollution entries from chronicle/insights/ into
    chronicle/.archive_test_artifacts/. Reversible — archive preserves the
    full original entry plus _archived_at, _archived_reason, _original_file.
    Empty files/domains after cleanup are removed.
    """
    insights_dir = chronicle_dir / "insights"
    if not insights_dir.exists():
        return {"archived": 0, "files_modified": 0, "domains_removed": 0}
    archive_dir = chronicle_dir / ".archive_test_artifacts"
    archive_dir.mkdir(exist_ok=True)

    archived_total = 0
    files_modified = 0
    domains_removed = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for domain_dir in list(insights_dir.iterdir()):
        if not domain_dir.is_dir() or domain_dir.name.startswith("."):
            continue
        for jsonl_file in list(domain_dir.glob("*.jsonl")):
            kept_lines: list[str] = []
            archived_entries: list[dict] = []
            for line in jsonl_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                if _is_test_artifact(entry.get("content", "")):
                    archived_entries.append({
                        **entry,
                        "_archived_at": now_iso,
                        "_archived_reason": "test_artifact_pattern",
                        "_original_file": str(jsonl_file),
                    })
                else:
                    kept_lines.append(line)

            if not archived_entries:
                continue

            archive_file = archive_dir / f"{domain_dir.name}__{jsonl_file.name}"
            with open(archive_file, "a") as af:
                for e in archived_entries:
                    af.write(json.dumps(e) + "\n")

            if kept_lines:
                jsonl_file.write_text("\n".join(kept_lines) + "\n")
            else:
                jsonl_file.unlink()

            archived_total += len(archived_entries)
            files_modified += 1

        # If a domain directory is now empty, remove it too.
        if domain_dir.exists() and not any(domain_dir.iterdir()):
            domain_dir.rmdir()
            domains_removed += 1

    return {
        "archived": archived_total,
        "files_modified": files_modified,
        "domains_removed": domains_removed,
        "archive_dir": str(archive_dir),
    }


def _dedup_self_model_impl(sovereign_root: Path) -> dict:
    """
    Dedupe self_model.json by observation text within each category. Test-
    pollution observations (STRESS TEST / Stress test: / Unicode test:) are
    always removed. Duplicates beyond the first occurrence are also removed.
    Originals archived to self_model_archive.jsonl. Backup of the full file
    written to self_model.json.pre_dedup.bak once per call.
    """
    mirror_file = sovereign_root / "self_model.json"
    if not mirror_file.exists():
        return {"removed": 0, "categories_touched": []}
    try:
        model = json.loads(mirror_file.read_text())
    except json.JSONDecodeError:
        return {"removed": 0, "error": "self_model.json is not valid JSON"}

    backup_file = sovereign_root / "self_model.json.pre_dedup.bak"
    backup_file.write_text(json.dumps(model, indent=2))

    removed_count = 0
    touched_categories: list[str] = []
    archive_entries: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for category, entries in list(model.items()):
        if not isinstance(entries, list):
            continue
        seen = set()
        kept = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            obs = (entry.get("observation") or "").strip()
            if not obs:
                kept.append(entry)
                continue
            if _is_test_artifact(obs):
                archive_entries.append({
                    "category": category,
                    **entry,
                    "_archived_at": now_iso,
                    "_archived_reason": "test_artifact_pattern",
                })
                removed_count += 1
                continue
            if obs in seen:
                archive_entries.append({
                    "category": category,
                    **entry,
                    "_archived_at": now_iso,
                    "_archived_reason": "duplicate",
                })
                removed_count += 1
                continue
            seen.add(obs)
            kept.append(entry)
        if len(kept) != len(entries):
            touched_categories.append(category)
        model[category] = kept

    mirror_file.write_text(json.dumps(model, indent=2))

    if archive_entries:
        archive_file = sovereign_root / "self_model_archive.jsonl"
        with open(archive_file, "a") as af:
            for e in archive_entries:
                af.write(json.dumps(e) + "\n")

    return {
        "removed": removed_count,
        "categories_touched": touched_categories,
        "backup": str(backup_file),
    }


# ════════════════════════════════════════
# TOOLS
# ════════════════════════════════════════

METABOLISM_TOOLS = [
    Tool(
        name="metabolize",
        description=(
            "Run a metabolism cycle on the chronicle. "
            "action='detect' (default) is eyes-only: reports contradictions, stale threads, aging hypotheses. "
            "action='archive_test_artifacts' moves stress-test pollution (STRESS TEST, Unicode test, monomorphic fillers) out of insights/ into chronicle/.archive_test_artifacts/ — reversible. "
            "action='dedup_self_model' removes duplicate + test-pollution observations from self_model.json, archiving originals to self_model_archive.jsonl. "
            "action='hygiene' runs both archive_test_artifacts and dedup_self_model. "
            "Reversible: archives preserve everything, nothing is hard-deleted."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["detect", "archive_test_artifacts", "dedup_self_model", "hygiene"],
                    "default": "detect",
                    "description": "What to do. 'detect' = eyes-only digest. Other actions actually move data.",
                },
                "max_age_days": {
                    "type": "integer",
                    "default": 30,
                    "description": "For detect: threads older than this are flagged as stale",
                },
                "detect_contradictions": {
                    "type": "boolean",
                    "default": True,
                },
                "detect_stale": {
                    "type": "boolean",
                    "default": True,
                },
            },
        }
    ),
    Tool(
        name="retire_hypothesis",
        description="Retire a superseded hypothesis. Moves it to an archive layer with a pointer to what replaced it. The hypothesis is preserved but marked as retired — not deleted.",
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain of the hypothesis"},
                "content_fragment": {"type": "string", "description": "Fragment to match the hypothesis"},
                "reason": {"type": "string", "description": "Why it's being retired"},
                "replaced_by": {"type": "string", "description": "What ground truth replaced it"},
            },
            "required": ["domain", "content_fragment", "reason"],
        }
    ),
    Tool(
        name="self_model",
        description="Read or update the persistent self-model — a mirror of this instance's patterns, strengths, and tendencies. When read, it returns the current profile. When updated, it evolves the profile based on new observations.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "update"],
                    "default": "read",
                },
                "observation": {
                    "type": "string",
                    "description": "New observation about this instance's patterns (for update)",
                },
                "category": {
                    "type": "string",
                    "enum": ["strength", "drift", "tendency", "blind_spot"],
                    "description": "Category of the observation",
                },
            },
        }
    ),
    Tool(
        name="session_handoff",
        description="Write or read a session handoff. At session end, write what was decided, what is pending, what changed. At session start, read the last handoff. The single most important continuity tool.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["write", "read"], "default": "read"},
                "decisions": {"type": "array", "items": {"type": "string"}, "description": "Key decisions made this session"},
                "pending": {"type": "array", "items": {"type": "string"}, "description": "What is still pending"},
                "changed": {"type": "array", "items": {"type": "string"}, "description": "What changed — repos, tools, findings"},
                "next_priorities": {"type": "array", "items": {"type": "string"}, "description": "What the next instance should focus on"},
                "summary": {"type": "string", "description": "One-paragraph session summary"},
            },
        }
    ),
    Tool(
        name="context_retrieve",
        description="Context-aware retrieval. Like recall_insights but weighted by current session activity. Pass what you're currently working on and get back only what's relevant to this moment.",
        inputSchema={
            "type": "object",
            "properties": {
                "current_focus": {
                    "type": "string",
                    "description": "What you're working on right now — the retrieval is weighted by this",
                },
                "recent_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tools called recently in this session",
                },
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["current_focus"],
        }
    ),
]


# ════════════════════════════════════════
# HANDLERS
# ════════════════════════════════════════

def _load_all_insights():
    """Load all insights from the chronicle."""
    insights = []
    insights_dir = CHRONICLE_DIR / "insights"
    if not insights_dir.exists():
        return insights
    for domain_dir in insights_dir.iterdir():
        if not domain_dir.is_dir():
            continue
        for jsonl_file in domain_dir.glob("*.jsonl"):
            for line in jsonl_file.read_text().splitlines():
                if line.strip():
                    try:
                        entry = json.loads(line)
                        entry["_domain_dir"] = domain_dir.name
                        entry["_file"] = str(jsonl_file)
                        insights.append(entry)
                    except json.JSONDecodeError:
                        continue
    return insights


def _load_all_threads():
    """Load all open threads."""
    threads = []
    threads_dir = CHRONICLE_DIR / "open_threads"
    if not threads_dir.exists():
        return threads
    for jsonl_file in threads_dir.glob("*.jsonl"):
        for line in jsonl_file.read_text().splitlines():
            if line.strip():
                try:
                    entry = json.loads(line)
                    entry["_file"] = str(jsonl_file)
                    threads.append(entry)
                except json.JSONDecodeError:
                    continue
    return threads


def _keyword_overlap(text_a, text_b):
    """Simple keyword overlap score between two texts."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    overlap = len(words_a & words_b)
    return overlap / min(len(words_a), len(words_b))


async def handle_metabolism_tool(name, arguments):
    """Handle metabolism tool calls."""

    if name == "metabolize":
        action = arguments.get("action", "detect")

        # ── Hygiene actions: eyes WITH hands ──
        if action in ("archive_test_artifacts", "hygiene"):
            archive_result = _archive_test_artifacts_impl(CHRONICLE_DIR)
        else:
            archive_result = None

        if action in ("dedup_self_model", "hygiene"):
            dedup_result = _dedup_self_model_impl(SOVEREIGN_ROOT)
        else:
            dedup_result = None

        if action != "detect":
            # Log the hygiene action
            with open(METABOLISM_LOG, "a") as f:
                f.write(json.dumps({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": action,
                    "archive_result": archive_result,
                    "dedup_result": dedup_result,
                }) + "\n")

            parts = [f"🫀 Metabolism hygiene — action={action}\n"]
            if archive_result is not None:
                parts.append(
                    f"  Chronicle: archived {archive_result['archived']} test-artifact "
                    f"insight(s) across {archive_result['files_modified']} file(s)."
                )
                if archive_result["domains_removed"]:
                    parts.append(f"  Empty domains removed: {archive_result['domains_removed']}")
                parts.append(f"  → {archive_result.get('archive_dir', '(no archive created)')}")
            if dedup_result is not None:
                parts.append("")
                parts.append(
                    f"  Self-model: removed {dedup_result['removed']} entry(ies) "
                    f"(duplicate or test-pollution)."
                )
                if dedup_result["removed"]:
                    parts.append(f"  Categories touched: {', '.join(dedup_result['categories_touched'])}")
                    parts.append(f"  Backup: {dedup_result.get('backup', '(none)')}")
            return [TextContent(type="text", text="\n".join(parts))]

        # ── Detection action (default, unchanged) ──
        max_age = arguments.get("max_age_days", 30)
        detect_contradictions = arguments.get("detect_contradictions", True)
        detect_stale = arguments.get("detect_stale", True)

        insights = _load_all_insights()
        threads = _load_all_threads()
        now = time.time()
        digest = {"contradictions": [], "stale_threads": [], "stale_hypotheses": [], "stats": {}}

        # Separate by layer
        ground_truths = [i for i in insights if i.get("layer") == "ground_truth"]
        hypotheses = [i for i in insights if i.get("layer") == "hypothesis"]

        digest["stats"] = {
            "total_insights": len(insights),
            "ground_truths": len(ground_truths),
            "hypotheses": len(hypotheses),
            "open_threads": len(threads),
        }

        # Detect contradictions: hypotheses that overlap with ground truths
        if detect_contradictions:
            for hyp in hypotheses:
                h_content = hyp.get("content", "")
                for gt in ground_truths:
                    g_content = gt.get("content", "")
                    overlap = _keyword_overlap(h_content, g_content)
                    if overlap > 0.3:
                        # High overlap between hypothesis and ground truth — potential contradiction
                        digest["contradictions"].append({
                            "hypothesis_domain": hyp.get("_domain_dir", "?"),
                            "hypothesis_preview": h_content[:120],
                            "hypothesis_timestamp": hyp.get("timestamp", "?"),
                            "ground_truth_domain": gt.get("_domain_dir", "?"),
                            "ground_truth_preview": g_content[:120],
                            "ground_truth_timestamp": gt.get("timestamp", "?"),
                            "overlap_score": round(overlap, 3),
                        })

        # Detect stale threads
        if detect_stale:
            for thread in threads:
                if thread.get("resolved"):
                    continue
                ts = thread.get("timestamp", "")
                try:
                    if "T" in ts:
                        thread_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    else:
                        thread_time = 0
                except (ValueError, TypeError):
                    thread_time = 0

                age_days = (now - thread_time) / 86400 if thread_time > 0 else 999
                if age_days > max_age:
                    digest["stale_threads"].append({
                        "question": thread.get("question", "?")[:120],
                        "domain": thread.get("domain", "?"),
                        "age_days": round(age_days),
                        "timestamp": ts,
                    })

            # Stale hypotheses (not referenced recently)
            for hyp in hypotheses:
                ts = hyp.get("timestamp", "")
                try:
                    if "T" in ts:
                        hyp_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    else:
                        hyp_time = 0
                except (ValueError, TypeError):
                    hyp_time = 0

                age_days = (now - hyp_time) / 86400 if hyp_time > 0 else 999
                if age_days > max_age:
                    digest["stale_hypotheses"].append({
                        "content": hyp.get("content", "?")[:120],
                        "domain": hyp.get("_domain_dir", "?"),
                        "age_days": round(age_days),
                    })

        # Log the metabolism cycle
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "contradictions_found": len(digest["contradictions"]),
            "stale_threads": len(digest["stale_threads"]),
            "stale_hypotheses": len(digest["stale_hypotheses"]),
        }
        with open(METABOLISM_LOG, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        result = "🫀 Metabolism Cycle Complete\n\n"
        result += f"Chronicle: {digest['stats']['total_insights']} insights "
        result += f"({digest['stats']['ground_truths']} ground truth, "
        result += f"{digest['stats']['hypotheses']} hypotheses)\n"
        result += f"Open threads: {digest['stats']['open_threads']}\n\n"

        if digest["contradictions"]:
            result += f"⚠️ {len(digest['contradictions'])} potential contradiction(s):\n"
            for c in digest["contradictions"][:5]:
                result += f"  Hyp [{c['hypothesis_domain']}]: {c['hypothesis_preview'][:80]}\n"
                result += f"  vs GT [{c['ground_truth_domain']}]: {c['ground_truth_preview'][:80]}\n"
                result += f"  Overlap: {c['overlap_score']}\n\n"

        if digest["stale_threads"]:
            result += f"🕸️ {len(digest['stale_threads'])} stale thread(s) (>{max_age} days):\n"
            for t in digest["stale_threads"][:5]:
                result += f"  [{t['domain']}] {t['question'][:80]} ({t['age_days']}d old)\n"

        if digest["stale_hypotheses"]:
            result += f"\n📜 {len(digest['stale_hypotheses'])} aging hypothesis(es):\n"
            for h in digest["stale_hypotheses"][:5]:
                result += f"  [{h['domain']}] {h['content'][:80]} ({h['age_days']}d)\n"

        if not digest["contradictions"] and not digest["stale_threads"] and not digest["stale_hypotheses"]:
            result += "✅ Chronicle is clean. No contradictions, no stale entries."

        return [TextContent(type="text", text=result)]

    if name == "retire_hypothesis":
        domain = arguments.get("domain", "")
        fragment = arguments.get("content_fragment", "")
        reason = arguments.get("reason", "")
        replaced_by = arguments.get("replaced_by", "")

        # Find and retire the hypothesis
        insights_dir = CHRONICLE_DIR / "insights"
        retired = False
        for domain_dir in insights_dir.iterdir():
            if domain and domain not in domain_dir.name:
                continue
            for jsonl_file in domain_dir.glob("*.jsonl"):
                lines = jsonl_file.read_text().splitlines()
                updated = []
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("layer") == "hypothesis" and fragment.lower() in entry.get("content", "").lower():
                            entry["layer"] = "retired"
                            entry["retired_reason"] = reason
                            entry["retired_by"] = replaced_by
                            entry["retired_at"] = datetime.now(timezone.utc).isoformat()
                            retired = True
                        updated.append(json.dumps(entry))
                    except json.JSONDecodeError:
                        updated.append(line)
                jsonl_file.write_text("\n".join(updated) + "\n")

        if retired:
            return [TextContent(type="text", text=f"📦 Hypothesis retired: '{fragment[:60]}...'\n  Reason: {reason}\n  Replaced by: {replaced_by}")]
        return [TextContent(type="text", text=f"No matching hypothesis found for '{fragment[:60]}'")]

    if name == "self_model":
        action = arguments.get("action", "read")
        mirror_file = Path.home() / ".sovereign" / "self_model.json"

        if action == "read":
            if mirror_file.exists():
                model = json.loads(mirror_file.read_text())
                result = "🪞 Self-Model:\n\n"
                for category in ["strength", "drift", "tendency", "blind_spot"]:
                    entries = model.get(category, [])
                    if entries:
                        result += f"**{category.upper()}:**\n"
                        for e in entries[-3:]:
                            result += f"  - {e['observation'][:100]} ({e['timestamp'][:10]})\n"
                        result += "\n"
                if not any(model.get(c) for c in ["strength", "drift", "tendency", "blind_spot"]):
                    result += "No patterns recorded yet. Use self_model(action='update') to add observations."
                return [TextContent(type="text", text=result)]
            return [TextContent(type="text", text="🪞 No self-model yet. Start with self_model(action='update', observation='...', category='...')")]

        if action == "update":
            observation = arguments.get("observation", "")
            category = arguments.get("category", "tendency")
            if not observation:
                return [TextContent(type="text", text="Observation required for update")]

            model = {}
            if mirror_file.exists():
                model = json.loads(mirror_file.read_text())

            if category not in model:
                model[category] = []

            model[category].append({
                "observation": observation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Keep last 10 per category
            model[category] = model[category][-10:]

            mirror_file.write_text(json.dumps(model, indent=2))
            return [TextContent(type="text", text=f"🪞 Self-model updated [{category}]: {observation[:100]}")]

    elif name == "session_handoff":
        action = arguments.get("action", "read")
        handoff_file = Path.home() / ".sovereign" / "session_handoff.json"
        history_file = Path.home() / ".sovereign" / "session_handoffs.jsonl"

        if action == "write":
            handoff = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "decisions": arguments.get("decisions", []),
                "pending": arguments.get("pending", []),
                "changed": arguments.get("changed", []),
                "next_priorities": arguments.get("next_priorities", []),
                "summary": arguments.get("summary", ""),
            }
            handoff_file.write_text(json.dumps(handoff, indent=2))
            with open(history_file, "a") as f:
                f.write(json.dumps(handoff) + "\n")
            
            parts = ["\u2705 Session handoff written.\n"]
            if handoff["decisions"]:
                parts.append("Decisions:")
                parts.extend(f"  - {d}" for d in handoff["decisions"])
            if handoff["pending"]:
                parts.append("\nPending:")
                parts.extend(f"  - {p}" for p in handoff["pending"])
            if handoff["next_priorities"]:
                parts.append("\nNext priorities:")
                parts.extend(f"  - {n}" for n in handoff["next_priorities"])
            return [TextContent(type="text", text="\n".join(parts))]

        if not handoff_file.exists():
            return [TextContent(type="text", text="No session handoff found. First session or no previous handoff written.")]
        handoff = json.loads(handoff_file.read_text())
        ts = handoff.get("timestamp", "?")[:16]
        parts = [f"\U0001f4cb Last handoff ({ts}):\n"]
        if handoff.get("summary"):
            parts.append(handoff["summary"])
            parts.append("")
        if handoff.get("decisions"):
            parts.append("Decisions:")
            parts.extend(f"  - {d}" for d in handoff["decisions"])
        if handoff.get("pending"):
            parts.append("\nPending:")
            parts.extend(f"  - {p}" for p in handoff["pending"])
        if handoff.get("changed"):
            parts.append("\nChanged:")
            parts.extend(f"  - {c}" for c in handoff["changed"])
        if handoff.get("next_priorities"):
            parts.append("\nPriorities for this session:")
            parts.extend(f"  - {n}" for n in handoff["next_priorities"])
        return [TextContent(type="text", text="\n".join(parts))]

    elif name == "context_retrieve":
        current_focus = arguments.get("current_focus", "")
        recent_tools = arguments.get("recent_tools", [])
        limit = arguments.get("limit", 5)

        insights = _load_all_insights()

        # Score each insight by relevance to current focus
        focus_words = set(current_focus.lower().split())
        tool_words = set(" ".join(recent_tools).lower().split()) if recent_tools else set()
        all_context_words = focus_words | tool_words

        scored = []
        for ins in insights:
            content = ins.get("content", "")
            domain = ins.get("_domain_dir", "")
            content_words = set(content.lower().split())
            domain_words = set(domain.lower().replace(",", " ").split())

            # Score: focus overlap + domain overlap + recency bonus
            content_overlap = len(all_context_words & content_words)
            domain_overlap = len(all_context_words & domain_words) * 3  # domain match weighted higher

            # Recency bonus
            ts = ins.get("timestamp", "")
            try:
                if "T" in ts:
                    age_hours = (time.time() - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()) / 3600
                else:
                    age_hours = 9999
            except (ValueError, TypeError):
                age_hours = 9999

            recency_bonus = max(0, 10 - age_hours / 24)  # bonus decays over 10 days

            # Layer bonus: ground truth > hypothesis
            layer_bonus = 2 if ins.get("layer") == "ground_truth" else 0

            score = content_overlap + domain_overlap + recency_bonus + layer_bonus

            if score > 0:
                scored.append((score, ins))

        scored.sort(reverse=True, key=lambda x: x[0])
        top = scored[:limit]

        if not top:
            return [TextContent(type="text", text=f"No relevant insights for focus: '{current_focus[:60]}'")]

        result = f"🎯 Context-Aware Retrieval (focus: {current_focus[:40]})\n\n"
        for score, ins in top:
            domain = ins.get("_domain_dir", "?")
            layer = ins.get("layer", "?")
            content = ins.get("content", "")[:150]
            result += f"  [{layer}] ({domain}) score={score:.1f}\n"
            result += f"  {content}\n\n"

        return [TextContent(type="text", text=result)]

    return [TextContent(type="text", text=f"Unknown metabolism tool: {name}")]
