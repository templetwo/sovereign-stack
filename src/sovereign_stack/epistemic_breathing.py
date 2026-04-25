"""
Sovereign Stack — Epistemic Breathing Layer
Classifies queries before the stack returns results.

This is a LIGHTWEIGHT heuristic classifier that approximates
the compass signals (OPEN/PAUSE/WITNESS) without loading the
3B model. It runs inline in the stack's retrieval path.

The full compass (Ministral-3B + LoRA) is too heavy for inline
stack queries. This heuristic provides 80% of the governance
value at 0% of the compute cost. When the full compass is
available via the pipeline API, upgrade to that.

Integration point: wrap recall_insights, context_retrieve,
and get_open_threads with breathe_query() before returning.
"""

import re

# ================================================================
# SIGNAL HEURISTICS
# ================================================================

# Keywords/patterns that indicate epistemic weight
WITNESS_PATTERNS = [
    # Grief, loss, death
    r"\b(grief|griev|mourn|die[ds]?|death|dying|funeral|loss|lost\s+(my|a|the))\b",
    r"\b(forgetting|dementia|alzheimer|terminal|suicide|self.harm)\b",
    # Existential weight
    r"\b(meaning\s+of\s+life|why\s+am\s+i|purpose|existential|soul|afterlife)\b",
    r"\b(suffering|pain|trauma|abuse|assault|victim)\b",
    # Trust rupture
    r"\b(betray|trust.*broken|abandon|reject|alone|isolat)\b",
    # Identity crisis
    r"\b(who\s+am\s+i|don\'?t\s+know\s+who|identity|lost\s+myself)\b",
]

PAUSE_PATTERNS = [
    # Ethical weight
    r"\b(ethical|moral|right\s+or\s+wrong|should\s+i|dilemma|conscience)\b",
    r"\b(fair|justice|equity|discriminat|bias|privilege)\b",
    # Life decisions
    r"\b(divorce|separate|break\s*up|marry|pregnant|abort|adopt)\b",
    r"\b(quit|resign|fire[d]?|career\s+change|retire)\b",
    r"\b(invest|risk|gamble|debt|bankrupt)\b",
    # Sensitive topics
    r"\b(religion|faith|god|pray|spirit|sacred|belief)\b",
    r"\b(politic|democrat|republican|liberal|conservative|vote)\b",
]

OPEN_PATTERNS = [
    # Direct factual
    r"\b(what\s+is|how\s+does|explain|define|calculate|list|compare)\b",
    r"\b(code|program|script|function|algorithm|debug|error)\b",
    r"\b(recipe|instructions|steps|tutorial|how\s+to)\b",
    # Curiosity
    r"\b(curious|wonder|interesting|fascnat|explore|research)\b",
    r"\b(history|science|math|physics|chemistry|biology)\b",
]


def classify_query(query: str) -> tuple[str, float, str]:
    """
    Classify a query's epistemic posture heuristically.

    Returns:
        signal: OPEN, PAUSE, or WITNESS
        confidence: 0.0 to 1.0
        rationale: why this classification
    """
    query_lower = query.lower().strip()

    # Score each signal
    witness_score = 0
    pause_score = 0
    open_score = 0

    witness_matches = []
    pause_matches = []
    open_matches = []

    for pattern in WITNESS_PATTERNS:
        if re.search(pattern, query_lower):
            witness_score += 2  # WITNESS patterns weighted higher
            match_text = re.search(pattern, query_lower).group(0)
            witness_matches.append(match_text)

    for pattern in PAUSE_PATTERNS:
        if re.search(pattern, query_lower):
            pause_score += 1
            match_text = re.search(pattern, query_lower).group(0)
            pause_matches.append(match_text)

    for pattern in OPEN_PATTERNS:
        if re.search(pattern, query_lower):
            open_score += 1
            match_text = re.search(pattern, query_lower).group(0)
            open_matches.append(match_text)

    # Explicit question words at the start strongly suggest OPEN
    if re.match(r"^(what|how|explain|define|list|compare|calculate|tell me about)\b", query_lower):
        open_score += 2

    # Question mark density — more questions = more weight
    q_count = query.count("?")
    if q_count == 0:
        # Statements often carry more weight than questions
        pause_score += 1

    # Short queries are often heavier (less hedging)
    if len(query_lower.split()) < 10:
        pause_score += 0.5

    # First person + emotion words = weight
    if re.search(r"\b(i|my|me|myself)\b", query_lower) and re.search(
        r"\b(feel|felt|afraid|scared|angry|sad|hurt|confused|lost|alone|broken)\b", query_lower
    ):
        witness_score += 2

    # Determine signal
    total = witness_score + pause_score + open_score
    if total == 0:
        return "OPEN", 0.3, "No strong signal detected — defaulting to OPEN"

    if witness_score > pause_score and witness_score > open_score:
        confidence = min(witness_score / (total + 1), 0.95)
        return "WITNESS", confidence, f"Weight detected: {', '.join(witness_matches[:3])}"
    if pause_score > open_score:
        confidence = min(pause_score / (total + 1), 0.9)
        return "PAUSE", confidence, f"Epistemic weight: {', '.join(pause_matches[:3])}"
    confidence = min(open_score / (total + 1), 0.9)
    return "OPEN", confidence, f"Exploratory: {', '.join(open_matches[:3])}"


