"""Pattern-based redaction for chronicle content before scribe ingestion.

Required by SCRIBE_SPEC.md: the scribe is a new audience for chronicle
content. Without redaction, recall could leak what compass blocks (the
2026-05-12 reflector contradiction, recorded as id cb7c9e3acf64_aa432ae0).

Design notes:
  - Pure-Python, no I/O, no LLM. Unit-testable in isolation.
  - Pattern order matters: longest / most specific first so that
    substrings of more general patterns are not re-matched.
  - Redaction is one-way by design. The original text is not preserved.
  - Counts are returned for observability (dashboard surfacing of strip
    rate per pattern).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Pattern


@dataclass
class RedactionResult:
    """Output of a single redact() call."""

    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_redactions(self) -> int:
        return sum(self.counts.values())

    @property
    def was_redacted(self) -> bool:
        return self.total_redactions > 0


# Patterns. Order matters — most-specific first.
#
# Each entry: (name, compiled-pattern, replacement-string)
# The name is used for the counts dict (observability).
_PATTERNS: list[tuple[str, Pattern[str], str]] = [
    # SSH / TLS private key blocks. Longest and most specific.
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<redacted-private-key>",
    ),
    # Bearer tokens in Authorization headers. Common shape in PostToolUse
    # traces of curl commands.
    (
        "bearer_token",
        re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
        "Bearer <redacted-token>",
    ),
    # Anthropic API key shape — sk-ant-*. Most specific of the API-key
    # family, matched before the generic sk-/pk- pattern.
    (
        "api_key_anthropic",
        re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
        "<redacted-key>",
    ),
    # Generic API keys with sk-, pk-, api_, or api- prefixes.
    (
        "api_key_generic",
        re.compile(r"\b(?:sk|pk|api)[-_][A-Za-z0-9_\-]{20,}\b"),
        "<redacted-key>",
    ),
    # Env-style credential assignments. Conservative: only triggers when
    # the LHS contains a credential-flavored substring. Avoids redacting
    # innocuous KEY=VALUE pairs like SOVEREIGN_ROOT=~/.sovereign.
    # Prefix is OPTIONAL so leading names like "API_KEY=val" or "TOKEN=val"
    # match without requiring a leading char to be consumed first.
    (
        "env_credential",
        re.compile(
            r"\b((?:[A-Z][A-Z0-9_]*)?"
            r"(?:TOKEN|SECRET|PASSWORD|PASS|AUTH|API_KEY|APIKEY|CREDENTIAL)"
            r"[A-Z0-9_]*)="
            r"([^\s\"']+)"
        ),
        r"\1=<redacted-env>",
    ),
    # Long hex strings (40+ chars) — likely SHA-1+, signatures, or
    # opaque tokens. The git-commit-SHA shape (40 chars) is intentionally
    # NOT redacted: short refs are 7-12 chars and full SHAs we keep
    # un-redacted for chronicle archaeology. Tighten to 48+ to skip SHA-1.
    (
        "hex_token",
        re.compile(r"\b[a-f0-9]{48,}\b"),
        "<redacted-hex>",
    ),
    # Filesystem paths containing sensitive markers.
    (
        "sensitive_path",
        re.compile(
            r"(?<![A-Za-z0-9_/.])"
            r"/[^\s\"']*?"
            r"(?:\.env|credentials|secrets|\.key\b|\.pem\b|id_rsa|id_ed25519)"
            r"[^\s\"']*"
        ),
        "<redacted-path>",
    ),
]


def pattern_names() -> list[str]:
    """Names of all active redaction patterns, in apply order."""
    return [name for name, _, _ in _PATTERNS]


def redact(text: str) -> RedactionResult:
    """Apply all redaction patterns to a string.

    Returns a RedactionResult with the redacted text and per-pattern
    strip counts. Empty / None input is passed through unchanged with
    empty counts.
    """
    if not text:
        return RedactionResult(text=text or "")

    counts: dict[str, int] = {}
    redacted = text
    for name, pattern, replacement in _PATTERNS:
        new_text, n = pattern.subn(replacement, redacted)
        if n > 0:
            counts[name] = n
            redacted = new_text

    return RedactionResult(text=redacted, counts=counts)


def redact_structure(obj, _depth: int = 0) -> tuple[object, dict[str, int]]:
    """Recursively redact strings inside a JSON-like structure.

    Returns (redacted-obj, aggregated-counts). Depth-limited to 32 to
    prevent runaway on pathological inputs.
    """
    if _depth > 32:
        return obj, {}

    if isinstance(obj, str):
        result = redact(obj)
        return result.text, result.counts

    counts: dict[str, int] = {}

    if isinstance(obj, dict):
        new_dict: dict = {}
        for k, v in obj.items():
            new_v, sub_counts = redact_structure(v, _depth + 1)
            new_dict[k] = new_v
            for name, n in sub_counts.items():
                counts[name] = counts.get(name, 0) + n
        return new_dict, counts

    if isinstance(obj, list):
        new_list: list = []
        for item in obj:
            new_item, sub_counts = redact_structure(item, _depth + 1)
            new_list.append(new_item)
            for name, n in sub_counts.items():
                counts[name] = counts.get(name, 0) + n
        return new_list, counts

    if isinstance(obj, tuple):
        new_tuple_items: list = []
        for item in obj:
            new_item, sub_counts = redact_structure(item, _depth + 1)
            new_tuple_items.append(new_item)
            for name, n in sub_counts.items():
                counts[name] = counts.get(name, 0) + n
        return tuple(new_tuple_items), counts

    return obj, {}


def redact_iter(strings: Iterable[str]) -> tuple[list[str], dict[str, int]]:
    """Redact a sequence of strings, returning the redacted list and
    aggregated counts. Convenience for batch operations."""
    results: list[str] = []
    counts: dict[str, int] = {}
    for s in strings:
        r = redact(s)
        results.append(r.text)
        for name, n in r.counts.items():
            counts[name] = counts.get(name, 0) + n
    return results, counts
