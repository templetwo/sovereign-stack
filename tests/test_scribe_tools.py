"""Unit tests for scribe.tools — path-scope guard, dispatch, redaction."""

from __future__ import annotations

import json

import pytest

from sovereign_stack.scribe import tools
from sovereign_stack.scribe.tools import (
    anthropic_tool_definitions,
    dispatch_tool,
    tool_chronicle_list_domains,
    tool_names,
)


class TestToolSurface:
    def test_four_tools_registered(self):
        names = tool_names()
        assert set(names) == {
            "chronicle_recall",
            "chronicle_read_file",
            "chronicle_list_domains",
            "chronicle_get_threads",
        }

    def test_anthropic_definitions_well_formed(self):
        defs = anthropic_tool_definitions()
        assert len(defs) == 4
        for d in defs:
            assert "name" in d
            assert "description" in d
            assert "input_schema" in d
            assert d["input_schema"]["type"] == "object"

    def test_dispatch_unknown_tool_returns_error(self):
        result, is_error = dispatch_tool("not_a_tool", {})
        assert is_error is True
        assert "unknown tool" in result


class TestPathScopeGuard:
    def test_rejects_parent_traversal(self):
        result, is_error = dispatch_tool("chronicle_read_file", {"path": "../../.env"})
        assert is_error is True
        assert "outside the chronicle root" in result

    def test_rejects_absolute_unix_path(self):
        result, is_error = dispatch_tool("chronicle_read_file", {"path": "/etc/passwd"})
        assert is_error is True
        assert "absolute paths are not allowed" in result

    def test_rejects_home_tilde(self):
        result, is_error = dispatch_tool("chronicle_read_file", {"path": "~/.env"})
        assert is_error is True
        assert "absolute paths are not allowed" in result

    def test_rejects_empty_path(self):
        result, is_error = dispatch_tool("chronicle_read_file", {"path": ""})
        assert is_error is True
        assert "non-empty string" in result

    def test_rejects_non_string(self):
        result, is_error = dispatch_tool("chronicle_read_file", {"path": 123})
        assert is_error is True

    def test_rejects_missing_path(self):
        result, is_error = dispatch_tool("chronicle_read_file", {})
        assert is_error is True
        # TypeError or path-required from handler signature
        assert "error" in result.lower()


class TestListDomains:
    def test_returns_json(self):
        result = tool_chronicle_list_domains(limit=5)
        parsed = json.loads(result)
        assert "domains" in parsed
        assert "count" in parsed
        assert parsed["count"] == len(parsed["domains"])
        assert parsed["count"] <= 5

    def test_filter_substring(self):
        # 'sovereign-stack' should be a known substring on any live install
        result = tool_chronicle_list_domains(filter="sovereign-stack", limit=10)
        parsed = json.loads(result)
        for domain in parsed["domains"]:
            assert "sovereign-stack" in domain.lower()

    def test_limit_cap_respected(self):
        result = tool_chronicle_list_domains(limit=999_999)
        parsed = json.loads(result)
        # Even with huge limit, the cap kicks in
        assert parsed["count"] <= tools.MAX_DOMAIN_LIST


class TestRecallLimits:
    def test_limit_clamped_to_max(self):
        result, is_error = dispatch_tool("chronicle_recall", {"limit": 999_999})
        assert is_error is False
        parsed = json.loads(result)
        assert parsed["limit"] <= tools.MAX_RECALL_LIMIT

    def test_limit_minimum_one(self):
        result, is_error = dispatch_tool("chronicle_recall", {"limit": -5})
        assert is_error is False
        parsed = json.loads(result)
        assert parsed["limit"] >= 1


class TestErrorEnvelope:
    def test_error_returns_tuple_with_is_error_true(self):
        result, is_error = dispatch_tool("chronicle_read_file", {"path": "../../etc/passwd"})
        assert isinstance(result, str)
        assert is_error is True

    def test_success_returns_is_error_false(self):
        result, is_error = dispatch_tool("chronicle_list_domains", {"limit": 1})
        assert is_error is False
        # Should parse as JSON
        json.loads(result)


