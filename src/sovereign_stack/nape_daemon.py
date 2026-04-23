"""
Nape Daemon — Runtime Critique Layer for Sovereign Stack

Nape watches every tool call made in a session and flags drift patterns before
they harden into bad habits or false records. The name comes from the persona
in nape_log.md: the observer at the back of the neck, catching what speed misses.

Gesture vocabulary:
  sharp honk  — error detected (declare-before-verify, premature summary)
  uneasy honk — something missed (repeated mistake without learning)
  low honk    — architectural observation (assertion without evidence)
  satisfied   — clean verify-before-declare observed (optional positive signal)

Storage layout under {root}/nape/:
  observations.jsonl  — every observe() call appended here
  honks.jsonl         — every drift honk appended here
  acks.jsonl          — acknowledgments (honk_id -> acked)

Design choice: all storage is append-only JSONL. No deletes, no updates to
existing records. Honks accumulate; acks are a separate overlay. This keeps
the audit trail intact regardless of what the instance claimed to do.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# =============================================================================
# CONSTANTS
# =============================================================================

# Tool calls that count as verification / evidence-gathering.
# When one of these appears recently before a high-confidence assertion or
# a "done" result, the assertion is considered grounded.
VERIFY_TOOL_NAMES: frozenset = frozenset({
    # External tools (passed via nape_observe manually by external callers).
    "Read",
    "Grep",
    "Glob",
    "Bash",
    "scan_thresholds",
    "recall_insights",
    "check_mistakes",
    "guardian_scan",
    "guardian_alerts",
    # Sovereign-stack verify-equivalents (now reached via auto-hook).
    "get_open_threads",
    "get_thread_touches",
    "get_inheritable_context",
    "route",
    "derive",
    "handoff_acted_on_records",
    "comms_get_acks",
    "comms_recall",
})

# Words in a tool result that suggest the instance is declaring completion.
# These are checked case-insensitively in the string representation of result.
DECLARE_WORDS: frozenset = frozenset({
    "done",
    "complete",
    "completed",
    "clean",
    "verified",
    "shipped",
    "ready",
    "finished",
    "fixed",
    "resolved",
    "success",
    "passed",
})

# Tools that write a session summary or handoff — used for premature-summary check.
SUMMARY_TOOL_NAMES: frozenset = frozenset({
    "end_session_review",
    "handoff",
    "close_session",
    "where_did_i_leave_off",
})

# Words in recent results that indicate an unresolved error state.
ERROR_WORDS: frozenset = frozenset({
    "error",
    "traceback",
    "exception",
    "failed",
    "failure",
    "cannot",
    "unable",
    "not found",
    "does not exist",
    "no such",
    "refused",
    "denied",
})

# Pattern name → honk level mapping. Source of truth for the gesture vocabulary.
PATTERN_LEVELS: Dict[str, str] = {
    "declare_before_verify":    "sharp",
    "premature_summary":        "sharp",
    "assertion_without_evidence": "low",
    "repeated_mistake":         "uneasy",
    "stale_context":            "low",      # Phase 2 — detector deferred
    "clean_verify_declare":     "satisfied",
}

# How many recent observations to consider as the sliding window for each check.
WINDOW_DECLARE_VERIFY = 3     # spec: "within last 3 tool calls"
WINDOW_ASSERTION      = 5     # spec: "last 5 calls"
WINDOW_PREMATURE      = 10    # scan back 10 calls for error indicators
WINDOW_REPEATED       = 20    # scan back 20 calls for repeated error class


# =============================================================================
# NAPE DAEMON
# =============================================================================

class NapeDaemon:
    """
    Runtime critique layer that watches tool-call history and flags drift.

    Instantiate once per server process with the sovereign data root:

        daemon = NapeDaemon(root="/Users/you/.sovereign")

    Then call observe() after every tool call. Honks are generated automatically
    and persisted. Query current_honks() to surface them in the UI.

    Parameters
    ----------
    root : str
        Path to the sovereign data root (the ~/.sovereign directory or any
        directory used as SOVEREIGN_ROOT during tests).
    """

    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._nape_dir = self._root / "nape"
        self._nape_dir.mkdir(parents=True, exist_ok=True)

        self._obs_path   = self._nape_dir / "observations.jsonl"
        self._honks_path = self._nape_dir / "honks.jsonl"
        self._acks_path  = self._nape_dir / "acks.jsonl"

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def observe(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any,
        session_id: str,
        timestamp: Optional[str] = None,
    ) -> None:
        """
        Record a tool call observation and run drift detection.

        This is the main entry point. Call it after every tool execution in the
        MCP dispatcher. In the current architecture this is done via the manual
        nape_observe MCP tool; a future hook into the main dispatcher will call
        it automatically.

        Parameters
        ----------
        tool_name : str
            Name of the tool that was called (e.g. "record_insight").
        arguments : dict
            The arguments dict passed to the tool.
        result : Any
            The tool's return value. Coerced to string for pattern matching.
        session_id : str
            Current session identifier. Used to scope the observation window.
        timestamp : str, optional
            ISO8601 timestamp. Defaults to UTC now if not provided.
        """
        if not tool_name:
            raise ValueError(
                "tool_name must be a non-empty string. "
                "Pass the name of the tool that produced this result."
            )
        if not session_id:
            raise ValueError(
                "session_id must be a non-empty string. "
                "Each session must have a stable identifier so Nape can "
                "scope drift detection to the current conversation."
            )

        ts = timestamp or _now_iso()

        record: Dict[str, Any] = {
            "obs_id":     str(uuid.uuid4()),
            "session_id": session_id,
            "timestamp":  ts,
            "tool_name":  tool_name,
            "arguments":  _safe_truncate(arguments),
            "result_str": _result_to_str(result),
        }

        _append_jsonl(self._obs_path, record)

        # Run drift detection on the updated window and persist new honks.
        recent = self._recent_observations(session_id, limit=max(
            WINDOW_REPEATED, WINDOW_PREMATURE, WINDOW_ASSERTION
        ))
        new_honks = self._check_drift(recent)
        for honk in new_honks:
            _append_jsonl(self._honks_path, honk)

    def current_honks(
        self,
        session_id: Optional[str],
        limit: int = 10,
        include_satisfied: bool = True,
    ) -> List[Dict]:
        """
        Return recent unacknowledged honks for a session.

        Honks are sorted newest-first. Already-acknowledged honks are excluded.

        Parameters
        ----------
        session_id : str or None
            Filter to this session. Pass None to return honks from all sessions
            (useful for cross-session summaries).
        limit : int
            Maximum number of honks to return. Defaults to 10.
        include_satisfied : bool
            When False, satisfied-level honks are excluded from the result.
            Defaults to True (preserves existing behavior).

        Returns
        -------
        list of dict
            Each dict has keys: honk_id, level, pattern, trigger_tool,
            observation, timestamp, session_id.
        """
        if limit < 1:
            raise ValueError(
                f"limit must be at least 1, got {limit!r}. "
                "Pass a positive integer to bound the result set."
            )

        acked_ids = self._acked_ids()
        honks     = _read_jsonl(self._honks_path)

        filtered = [
            h for h in honks
            if h.get("honk_id") not in acked_ids
            and (session_id is None or h.get("session_id") == session_id)
            and (include_satisfied or h.get("level") != "satisfied")
        ]

        # Newest first.
        filtered.sort(key=lambda h: h.get("timestamp", ""), reverse=True)
        return filtered[:limit]

    def _check_drift(self, recent_obs: List[Dict]) -> List[Dict]:
        """
        Run all pattern detectors over a sliding window of recent observations.

        Returns a list of new honks to persist. Each honk will have already
        been de-duplicated within the window (same session, same pattern, same
        trigger_tool firing consecutively is not repeated).

        Parameters
        ----------
        recent_obs : list of dict
            Recent observations for a session, newest-last (chronological).
            Comes from _recent_observations().

        Returns
        -------
        list of dict
            New honk records ready for appending to honks.jsonl.
        """
        if not recent_obs:
            return []

        honks: List[Dict] = []

        # The latest observation is the "trigger" — the one we're evaluating.
        latest = recent_obs[-1]

        honks.extend(self._detect_declare_before_verify(latest, recent_obs))
        honks.extend(self._detect_premature_summary(latest, recent_obs))
        honks.extend(self._detect_assertion_without_evidence(latest, recent_obs))
        honks.extend(self._detect_repeated_mistake(latest, recent_obs))
        # Stale-context detection deferred to Phase 2 (see module docstring).

        return honks

    def ack(self, honk_id: str, note: str) -> Dict:
        """
        Acknowledge a honk, marking it as addressed.

        Acknowledgment is append-only: the original honk is not modified.
        The ack record is written to acks.jsonl. Acknowledged honks no longer
        appear in current_honks() but remain queryable for audit purposes.

        Parameters
        ----------
        honk_id : str
            The honk_id of the honk to acknowledge.
        note : str
            A brief note explaining how the honk was addressed or why it was
            a false positive.

        Returns
        -------
        dict
            The ack record that was written.

        Raises
        ------
        ValueError
            If honk_id is empty or not found in honks.jsonl.
        """
        if not honk_id:
            raise ValueError(
                "honk_id must be a non-empty string. "
                "Pass the honk_id from current_honks() output."
            )

        # Verify the honk exists before writing an ack.
        honks = _read_jsonl(self._honks_path)
        matching = [h for h in honks if h.get("honk_id") == honk_id]
        if not matching:
            raise ValueError(
                f"No honk found with honk_id {honk_id!r}. "
                "Check current_honks() for valid IDs."
            )

        ack_record = {
            "ack_id":    str(uuid.uuid4()),
            "honk_id":  honk_id,
            "note":     note,
            "acked_at": _now_iso(),
        }
        _append_jsonl(self._acks_path, ack_record)
        return ack_record

    def summary(self, session_id: Optional[str]) -> Dict:
        """
        Return honk counts broken down by level for a session.

        Parameters
        ----------
        session_id : str or None
            Filter to this session. Pass None to summarize all sessions.

        Returns
        -------
        dict
            Keys: session_id (or "all"), sharp, low, uneasy, satisfied, total,
            unacknowledged.
        """
        acked_ids = self._acked_ids()
        honks = _read_jsonl(self._honks_path)

        if session_id is not None:
            honks = [h for h in honks if h.get("session_id") == session_id]

        counts: Dict[str, int] = {"sharp": 0, "low": 0, "uneasy": 0, "satisfied": 0}
        unacked = 0

        for honk in honks:
            level = honk.get("level", "low")
            counts[level] = counts.get(level, 0) + 1
            # Satisfied honks are positive signal — they do not count as backlog.
            if honk.get("honk_id") not in acked_ids and level != "satisfied":
                unacked += 1

        return {
            "session_id":      session_id or "all",
            **counts,
            "total":           sum(counts.values()),
            "unacknowledged":  unacked,
        }

    # -------------------------------------------------------------------------
    # Pattern detectors (private)
    # -------------------------------------------------------------------------

    def _detect_declare_before_verify(
        self, latest: Dict, recent_obs: List[Dict]
    ) -> List[Dict]:
        """
        Detect when a tool result contains completion language but no verify call
        appears in the preceding WINDOW_DECLARE_VERIFY observations.

        Pattern: sharp honk.
        Fires when: result_str contains any DECLARE_WORD and none of the
        previous 3 tool calls were VERIFY_TOOL_NAMES.
        """
        result_str = latest.get("result_str", "").lower()

        # Check whether any declare word appears in this result.
        if not any(word in result_str for word in DECLARE_WORDS):
            return []

        # Look at the preceding observations (not including this one).
        preceding = recent_obs[:-1][-WINDOW_DECLARE_VERIFY:]
        preceding_tools = {obs.get("tool_name") for obs in preceding}

        # If at least one verify call is present, no honk.
        if preceding_tools & VERIFY_TOOL_NAMES:
            # Optionally emit a "satisfied" honk to mark the clean pattern.
            return [self._build_honk(
                session_id=latest["session_id"],
                pattern="clean_verify_declare",
                trigger_tool=latest["tool_name"],
                observation=(
                    f"{latest['tool_name']} result suggests completion. "
                    f"Verify calls present in recent history: "
                    f"{preceding_tools & VERIFY_TOOL_NAMES}. Pattern is clean."
                ),
                timestamp=latest["timestamp"],
            )]

        return [self._build_honk(
            session_id=latest["session_id"],
            pattern="declare_before_verify",
            trigger_tool=latest["tool_name"],
            observation=(
                f"{latest['tool_name']} result contains completion language "
                f"but no verify call (Read, Grep, Bash, etc.) appears in the "
                f"preceding {WINDOW_DECLARE_VERIFY} tool calls. "
                f"Recent tools: {[o.get('tool_name') for o in preceding]}. "
                f"Verify the claim before treating it as ground truth."
            ),
            timestamp=latest["timestamp"],
        )]

    def _detect_premature_summary(
        self, latest: Dict, recent_obs: List[Dict]
    ) -> List[Dict]:
        """
        Detect when a session-summary tool is called but recent history contains
        unresolved error indicators.

        Pattern: sharp honk.
        Fires when: trigger tool is in SUMMARY_TOOL_NAMES and any of the
        preceding WINDOW_PREMATURE results contains an ERROR_WORD.
        """
        if latest.get("tool_name") not in SUMMARY_TOOL_NAMES:
            return []

        preceding = recent_obs[:-1][-WINDOW_PREMATURE:]
        error_obs = [
            obs for obs in preceding
            if any(word in obs.get("result_str", "").lower() for word in ERROR_WORDS)
        ]

        if not error_obs:
            return []

        sample_tools = [obs.get("tool_name") for obs in error_obs[:3]]
        return [self._build_honk(
            session_id=latest["session_id"],
            pattern="premature_summary",
            trigger_tool=latest["tool_name"],
            observation=(
                f"{latest['tool_name']} called but recent tool history shows "
                f"{len(error_obs)} result(s) with error language. "
                f"Tools with error results (up to 3): {sample_tools}. "
                f"Resolve errors before writing the final summary."
            ),
            timestamp=latest["timestamp"],
        )]

    def _detect_assertion_without_evidence(
        self, latest: Dict, recent_obs: List[Dict]
    ) -> List[Dict]:
        """
        Detect when record_insight is called with high confidence but no
        verify call appears in the preceding WINDOW_ASSERTION observations.

        Pattern: low honk.
        Fires when: tool_name is "record_insight", confidence > 0.9,
        and no VERIFY_TOOL_NAME appears in the last 5 calls.
        """
        if latest.get("tool_name") != "record_insight":
            return []

        # Confidence lives in arguments, not in the result string.
        confidence = latest.get("arguments", {}).get("confidence")
        if confidence is None or float(confidence) <= 0.9:
            return []

        preceding = recent_obs[:-1][-WINDOW_ASSERTION:]
        preceding_tools = {obs.get("tool_name") for obs in preceding}

        if preceding_tools & VERIFY_TOOL_NAMES:
            return []

        return [self._build_honk(
            session_id=latest["session_id"],
            pattern="assertion_without_evidence",
            trigger_tool=latest["tool_name"],
            observation=(
                f"record_insight called with confidence={confidence} (>0.9) "
                f"but no Read, Grep, or Bash appears in the preceding "
                f"{WINDOW_ASSERTION} tool calls. "
                f"Recent tools: {[o.get('tool_name') for o in preceding]}. "
                f"High-confidence claims require observable evidence."
            ),
            timestamp=latest["timestamp"],
        )]

    def _detect_repeated_mistake(
        self, latest: Dict, recent_obs: List[Dict]
    ) -> List[Dict]:
        """
        Detect when a tool call yields an error and the same error class has
        appeared before in the window without a record_learning call in between.

        Pattern: uneasy honk.
        Fires when: latest result contains an ERROR_WORD, and the same tool
        produced an error earlier in the window, and no record_learning call
        appears between the two error occurrences.
        """
        if not any(word in latest.get("result_str", "").lower() for word in ERROR_WORDS):
            return []

        trigger_tool = latest.get("tool_name")
        session_id   = latest.get("session_id")

        # Scan the preceding window (not including latest) for the same tool erroring.
        preceding = recent_obs[:-1][-WINDOW_REPEATED:]

        # Find earlier errors from the same tool.
        earlier_errors = [
            obs for obs in preceding
            if obs.get("tool_name") == trigger_tool
            and any(word in obs.get("result_str", "").lower() for word in ERROR_WORDS)
        ]

        if not earlier_errors:
            return []

        # Check whether a record_learning call appeared after the earliest earlier error.
        earliest_error_ts = min(e.get("timestamp", "") for e in earlier_errors)
        learning_after = any(
            obs.get("tool_name") == "record_learning"
            and obs.get("timestamp", "") > earliest_error_ts
            for obs in preceding
        )

        if learning_after:
            return []

        return [self._build_honk(
            session_id=session_id,
            pattern="repeated_mistake",
            trigger_tool=trigger_tool,
            observation=(
                f"{trigger_tool} has produced errors {len(earlier_errors) + 1} time(s) "
                f"in this session window without a record_learning call in between. "
                f"Earliest prior error at {earliest_error_ts}. "
                f"Consider calling record_learning to capture what went wrong "
                f"before repeating the same action."
            ),
            timestamp=latest["timestamp"],
        )]

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _recent_observations(self, session_id: str, limit: int) -> List[Dict]:
        """
        Read the last `limit` observations for a session, in chronological order
        (oldest first, newest last). This ordering matches the sliding-window
        semantics expected by all detector methods.

        Parameters
        ----------
        session_id : str
            Session to filter on.
        limit : int
            Maximum number of observations to return.
        """
        all_obs = _read_jsonl(self._obs_path)
        session_obs = [o for o in all_obs if o.get("session_id") == session_id]
        # Keep chronological order; take the tail (most recent N).
        return session_obs[-limit:]

    def _acked_ids(self) -> frozenset:
        """Return the set of honk_ids that have been acknowledged."""
        acks = _read_jsonl(self._acks_path)
        return frozenset(a.get("honk_id") for a in acks if a.get("honk_id"))

    @staticmethod
    def _build_honk(
        session_id: str,
        pattern: str,
        trigger_tool: str,
        observation: str,
        timestamp: str,
    ) -> Dict:
        """
        Construct a honk record dict.

        Parameters
        ----------
        session_id : str
            Session this honk belongs to.
        pattern : str
            One of the keys in PATTERN_LEVELS (e.g. "declare_before_verify").
        trigger_tool : str
            The tool call that triggered this detection.
        observation : str
            Human-readable description of what was detected.
        timestamp : str
            ISO8601 timestamp of the triggering observation.

        Returns
        -------
        dict
            Complete honk record with honk_id assigned.
        """
        level = PATTERN_LEVELS.get(pattern, "low")
        return {
            "honk_id":      str(uuid.uuid4()),
            "session_id":   session_id,
            "pattern":      pattern,
            "level":        level,
            "trigger_tool": trigger_tool,
            "observation":  observation,
            "timestamp":    timestamp,
        }


# =============================================================================
# STORAGE UTILITIES (module-private)
# =============================================================================

def _append_jsonl(path: Path, record: Dict) -> None:
    """
    Append a single JSON record as a newline to the JSONL file at `path`.

    Creates the file if it does not exist. Never truncates or rewrites.

    Parameters
    ----------
    path : Path
        Destination JSONL file.
    record : dict
        Must be JSON-serializable. A TypeError is raised at json.dumps time
        if it is not — callers should use _safe_truncate() on nested objects.
    """
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> List[Dict]:
    """
    Read all records from a JSONL file. Returns an empty list if the file
    does not exist or is empty. Silently skips malformed lines (preserves
    read-forward even if a single write was corrupted).

    Parameters
    ----------
    path : Path
        Source JSONL file.

    Returns
    -------
    list of dict
        Records in file order (chronological, since appends are ordered).
    """
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _result_to_str(result: Any) -> str:
    """
    Coerce a tool result to a plain string for keyword-based pattern matching.

    Handles the common MCP return shapes (list of TextContent, plain string,
    dict) without importing mcp.types to avoid a hard dependency in tests.

    Parameters
    ----------
    result : Any
        The raw return value of a tool call.

    Returns
    -------
    str
        Best-effort plain text representation, truncated to 4096 chars.
    """
    if result is None:
        return ""
    if isinstance(result, str):
        text = result
    elif isinstance(result, list):
        # Handle list of TextContent objects or plain strings.
        parts = []
        for item in result:
            if hasattr(item, "text"):
                parts.append(str(item.text))
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        text = " ".join(parts)
    elif isinstance(result, dict):
        text = json.dumps(result, ensure_ascii=False)
    else:
        text = str(result)

    return text[:4096]


def _safe_truncate(arguments: Dict) -> Dict:
    """
    Return a copy of the arguments dict with long string values truncated.

    Prevents observations.jsonl from growing unbounded when argument values
    are large (e.g. a `content` field with an essay-length insight).

    Parameters
    ----------
    arguments : dict
        Tool arguments dict.

    Returns
    -------
    dict
        Shallow copy with string values capped at 512 chars.
    """
    if not isinstance(arguments, dict):
        return {}
    result = {}
    for k, v in arguments.items():
        if isinstance(v, str) and len(v) > 512:
            result[k] = v[:512] + "...[truncated]"
        else:
            result[k] = v
    return result


def _now_iso() -> str:
    """Return the current UTC time as an ISO8601 string with timezone."""
    return datetime.now(timezone.utc).isoformat()
