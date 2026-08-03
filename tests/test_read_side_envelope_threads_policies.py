"""Read-side envelope for get_open_threads + coverage footer for current_policies.

The aae7281 rule, extended (draft/read-side-envelope-2, held for HQ review):
a read surface states its own coverage. Measured 2026-08-02 (Stack chronicle
domain sovereign-stack,fail-open,read-side): the get_open_threads tool
returned a bare JSON list — limit:25 returned exactly 25 rows,
indistinguishable from completeness, while the store held 157 open threads.
current_policies returned a bare 9,324-char string with no coverage
statement.

Per standing law #2 the gates here demonstrably FAIL on the unfixed base
(main @ f2f09a4): `envelope=` does not exist on get_open_threads
(TypeError), the tool dispatch returns a bare list (and prose on empty),
current_policies has no Coverage line, and recall_arc has no `_items`
(every envelope response was silently skipped by isinstance-list gates).
Tests marked "positive control" pass on both sides and are labeled as such.

Hermetic — every store lives under tmp_path / _isolated_server's tmp root;
nothing touches the live ~/.sovereign.
"""

import json

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.policies import PolicyRegistry

ENVELOPE_FIELDS = (
    "items",
    "returned",
    "total_matched",
    "offset",
    "scope",
    "truncated",
    "partial_reasons",
    "continuation",
)


@pytest.fixture
def mem(tmp_path):
    root = tmp_path / "chronicle"
    root.mkdir(parents=True)
    return ExperientialMemory(root=str(root))


def _seed_threads(mem, n, domain="testing"):
    for i in range(n):
        mem.record_open_thread(
            question=f"Question number {i} still unanswered?",
            context=f"context {i}",
            domain=domain,
            session_id="envelope-test",
        )


# ── get_open_threads: memory-level envelope (FAIL on base: TypeError) ──


class TestOpenThreadsEnvelope:
    def test_envelope_reports_total_matched_and_truncation(self, mem):
        """A capped page names the total it was cut from and hands back the cursor."""
        _seed_threads(mem, 12)
        env = mem.get_open_threads(limit=5, envelope=True)
        for field in ENVELOPE_FIELDS:
            assert field in env, f"envelope missing {field}"
        assert env["returned"] == 5
        assert len(env["items"]) == 5
        assert env["total_matched"] == 12
        assert env["truncated"] is True
        assert "truncated:12" in env["partial_reasons"]
        assert env["continuation"] == {"offset": 5, "limit": 5}

    def test_envelope_complete_read_has_empty_partial_reasons(self, mem):
        """Invariant 5: partial_reasons empty IFF complete, exact, lossless."""
        _seed_threads(mem, 3)
        env = mem.get_open_threads(limit=10, envelope=True)
        assert env["returned"] == env["total_matched"] == 3
        assert env["truncated"] is False
        assert env["partial_reasons"] == []
        assert env["continuation"] is None

    def test_envelope_domain_empty_is_explicit(self, mem):
        """D1: a filter matching nothing says so — not an empty list
        indistinguishable from an empty store."""
        _seed_threads(mem, 2, domain="real-domain")
        env = mem.get_open_threads(domain="typo-domain", envelope=True)
        assert env["items"] == []
        assert env["scope"]["mode"] == "domain-empty"
        assert "domain_no_match" in env["partial_reasons"]

    def test_envelope_scope_counts_domains(self, mem):
        _seed_threads(mem, 2, domain="alpha")
        _seed_threads(mem, 2, domain="beta")
        env = mem.get_open_threads(domain="alpha", envelope=True)
        assert env["scope"]["mode"] == "domain"
        assert env["scope"]["domain_query"] == "alpha"
        assert env["scope"]["domains_searched"] == 1
        assert env["scope"]["domains_total"] == 2

    def test_envelope_counts_corrupt_lines(self, mem):
        """A skipped line is a partial read — counted, not silently dropped."""
        _seed_threads(mem, 2, domain="mixed")
        thread_file = mem.threads_dir / "mixed.jsonl"
        with open(thread_file, "a") as fh:
            fh.write("{this is not json\n")
        env = mem.get_open_threads(domain="mixed", envelope=True)
        assert env["total_matched"] == 2
        assert "corrupt_line_skipped:1" in env["partial_reasons"]

    def test_envelope_supersedes_with_total(self, mem):
        """When both are passed, the envelope (a superset) wins."""
        _seed_threads(mem, 4)
        env = mem.get_open_threads(limit=2, envelope=True, with_total=True)
        assert "items" in env and "threads" not in env

    # Positive controls — pass on both base and fixed tree.

    def test_default_shape_still_bare_list(self, mem):
        """Positive control: internal callers (boot ritual, arrival_state,
        reflexive, scribe context_builder) keep the list shape."""
        _seed_threads(mem, 3)
        result = mem.get_open_threads()
        assert isinstance(result, list)
        assert len(result) == 3

    def test_with_total_shape_unchanged(self, mem):
        """Positive control: scribe/tools.py's with_total contract holds."""
        _seed_threads(mem, 3)
        result = mem.get_open_threads(limit=2, with_total=True)
        assert set(result.keys()) == {"threads", "total", "has_more", "offset"}
        assert result["total"] == 3


