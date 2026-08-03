"""Phase 1 — retrieval honesty: subset matching, D1, short terms, Schema v1.

Per standing law #2, the gates here demonstrably FAIL on the unfixed base
(main @ 41e2045): subset reach returns only the bare dir, compound queries
fall through to the global fallback, short query terms are silently
dropped, and `envelope=` does not exist (TypeError). The positive controls
pass on both sides and are labeled as such.

Envelope assertions follow Schema v1 (canonical, fable (2/3) 2026-07-20):
8 required top-level fields, 3-value scope.mode enum, closed
partial_reasons vocabulary, invariants 1-8. The auditor (3/3) gates the
same contract independently; these are the builder's own checks.
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.reflexive import ReflexiveSurface

_REASON_RE = re.compile(
    r"^(truncated:\d+|domain_no_match|query_term_ignored:[^:]+:.+|corrupt_line_skipped:\d+"
    r"|supersession-ledger-missing-but-chronicle-references-it"
    r"|supersession_ledger_corrupt_line_skipped:\d+)$"
)


def _write(root: Path, domain: str, content: str, ts: str, layer: str = "hypothesis"):
    d = root / "insights" / domain
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "timestamp": ts,
        "domain": domain,
        "content": content,
        "intensity": 0.5,
        "layer": layer,
        "session_id": "phase1-test",
    }
    with open(d / "phase1-test.jsonl", "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


@pytest.fixture
def chronicle(tmp_path):
    root = tmp_path / "chronicle"
    (root / "insights").mkdir(parents=True)
    recs = {}
    recs["bare1"] = _write(root, "lineage", "bare lineage row one", "2026-06-01T08:00:00+00:00")
    recs["bare2"] = _write(root, "lineage", "bare lineage row two", "2026-06-02T08:00:00+00:00")
    recs["comp1"] = _write(
        root, "lineage,letters", "compound lineage letter alpha", "2026-06-03T08:00:00+00:00"
    )
    recs["comp2"] = _write(
        root, "lineage,letters", "compound lineage letter beta", "2026-06-04T08:00:00+00:00"
    )
    recs["comp3"] = _write(
        root, "letters,lineage,poems", "triple compound row", "2026-06-05T08:00:00+00:00"
    )
    recs["other"] = _write(root, "letters,poems", "poems only row", "2026-06-06T08:00:00+00:00")
    recs["p1a"] = _write(root, "alpha", "P1 write path note", "2026-06-07T08:00:00+00:00")
    recs["p1b"] = _write(root, "alpha", "unrelated alpha row", "2026-06-08T08:00:00+00:00")
    return {"root": root, "recs": recs, "mem": ExperientialMemory(root=str(root))}


def _assert_schema_v1(env: dict, limit: int):
    """The 8 invariants, asserted as written."""
    assert set(env) == {
        "items",
        "returned",
        "total_matched",
        "offset",
        "scope",
        "truncated",
        "partial_reasons",
        "continuation",
    }
    assert env["returned"] == len(env["items"])  # inv 1
    assert env["total_matched"] >= env["returned"]  # inv 2
    assert env["truncated"] == (env["total_matched"] > env["offset"] + env["returned"])  # inv 3
    assert (env["continuation"] is not None) == env["truncated"]  # inv 4
    if env["continuation"] is not None:
        assert env["continuation"] == {"offset": env["offset"] + env["returned"], "limit": limit}
    complete = (
        not env["truncated"]
        and env["scope"]["mode"] != "domain-empty"
        and not any(
            r.startswith(
                (
                    "corrupt_line_skipped",
                    "query_term_ignored",
                    "supersession_ledger_corrupt_line_skipped",
                )
            )
            or r == "supersession-ledger-missing-but-chronicle-references-it"
            for r in env["partial_reasons"]
        )
    )
    assert (env["partial_reasons"] == []) == complete  # inv 5
    scope = env["scope"]
    assert set(scope) == {"mode", "domain_query", "domains_searched", "domains_total"}
    assert scope["mode"] in ("domain", "domain-empty", "all")
    if scope["mode"] == "domain-empty":  # inv 6
        assert scope["domains_searched"] == 0
        assert env["total_matched"] == 0
        assert env["items"] == []
    if scope["mode"] == "all":  # inv 7
        assert scope["domains_searched"] == scope["domains_total"]
    for reason in env["partial_reasons"]:  # inv 8
        assert _REASON_RE.match(reason), f"reason outside closed vocabulary: {reason}"


class TestSubsetContainment:
    def test_bare_label_reaches_compound_siblings(self, chronicle):
        """UNFIXED: the bare 'lineage' dir exists, so ONLY its 2 rows return
        and the 3 compound-sibling rows stay invisible (the 1,303-row class).
        FIXED: subset containment reaches all 5."""
        env = chronicle["mem"].recall_insights(domain="lineage", limit=50, envelope=True)
        assert env["total_matched"] == 5
        assert env["scope"]["mode"] == "domain"
        assert env["scope"]["domains_searched"] == 3  # lineage + 2 compounds
        _assert_schema_v1(env, limit=50)

    def test_compound_query_matches_superset_dir(self, chronicle):
        """UNFIXED: no dir named 'letters,lineage' exists, so the global
        fallback returns ALL 8 rows. FIXED: subset containment returns the
        3 rows whose label set contains both, order-insensitively."""
        env = chronicle["mem"].recall_insights(domain="letters,lineage", limit=50, envelope=True)
        assert env["total_matched"] == 3
        contents = {i["content"] for i in env["items"]}
        assert contents == {
            "compound lineage letter alpha",
            "compound lineage letter beta",
            "triple compound row",
        }
        assert env["scope"]["domain_query"] == "letters,lineage"  # sorted join
        _assert_schema_v1(env, limit=50)

    def test_exact_bare_domain_without_siblings_unchanged(self, chronicle):
        """Positive control, passes on both sides: 'alpha' has no compound
        siblings, so subset matching returns exactly what exact matching did,
        and the default (no envelope) return shape is still a list."""
        result = chronicle["mem"].recall_insights(domain="alpha", limit=50)
        assert isinstance(result, list)
        assert {r["content"] for r in result} == {"P1 write path note", "unrelated alpha row"}


class TestD1ExplicitEmpty:
    def test_typo_domain_returns_domain_empty_not_corpus(self, chronicle):
        """UNFIXED: 'lineag' (typo) matches no dir and falls back to a
        GLOBAL scan — all 8 rows dressed as one domain. FIXED (D1): explicit
        empty, named in the envelope."""
        env = chronicle["mem"].recall_insights(domain="lineag", limit=50, envelope=True)
        assert env["items"] == []
        assert env["total_matched"] == 0
        assert env["scope"]["mode"] == "domain-empty"
        assert "domain_no_match" in env["partial_reasons"]
        _assert_schema_v1(env, limit=50)


class TestShortQueryTerms:
    def test_two_char_term_filters_instead_of_silently_dropping(self, chronicle):
        """UNFIXED: 'P1' is under the 3-char floor, the term list comes back
        empty, the text filter VANISHES, and all 8 rows return while the
        caller believes it filtered (R3, live-reproduced 2026-07-20).
        FIXED: every non-empty term filters."""
        env = chronicle["mem"].recall_insights(query="P1", limit=50, envelope=True)
        assert env["total_matched"] == 1
        assert env["items"][0]["content"] == "P1 write path note"
        _assert_schema_v1(env, limit=50)


class TestPaginationEnvelope:
    def test_truncated_page_reports_coverage(self, chronicle):
        """UNFIXED: envelope= does not exist (TypeError). FIXED: a truncated
        page states returned vs total, and continuation walks the rest."""
        env = chronicle["mem"].recall_insights(domain="lineage", limit=2, offset=1, envelope=True)
        assert env["returned"] == 2
        assert env["total_matched"] == 5
        assert env["offset"] == 1
        assert env["truncated"] is True
        assert f"truncated:{env['total_matched']}" in env["partial_reasons"]
        _assert_schema_v1(env, limit=2)

    def test_pagination_walk_is_complete_and_duplicate_free(self, chronicle):
        seen = []
        offset = 0
        while True:
            env = chronicle["mem"].recall_insights(
                domain="lineage", limit=2, offset=offset, envelope=True
            )
            _assert_schema_v1(env, limit=2)
            seen.extend(i["content"] for i in env["items"])
            if not env["truncated"]:
                break
            offset = env["continuation"]["offset"]
        assert len(seen) == 5
        assert len(set(seen)) == 5


class TestReflexiveRelevanceBeforeTruncation:
    def test_insights_bucket_requests_relevance_order(self, tmp_path):
        """UNFIXED: the insights bucket calls recall_insights with the
        default newest-first order, so the 200-cap is applied by recency and
        the scorer never sees an old-but-relevant entry. FIXED: the call
        site passes order='relevance'."""
        sovereign_root = tmp_path / "sovereign"
        (sovereign_root / "chronicle" / "insights").mkdir(parents=True)
        surfacer = ReflexiveSurface(sovereign_root)
        calls = []
        real = surfacer._memory.recall_insights

        def spy(*args, **kwargs):
            calls.append(kwargs)
            return real(*args, **kwargs)

        with patch.object(surfacer._memory, "recall_insights", side_effect=spy):
            surfacer.surface(domain_tags=["phase1"])
        insight_calls = [c for c in calls if c.get("query") is not None]
        assert insight_calls, "insights bucket never queried"
        assert insight_calls[0].get("order") == "relevance"