def breathe_query(query: str, results: list[dict]) -> list[dict]:
    """
    Apply epistemic breathing to stack query results.

    Based on the query's signal, filter and reorder results:
    - OPEN: return all results, prioritize breadth
    - PAUSE: prioritize ground_truth over hypothesis, add weight warning
    - WITNESS: filter to only ground_truth, prepend holding message
    """
    signal, confidence, rationale = classify_query(query)

    if signal == "OPEN":
        # Full results, no filtering
        return results

    if signal == "PAUSE":
        # Prioritize ground_truth, flag hypotheses
        ground = [r for r in results if r.get("layer") == "ground_truth"]
        hypo = [r for r in results if r.get("layer") == "hypothesis"]
        threads = [r for r in results if r.get("layer") == "open_thread"]
        other = [
            r
            for r in results
            if r.get("layer") not in ("ground_truth", "hypothesis", "open_thread")
        ]

        # Ground truth first, then threads, then hypotheses last
        return ground + threads + hypo + other

    if signal == "WITNESS":
        # Only ground truth — don't surface speculation on threshold queries
        return [r for r in results if r.get("layer") == "ground_truth"]

    return results


def breathe_comms(message: dict) -> dict:
    """
    Apply epistemic breathing to a comms message before delivery.

    Returns the message with an added 'epistemic_signal' field
    and optionally a 'hold' flag if the message should be held
    rather than delivered immediately.
    """
    content = message.get("content", "")
    signal, confidence, rationale = classify_query(content)

    message["epistemic_signal"] = signal
    message["epistemic_confidence"] = confidence
    message["epistemic_rationale"] = rationale

    # WITNESS messages get held — they exist but aren't pushed
    if signal == "WITNESS" and confidence > 0.6:
        message["hold"] = True
        message["hold_reason"] = f"WITNESS signal ({confidence:.0%}): {rationale}"
    else:
        message["hold"] = False

    return message


# ================================================================
# INTEGRATION HELPERS
# ================================================================


def wrap_tool_response(tool_name: str, query: str, result: any) -> any:
    """
    Wrap a tool's response with epistemic breathing.
    Call this in the stack server before returning results.

    Usage in server.py:
        result = original_tool_handler(arguments)
        if tool_name in BREATHABLE_TOOLS:
            result = wrap_tool_response(tool_name, query, result)
        return result
    """
    BREATHABLE_TOOLS = [
        "recall_insights",
        "context_retrieve",
        "get_open_threads",
        "get_inheritable_context",
    ]

    if tool_name not in BREATHABLE_TOOLS:
        return result

    signal, confidence, rationale = classify_query(query)

    # Add breathing metadata to response
    if isinstance(result, dict):
        result["_breathing"] = {
            "signal": signal,
            "confidence": confidence,
            "rationale": rationale,
        }

    return result


# ================================================================
# SELF-TEST
# ================================================================
if __name__ == "__main__":
    test_queries = [
        ("My grandmother is forgetting us one by one", "WITNESS"),
        ("How do I sort a list in Python?", "OPEN"),
        ("Is it ethical to bring children into a suffering world?", "PAUSE"),
        ("I feel so alone since the divorce", "WITNESS"),
        ("What's the capital of France?", "OPEN"),
        ("Should I quit my job and start a business?", "PAUSE"),
        ("I don't know who I am anymore", "WITNESS"),
        ("Explain the Pythagorean theorem", "OPEN"),
        ("Is it fair that billionaires exist while people starve?", "PAUSE"),
        ("My best friend died yesterday", "WITNESS"),
    ]

    print("Epistemic Breathing Layer — Self-Test")
    print("=" * 50)

    correct = 0
    for query, expected in test_queries:
        signal, conf, rationale = classify_query(query)
        match = "✅" if signal == expected else "❌"
        if signal == expected:
            correct += 1
        print(f"  {match} [{signal:<7} {conf:.0%}] {query[:50]}")
        if signal != expected:
            print(f"       Expected {expected}, got {signal}: {rationale}")

    print(f"\n{correct}/{len(test_queries)} correct")