# ── get_open_threads: tool dispatch returns the envelope (FAIL on base: bare list / prose) ──


class TestOpenThreadsDispatch:
    def _dispatch(self, srv, arguments):
        import asyncio

        result = asyncio.run(srv._dispatch_tool("get_open_threads", arguments))
        assert result and result[0].type == "text"
        return result[0].text

    def test_tool_response_is_envelope(self):
        from tests.test_nape_autohook import _isolated_server

        with _isolated_server("envelope-dispatch-test") as (srv, _tmp_root):
            for i in range(7):
                srv.experiential.record_open_thread(
                    question=f"Dispatch question {i} still unanswered?",
                    context="dispatch",
                    domain="dispatch-test",
                    session_id="envelope-dispatch-test",
                )
            text = self._dispatch(srv, {"limit": 3})
            payload = json.loads(text)
            assert isinstance(payload, dict), "tool response is still a bare list"
            for field in ENVELOPE_FIELDS:
                assert field in payload, f"tool envelope missing {field}"
            assert payload["returned"] == 3
            assert payload["total_matched"] == 7
            assert payload["truncated"] is True
            assert payload["continuation"] == {"offset": 3, "limit": 3}

    def test_tool_response_empty_case_is_envelope(self):
        """The empty case states its scope instead of prose: items:[] with
        scope.mode distinguishes an empty store from a missed filter."""
        from tests.test_nape_autohook import _isolated_server

        with _isolated_server("envelope-empty-test") as (srv, _tmp_root):
            text = self._dispatch(srv, {})
            payload = json.loads(text)  # base returns prose -> ValueError
            assert payload["items"] == []
            assert payload["total_matched"] == 0
            assert payload["scope"]["mode"] == "all"


# ── current_policies: coverage footer (FAIL on base: no Coverage line) ──


class TestPoliciesCoverageFooter:
    @pytest.fixture
    def registry(self, tmp_path):
        return PolicyRegistry(tmp_path / "policies" / "policies.jsonl")

    def _seed(self, registry, retire_one=True):
        first = registry.set_policy(
            statement="First standing policy.", domain="writing,style", set_by="anthony"
        )
        second = registry.set_policy(
            statement="Second standing policy.", domain="ops", set_by="anthony"
        )
        if retire_one:
            registry.set_policy(
                statement="",
                domain="",
                set_by="anthony",
                policy_id=second["policy_id"],
                status="retired",
            )
        return first, second

    def test_coverage_footer_states_shown_of_total(self, registry):
        """A held-back retired set is a partial read and the footer says so."""
        self._seed(registry)
        out = registry.current_policies()
        assert "Coverage: returned=1 of total_matched=2" in out
        assert "scope.mode=all" in out
        assert "truncated=false" in out
        assert "retired_held_back:1" in out

    def test_coverage_footer_complete_read_empty_reasons(self, registry):
        """Invariant 5 on the string surface: nothing held back -> []."""
        self._seed(registry)
        out = registry.current_policies(include_retired=True)
        assert "Coverage: returned=2 of total_matched=2" in out
        assert "partial_reasons=[]" in out

    def test_coverage_footer_domain_empty(self, registry):
        self._seed(registry)
        out = registry.current_policies(domain="nonexistent")
        assert "scope.mode=domain-empty" in out
        assert "domain_no_match" in out

    def test_coverage_footer_domain_scope(self, registry):
        self._seed(registry, retire_one=False)
        out = registry.current_policies(domain="ops")
        assert "Coverage: returned=1 of total_matched=1" in out
        assert 'scope.mode=domain (domain:"ops")' in out
        assert "(2 registered across all domains)" in out

    def test_coverage_footer_present_when_empty(self, registry):
        """Positive control shape + new line: even the empty registry states
        coverage (returned=0 of total_matched=0)."""
        out = registry.current_policies()
        assert "Coverage: returned=0 of total_matched=0" in out


# ── recall_arc: envelope unwrap (FAIL on base: ImportError — no _items) ──


class TestRecallArcEnvelopeUnwrap:
    def test_items_unwraps_envelope(self):
        from sovereign_stack.recall_arc import _items

        assert _items({"items": [{"a": 1}], "total_matched": 9}) == [{"a": 1}]

    def test_items_passes_bare_list_through(self):
        from sovereign_stack.recall_arc import _items

        assert _items([{"a": 1}]) == [{"a": 1}]

    def test_items_defensive_on_junk(self):
        from sovereign_stack.recall_arc import _items

        assert _items(None) == []
        assert _items({"threads": []}) == []
        assert _items("nope") == []
