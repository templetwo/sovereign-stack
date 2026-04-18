"""
recall_arc — contextual + temporal recall for the sovereign stack.

Bridges fresh Claude instances to the chronicle by returning an ARC
(temporally ordered entries with contextual neighborhood) rather than a
flat similarity-ranked list.

Usage:
    export STACK_TOKEN=<bearer token>
    python3 recall_arc.py "compass drift entropy"
"""
import json
import os
import subprocess
from datetime import datetime, timedelta


TOKEN = os.environ.get("STACK_TOKEN", "")
BASE_URL = os.environ.get("STACK_URL", "https://stack.templetwo.com/api/call")


def stack_call(tool, args, timeout=30, retries=2):
    """Call a stack MCP tool via REST bridge. Returns parsed result or None."""
    if not TOKEN:
        raise RuntimeError("STACK_TOKEN env var not set")
    payload = json.dumps({"tool": tool, "arguments": args})
    for _ in range(retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", BASE_URL,
                 "-H", f"Authorization: Bearer {TOKEN}",
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=timeout,
            )
            if not result.stdout.strip():
                continue
            response = json.loads(result.stdout)
            if not response.get("ok"):
                return None
            return response.get("result")
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
    return None


def recall_arc(topic, temporal_window_hours=72, max_entries=30, max_domain_seeds=4,
               direct_limit=12, temporal_limit=15, require_term_match=True):
    """
    Reconstruct the arc of a topic through the chronicle.

    Combines four retrieval passes, deduplicated by timestamp, sorted ascending:
    - Phase 1: semantic matches (recall_insights)
    - Phase 2: temporal neighborhood around each direct hit
    - Phase 3: domain-adjacent entries
    - Phase 4: open threads with topic overlap
    """
    # Phase 1: direct matches
    direct = stack_call("recall_insights", {"query": topic, "limit": direct_limit})
    if not isinstance(direct, list):
        direct = []

    seen = {}
    for entry in direct:
        ts = entry.get("timestamp")
        if ts:
            seen[ts] = {**entry, "_source": "direct_match"}

    # Phase 2: temporal neighborhood
    window = timedelta(hours=temporal_window_hours)
    for entry in direct[:5]:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
        except (ValueError, KeyError):
            continue
        start = (ts - window).isoformat()
        end = (ts + window).isoformat()
        neighbors = stack_call("recall_insights", {
            "query": "",
            "limit": temporal_limit,
            "start_date": start,
            "end_date": end,
        })
        if neighbors and isinstance(neighbors, list):
            for n in neighbors:
                if n["timestamp"] not in seen:
                    seen[n["timestamp"]] = {**n, "_source": "temporal_neighbor"}

    # Phase 3: domain-adjacent
    GENERIC_DOMAINS = {"reflection", "surprise", "hypothesis", "ground_truth", "open_thread"}
    domains = []
    for entry in direct:
        for d in entry.get("domain", "").split(","):
            d = d.strip().lower()
            if d and d not in GENERIC_DOMAINS and d not in domains:
                domains.append(d)
    for domain in domains[:max_domain_seeds]:
        dom_hits = stack_call("recall_insights", {"query": domain, "limit": 4})
        if dom_hits and isinstance(dom_hits, list):
            for h in dom_hits:
                if h["timestamp"] not in seen:
                    seen[h["timestamp"]] = {**h, "_source": f"domain:{domain}"}

    # Phase 4: open threads
    topic_terms = {t.lower() for t in topic.split() if len(t) >= 3}
    open_threads = stack_call("get_open_threads", {})
    if open_threads and isinstance(open_threads, list):
        for t in open_threads:
            q = t.get("question", "").lower()
            d = t.get("domain", "").lower()
            if any(term in q or term in d for term in topic_terms):
                ts = t.get("timestamp", "")
                if ts and ts not in seen:
                    seen[ts] = {
                        "timestamp": ts,
                        "domain": t.get("domain", ""),
                        "content": t.get("question", "") + "\n\nContext: " + t.get("context", ""),
                        "intensity": 0.8,
                        "layer": "open_thread",
                        "_source": "open_thread",
                    }

    # Phase 5: relevance gate
    if require_term_match:
        significant_terms = [t.lower() for t in topic.split() if len(t) >= 3]
        if not significant_terms:
            return []
        topic_anchored = False
        for entry in seen.values():
            if entry.get("_source") == "open_thread":
                topic_anchored = True
                break
            blob = (entry.get("content", "") + " " + entry.get("domain", "")).lower()
            if any(term in blob for term in significant_terms):
                topic_anchored = True
                break
        if not topic_anchored:
            return []

    # Phase 6: sort and annotate
    arc = sorted(seen.values(), key=lambda e: e.get("timestamp", ""))[:max_entries]

    for i, entry in enumerate(arc):
        layer = entry.get("layer", "")
        domain = entry.get("domain", "").lower()
        content = entry.get("content", "").lower()[:200]

        if "mistake" in domain or "learning" in domain or "failure" in content or "wrong" in content[:60]:
            entry["_type"] = "failure"
        elif layer == "open_thread":
            entry["_type"] = "open"
        elif "transformation" in domain or "pivot" in content[:80] or "surprise" in domain:
            entry["_type"] = "transformation"
        else:
            entry["_type"] = "insight"

        if i > 0:
            entry["_prev_ts"] = arc[i - 1]["timestamp"]
        if i < len(arc) - 1:
            entry["_next_ts"] = arc[i + 1]["timestamp"]

    return arc


def format_arc(arc, compact=True):
    """Render an arc as a narrative reading."""
    if not arc:
        return "(no arc found)"

    TYPE_MARKER = {"insight": "◆", "failure": "✗", "transformation": "↻", "open": "?"}

    lines = []
    span = f"{arc[0]['timestamp'][:10]} → {arc[-1]['timestamp'][:10]}"
    lines.append(f"ARC · {len(arc)} entries · {span}")
    lines.append("─" * 72)

    for entry in arc:
        t = entry["timestamp"][:16].replace("T", " ")
        marker = TYPE_MARKER.get(entry["_type"], "·")
        intensity = entry.get("intensity", 0)
        src = entry.get("_source", "?")
        domain = entry.get("domain", "")[:50]
        content = entry.get("content", "")

        lines.append(f"\n{marker} {t}  [{intensity:.2f}]  via {src}")
        if domain:
            lines.append(f"   tags: {domain}")
        first_para = content.split("\n\n")[0]
        if compact and len(first_para) > 280:
            first_para = first_para[:280] + "…"
        for row in first_para.split("\n"):
            lines.append(f"   {row}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "compass drift entropy"
    print(f"querying arc for: {topic!r}\n")
    arc = recall_arc(topic)
    print(format_arc(arc))