class TestTruncationAlwaysProducesValidJson:
    """The invariant this fix exists for: a truncated structured result must
    still be JSON.

    `_truncate_result` sliced serialized JSON at a raw character offset, so a
    large chronicle_recall returned ~80,000 characters of almost-JSON. Callers
    discovered the truncation by way of a JSONDecodeError, and the payload
    itself said nothing about having been cut. Found 2026-07-30 when a live-data
    test tripped it; the threads call site had the identical defect and was
    simply not large enough to fail yet.
    """

    CAP = 5_000  # small cap so these run fast; the logic is size-independent

    def _payload(self, n_items, item_chars):
        return {
            "count": n_items,
            "limit": 30,
            "insights": [
                {"timestamp": "2026-07-30T00:00:00", "content": "x" * item_chars}
                for _ in range(n_items)
            ],
        }

    @pytest.mark.parametrize(
        "n_items,item_chars",
        [
            (0, 0),
            (1, 10),
            (1, 100_000),  # a SINGLE item bigger than the whole cap
            (3, 40),
            (30, 400),
            (30, 4_000),
            (200, 900),
            (5, 1_100),  # sits near the boundary
        ],
    )
    def test_output_always_parses(self, n_items, item_chars):
        out = tools._truncate_json_result(
            self._payload(n_items, item_chars), "insights", max_chars=self.CAP
        )
        parsed = json.loads(out)  # the whole point — must not raise
        assert isinstance(parsed, dict)
        assert len(out) <= self.CAP or parsed.get("count") == 0

    def test_truncation_is_declared_in_the_payload_not_only_in_its_length(self):
        out = tools._truncate_json_result(self._payload(50, 1_000), "insights", max_chars=self.CAP)
        p = json.loads(out)
        assert p["truncated"] is True
        assert p["omitted"] > 0
        assert "truncation_reason" in p
        assert p["count"] + p["omitted"] == 50, "returned + omitted must equal the original total"

    def test_count_always_equals_the_items_actually_present(self):
        """A count that does not match the array is the same class of lie as a
        silent truncation."""
        for n, chars in ((50, 1_000), (3, 40), (200, 900)):
            p = json.loads(
                tools._truncate_json_result(self._payload(n, chars), "insights", max_chars=self.CAP)
            )
            assert p["count"] == len(p["insights"])

    def test_an_untruncated_payload_is_left_alone(self):
        """No truncation metadata may appear on a response that was not cut."""
        p = json.loads(
            tools._truncate_json_result(self._payload(2, 10), "insights", max_chars=self.CAP)
        )
        assert "truncated" not in p
        assert "omitted" not in p
        assert p["count"] == 2

    def test_one_oversized_item_yields_zero_items_not_a_mangled_one(self):
        """Better to return nothing and say so than to hand back half an entry."""
        p = json.loads(
            tools._truncate_json_result(self._payload(1, 100_000), "insights", max_chars=self.CAP)
        )
        assert p["insights"] == []
        assert p["count"] == 0
        assert p["omitted"] == 1
        assert p["truncated"] is True

    def test_the_old_slice_really_did_break_and_the_new_path_does_not(self):
        """Anti-vacuity: proves these tests would have caught the original bug."""
        payload = self._payload(3, 40_000)
        sliced = tools._truncate_result(
            json.dumps(payload, indent=2, ensure_ascii=False), max_chars=self.CAP
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(sliced)
        json.loads(tools._truncate_json_result(payload, "insights", max_chars=self.CAP))

    def test_plain_text_truncation_is_unchanged(self):
        """_truncate_result is still correct for its actual job."""
        out = tools._truncate_result("y" * 10_000, max_chars=self.CAP)
        assert len(out) > self.CAP  # marker appended
        assert out.startswith("y" * 100)
        assert "truncated at" in out
