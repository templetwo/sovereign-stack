"""
Content-class RECALL filter (Phase 1, feat/chronicle-content-class).

Locks the FIX-2 recall-side guarantees on recall_insights:

  - default (both params unset) returns every class, untagged included;
  - exclude_content_class drops the named class and KEEPS untagged
    (None-VISIBLE semantics);
  - the drop is STRUCTURAL: a query that matches an excluded entry's OWN text
    still cannot resurface it (the filter runs before the text search, and the
    excluded entry never enters the result set);
  - content_class (include-mode) returns only the named class and drops
    untagged (this is how the blind reviewer blinds);
  - a bare string is accepted, equivalent to a 1-element list;
  - entries are not mutated by the filter;
  - the envelope emits NO new partial_reason for class exclusion
    (total_matched counts survivors only);
  - an MCP-level dispatch of recall_insights with exclude_content_class honors
    the filter (dispatch wiring).
"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory
from tests.test_nape_autohook import _isolated_server

SESSION = "content-class-recall"
DOMAIN = "recall-cc"


@pytest.fixture()
def mem():
    tmp = Path(tempfile.mkdtemp(prefix="cc-recall-"))
    m = ExperientialMemory(root=str(tmp / "chronicle"))
    # A domain mixing all three states: untagged, outcome, specification.
    m.record_insight(DOMAIN, "shared apple untagged note", 0.5, SESSION)
    m.record_insight(DOMAIN, "shared apple outcome note", 0.5, SESSION, content_class="outcome")
    m.record_insight(DOMAIN, "shared apple spec note", 0.5, SESSION, content_class="specification")
    yield m
    shutil.rmtree(tmp, ignore_errors=True)


def _classes(rows):
    return sorted((r.get("content_class") or "<none>") for r in rows)


class TestDefaultUnset:
    def test_default_returns_all_classes(self, mem):
        rows = mem.recall_insights(domain=DOMAIN, limit=50)
        assert _classes(rows) == ["<none>", "outcome", "specification"]


class TestExcludeNoneVisible:
    def test_exclude_drops_tagged_keeps_untagged(self, mem):
        rows = mem.recall_insights(domain=DOMAIN, limit=50, exclude_content_class="outcome")
        # outcome gone; untagged (None) survives — None-VISIBLE.
        assert _classes(rows) == ["<none>", "specification"]

    def test_exclude_multiple(self, mem):
        rows = mem.recall_insights(
            domain=DOMAIN, limit=50, exclude_content_class=["outcome", "specification"]
        )
        assert _classes(rows) == ["<none>"]


class TestStructuralDrop:
    def test_query_matching_excluded_text_cannot_resurface(self, mem):
        # The query terms match the outcome entry's OWN words. If the filter
        # were applied after the text search (or only to the display), this
        # would leak the excluded entry back in.
        rows = mem.recall_insights(
            query="apple outcome note",
            domain=DOMAIN,
            limit=50,
            exclude_content_class="outcome",
        )
        assert not any(r.get("content_class") == "outcome" for r in rows)


class TestIncludeOnly:
    def test_include_returns_only_that_class(self, mem):
        rows = mem.recall_insights(domain=DOMAIN, limit=50, content_class="outcome")
        assert _classes(rows) == ["outcome"]

    def test_include_drops_untagged(self, mem):
        rows = mem.recall_insights(domain=DOMAIN, limit=50, content_class="specification")
        assert all(r.get("content_class") == "specification" for r in rows)
        assert "<none>" not in _classes(rows)


class TestBareStringAccepted:
    def test_bare_string_equals_singleton_list(self, mem):
        s = mem.recall_insights(domain=DOMAIN, limit=50, content_class="outcome")
        lst = mem.recall_insights(domain=DOMAIN, limit=50, content_class=["outcome"])
        assert [r["content"] for r in s] == [r["content"] for r in lst]

    def test_bare_string_exclude(self, mem):
        s = mem.recall_insights(domain=DOMAIN, limit=50, exclude_content_class="outcome")
        lst = mem.recall_insights(domain=DOMAIN, limit=50, exclude_content_class=["outcome"])
        assert [r["content"] for r in s] == [r["content"] for r in lst]


class TestEntriesNotMutated:
    def test_no_injected_keys(self, mem):
        rows = mem.recall_insights(domain=DOMAIN, limit=50, exclude_content_class="outcome")
        allowed = {
            "timestamp",
            "domain",
            "content",
            "intensity",
            "layer",
            "session_id",
            "content_class",
        }
        for r in rows:
            assert set(r.keys()) <= allowed


class TestEnvelopeNoNewPartialReason:
    def test_exclusion_counts_survivors_only(self, mem):
        env = mem.recall_insights(
            domain=DOMAIN, limit=50, exclude_content_class="outcome", envelope=True
        )
        # Excluded entries never enter `insights`, so total_matched is 2,
        # and no class-specific partial_reason is emitted.
        assert env["total_matched"] == 2
        assert env["returned"] == 2
        assert env["partial_reasons"] == []


class TestMCPDispatchWiring:
    def test_dispatch_recall_insights_honors_exclude(self):
        with _isolated_server(SESSION) as (srv, _tmp_root):
            srv.experiential.record_insight(DOMAIN, "d apple untagged", 0.5, SESSION)
            srv.experiential.record_insight(
                DOMAIN, "d apple outcome", 0.5, SESSION, content_class="outcome"
            )
            result = asyncio.run(
                srv._dispatch_tool(
                    "recall_insights",
                    {"domain": DOMAIN, "limit": 50, "exclude_content_class": ["outcome"]},
                )
            )
        env = json.loads(result[0].text)
        classes = [item.get("content_class") for item in env["items"]]
        assert "outcome" not in classes
        assert None in classes  # untagged survives through the dispatch too
        assert env["total_matched"] == 1
