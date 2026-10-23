"""
Design C5: season_review's section 3 is a keyword screen, and it says so.

Section 3 is a regex over the text of >=0.9 sentinels. Under its old title
("POLICY CANDIDATES") and its old overflow line ("…and N more"), the number
of regex hits travelled into planning as a number of policies. The section
now names itself a keyword screen, prints beside every candidate the
screen's words found in that sentinel, and counts its overflow as matches.

Hermetic: every chronicle lives under tmp_path.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from sovereign_stack.provenance import derive_claim_id, display_id
from sovereign_stack.seasons import season_review

# Written out, never imported from seasons.py, so a changed title turns these red.
KEYWORD_SCREEN_TITLE = (
    "3. POLICY-SHAPED KEYWORD SCREEN (a regex over sentinel text; NOT a count of policies)"
)
OLD_TITLE = "3. POLICY CANDIDATES (policy-shaped sentinels absent from the registry)"
OVERFLOW_LINE = "  …and 1 more matches (keyword screen)"

# Each text carries only the screen words its test names. The screen's words are
# always, never, must, must not, do not, don't, require, required, prefer, policy
# and non-negotiable, so a stray one would add a word and break the anchored pin.
NEVER_TEXT = "Seats never land a receipt that points at a scratch path."
ALWAYS_POLICY_TEXT = "Closing a session with a handoff is always policy here."


def _write_sentinel(root, domain, content):
    """One >=0.9 ground_truth entry, dated yesterday, in a live-shaped shard."""
    entry = {
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "domain": domain,
        "content": content,
        "intensity": 0.92,
        "session_id": "session_20261022_000000",
        "layer": "ground_truth",
    }
    domain_dir = root / "insights" / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    with open(domain_dir / "session_20261022_000000.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _section_three(report):
    """Section 3, title line through the line before section 4, whatever its title."""
    start = report.index("\n3. ") + 1
    return report[start : report.index("\n4. HYGIENE", start)]


def _candidate_line(section, entry, matched):
    """The entry's bullet line, anchored at both ends and ending in exactly
    ``matched``; returns the match, whose ``preview`` group is the shown text."""
    claim = re.escape(display_id(derive_claim_id(entry)))
    domain = re.escape(entry["domain"])
    pattern = rf"^  • {claim} \[{domain}\] (?P<preview>.*) · matched: {re.escape(matched)}$"
    return re.search(pattern, section, re.MULTILINE)


@pytest.fixture
def screen_root(tmp_path):
    """The brief's chronicle: one sentinel matching "never", one "always policy"."""
    root = tmp_path / "chronicle"
    never = _write_sentinel(root, "seat-discipline", NEVER_TEXT)
    always_policy = _write_sentinel(root, "session-close", ALWAYS_POLICY_TEXT)
    return root, never, always_policy


def test_section_three_is_titled_a_keyword_screen(screen_root):
    root, _, _ = screen_root
    report = season_review(chronicle_root=root)
    assert _section_three(report).splitlines()[0] == KEYWORD_SCREEN_TITLE
    assert OLD_TITLE not in report
    assert "POLICY CANDIDATES" not in report


def test_each_candidate_names_the_words_that_matched(screen_root):
    root, never, always_policy = screen_root
    section = _section_three(season_review(chronicle_root=root))
    assert _candidate_line(section, never, "never")
    assert _candidate_line(section, always_policy, "always, policy")
    # The suffix rides the bullet line; the set_policy call stays ready to paste.
    calls = [line for line in section.splitlines() if "→ set_policy(statement=" in line]
    assert len(calls) == 2
    assert all("matched:" not in call for call in calls)


def test_matched_words_follow_the_text_lower_cased_and_once_each(tmp_path):
    root = tmp_path / "chronicle"
    entry = _write_sentinel(
        root, "watchman", "Policy: the watchman sweeps hourly, always, and ALWAYS logs it."
    )
    section = _section_three(season_review(chronicle_root=root))
    # Text order puts "policy" first; the regex's alternation order would put
    # "always" first, and a missing de-duplication would name "always" twice.
    assert _candidate_line(section, entry, "policy, always")


def test_a_word_past_the_preview_cut_is_still_named(tmp_path):
    root = tmp_path / "chronicle"
    content = (
        "The jetson mirror pulls the chronicle every six hours over the local network"
        " and checks each copied file; it never skips that step."
    )
    entry = _write_sentinel(root, "jetson-mirror", content)
    line = _candidate_line(_section_three(season_review(chronicle_root=root)), entry, "never")
    assert line
    # The preview stops at 80 characters, before the word: only the suffix shows it.
    assert line.group("preview").endswith("…")
    assert "never" not in line.group("preview")


def test_overflow_counts_matches_of_the_screen(screen_root):
    root, _, _ = screen_root
    section = _section_three(season_review(chronicle_root=root, max_candidates=1))
    assert section.count("→ set_policy(statement=") == 1
    assert OVERFLOW_LINE in section.splitlines()
    assert "raise max_candidates" not in section
