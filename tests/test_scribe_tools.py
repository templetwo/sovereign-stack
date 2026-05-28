"""Unit tests for scribe.tools — path-scope guard, dispatch, redaction."""

from __future__ import annotations

import json

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
