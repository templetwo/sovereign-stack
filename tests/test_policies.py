"""
Tests for the v1.7.0 policy registry (policies.py).

Spec section 6: register/amend/retire fold, latest-wins, comma-tag
domain filter, empty-registry honesty, footer derivation text, set_by
required, source_refs validation, well-formed tool schemas.

Hermetic — every registry lives under tmp_path; nothing touches
~/.sovereign live data.
"""

import json
import re

import pytest

from sovereign_stack.policies import (
    EMPTY_REGISTRY_LINE,
    POLICY_TOOL_INTENTS,
    POLICY_TOOL_TIERS,
    POLICY_TOOLS,
    PolicyRegistry,
    handle_policy_tool,
)


@pytest.fixture
def registry(tmp_path):
    """Registry over a path whose directory does not exist yet (lazy creation)."""
    return PolicyRegistry(tmp_path / "policies" / "policies.jsonl")


def _register(registry, statement="No em dashes in casual writing.", domain="writing,style"):
    return registry.set_policy(statement=statement, domain=domain, set_by="anthony")


# ── set_policy: register ──


class TestRegister:
    def test_returns_full_schema_record(self, registry):
        record = _register(registry)
        for field in (
            "policy_id",
            "version",
            "timestamp",
            "statement",
            "domain",
            "status",
            "set_by",
            "by",
            "source_refs",
        ):
            assert field in record
        assert record["version"] == 1
        assert record["status"] == "active"
        assert record["set_by"] == "anthony"
        assert record["statement"] == "No em dashes in casual writing."

    def test_policy_id_is_dated_slug(self, registry):
        record = _register(registry)
        assert re.fullmatch(r"pol_\d{8}_no-em-dashes-in-casual-writing", record["policy_id"])

    def test_collision_suffix(self, registry):
        first = _register(registry)
        second = _register(registry, domain="reddit")
        third = _register(registry, domain="email")
        assert second["policy_id"] == f"{first['policy_id']}-2"
        assert third["policy_id"] == f"{first['policy_id']}-3"

    def test_statement_required_for_new_policy(self, registry):
        with pytest.raises(ValueError, match="statement is required"):
            registry.set_policy(statement="", domain="x", set_by="anthony")

    def test_domain_required_for_new_policy(self, registry):
        with pytest.raises(ValueError, match="domain is required"):
            registry.set_policy(statement="A policy.", domain="", set_by="anthony")

    def test_lazy_creation_only_on_write(self, registry):
        parent = registry.policies_path.parent
        assert not parent.exists()
        # Reads never create anything.
        registry.current_policies()
        registry.boot_line()
        assert not parent.exists()
        _register(registry)
        assert registry.policies_path.exists()

    def test_append_only(self, registry):
        record = _register(registry)
        first_line = registry.policies_path.read_text().splitlines()[0]
        registry.set_policy(
            statement="Amended.", domain="", set_by="anthony", policy_id=record["policy_id"]
        )
        lines = registry.policies_path.read_text().splitlines()
        assert len(lines) == 2
        assert lines[0] == first_line  # earlier records never rewritten


# ── set_policy: human gate ──


class TestSetByRequired:
    @pytest.mark.parametrize("set_by", ["", "   ", None])
    def test_rejected(self, registry, set_by):
        with pytest.raises(ValueError, match="human-gated"):
            registry.set_policy(statement="A policy.", domain="x", set_by=set_by)

    def test_nothing_written_on_rejection(self, registry):
        with pytest.raises(ValueError):
            registry.set_policy(statement="A policy.", domain="x", set_by="")
        assert not registry.policies_path.exists()


# ── set_policy: amend / retire ──


class TestAmendRetire:
    def test_amend_bumps_version(self, registry):
        record = _register(registry)
        amended = registry.set_policy(
            statement="No em dashes anywhere casual.",
            domain="writing",
            set_by="anthony",
            policy_id=record["policy_id"],
        )
        assert amended["policy_id"] == record["policy_id"]
        assert amended["version"] == 2
        assert amended["status"] == "active"

    def test_amend_unknown_policy_id_rejected(self, registry):
        with pytest.raises(ValueError, match="unknown policy_id"):
            registry.set_policy(
                statement="x", domain="y", set_by="anthony", policy_id="pol_20260101_ghost"
            )

    def test_retire(self, registry):
        record = _register(registry)
        retired = registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=record["policy_id"],
            status="retired",
        )
        assert retired["status"] == "retired"
        assert retired["version"] == 2
        # Carry-forward: empty statement/domain inherit the previous record.
        assert retired["statement"] == record["statement"]
        assert retired["domain"] == record["domain"]

    def test_retire_requires_policy_id(self, registry):
        with pytest.raises(ValueError, match="retiring requires policy_id"):
            registry.set_policy(statement="x", domain="y", set_by="anthony", status="retired")

    def test_double_retire_rejected(self, registry):
        record = _register(registry)
        registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=record["policy_id"],
            status="retired",
        )
        with pytest.raises(ValueError, match="already retired"):
            registry.set_policy(
                statement="",
                domain="",
                set_by="anthony",
                policy_id=record["policy_id"],
                status="retired",
            )

    def test_unretire_via_active_record(self, registry):
        record = _register(registry)
        registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=record["policy_id"],
            status="retired",
        )
        revived = registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=record["policy_id"],
            status="active",
        )
        assert revived["version"] == 3
        assert registry.fold()[record["policy_id"]]["status"] == "active"

    def test_invalid_status_rejected(self, registry):
        with pytest.raises(ValueError, match="status"):
            registry.set_policy(statement="x", domain="y", set_by="anthony", status="paused")

    def test_register_amend_retire_fold(self, registry):
        """Full lifecycle: the fold always shows exactly the latest record."""
        record = _register(registry)
        pid = record["policy_id"]
        registry.set_policy(statement="Amended text.", domain="", set_by="anthony", policy_id=pid)
        assert registry.fold()[pid]["statement"] == "Amended text."
        assert registry.fold()[pid]["version"] == 2
        registry.set_policy(
            statement="", domain="", set_by="anthony", policy_id=pid, status="retired"
        )
        folded = registry.fold()
        assert len(folded) == 1
        assert folded[pid]["status"] == "retired"
        assert folded[pid]["version"] == 3
        # Ledger keeps all three records.
        assert len(registry.load_records()) == 3


# ── set_policy: source_refs grammar ──


class TestSourceRefs:
    def test_all_valid_types_accepted(self, registry):
        refs = [
            {"type": "claim", "ref": "a" * 64},
            {"type": "archive", "ref": "arc_123"},
            {"type": "letter", "ref": "to_arrival/2026-06-01.md"},
            {"type": "doc", "ref": "CLAUDE.md"},
            {"type": "human", "ref": "anthony"},
        ]
        record = registry.set_policy(
            statement="Receipted policy.",
            domain="chronicle",
            set_by="anthony",
            source_refs=refs,
        )
        assert record["source_refs"] == refs

    def test_unknown_type_rejected_naming_offender(self, registry):
        with pytest.raises(ValueError, match=r"source_refs\[1\].*'url'"):
            registry.set_policy(
                statement="x",
                domain="y",
                set_by="anthony",
                source_refs=[
                    {"type": "human", "ref": "anthony"},
                    {"type": "url", "ref": "https://example.com"},
                ],
            )

    def test_missing_ref_rejected(self, registry):
        with pytest.raises(ValueError, match=r"source_refs\[0\].*non-empty string 'ref'"):
            registry.set_policy(
                statement="x", domain="y", set_by="anthony", source_refs=[{"type": "human"}]
            )

    def test_empty_ref_rejected(self, registry):
        with pytest.raises(ValueError, match="non-empty string 'ref'"):
            registry.set_policy(
                statement="x",
                domain="y",
                set_by="anthony",
                source_refs=[{"type": "doc", "ref": "  "}],
            )

    def test_non_dict_entry_rejected(self, registry):
        with pytest.raises(ValueError, match=r"source_refs\[0\] must be an object"):
            registry.set_policy(
                statement="x", domain="y", set_by="anthony", source_refs=["human:anthony"]
            )

    def test_extra_keys_rejected(self, registry):
        with pytest.raises(ValueError, match=r"unknown key\(s\) \['sha256'\]"):
            registry.set_policy(
                statement="x",
                domain="y",
                set_by="anthony",
                source_refs=[{"type": "doc", "ref": "CLAUDE.md", "sha256": "f" * 64}],
            )

    def test_non_list_rejected(self, registry):
        with pytest.raises(ValueError, match="must be a list"):
            registry.set_policy(
                statement="x",
                domain="y",
                set_by="anthony",
                source_refs={"type": "human", "ref": "anthony"},
            )

    def test_nothing_written_on_rejection(self, registry):
        with pytest.raises(ValueError):
            registry.set_policy(
                statement="x", domain="y", set_by="anthony", source_refs=[{"type": "bogus"}]
            )
        assert not registry.policies_path.exists()


# ── current_policies: fold + display ──


class TestCurrentPolicies:
    def test_latest_wins(self, registry):
        record = _register(registry, statement="Old statement here.")
        registry.set_policy(
            statement="New statement wins.",
            domain="",
            set_by="anthony",
            policy_id=record["policy_id"],
        )
        out = registry.current_policies()
        assert "New statement wins." in out
        assert "Old statement here." not in out
        assert "v2" in out

    def test_comma_tag_domain_filter(self, registry):
        _register(registry, statement="Style policy.", domain="writing,style,reddit")
        _register(registry, statement="Infra policy.", domain="infrastructure")
        # Element match hits.
        out = registry.current_policies(domain="style")
        assert "Style policy." in out
        assert "Infra policy." not in out
        # First element too.
        assert "Style policy." in registry.current_policies(domain="writing")
        # Substring of a tag is NOT a match (element convention, memory.py:664).
        out = registry.current_policies(domain="sty")
        assert "Style policy." not in out
        assert "No policies match" in out

    def test_retired_held_back_by_default(self, registry):
        keep = _register(registry, statement="Keeper policy.", domain="a")
        gone = _register(registry, statement="Retiree policy.", domain="b")
        registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=gone["policy_id"],
            status="retired",
        )
        out = registry.current_policies()
        assert "Keeper policy." in out
        assert "Retiree policy." not in out
        assert "1 retired held back" in out
        assert "include_retired=true" in out
        assert keep["policy_id"] in out

    def test_include_retired_shows_marked(self, registry):
        gone = _register(registry, statement="Retiree policy.", domain="b")
        registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=gone["policy_id"],
            status="retired",
        )
        out = registry.current_policies(include_retired=True)
        assert "Retiree policy." in out
        assert "(retired)" in out
        assert "1 retired shown." in out

    def test_active_count_in_header(self, registry):
        _register(registry, statement="One.", domain="a")
        _register(registry, statement="Two.", domain="b")
        assert "2 active" in registry.current_policies()

    def test_source_refs_rendered(self, registry):
        registry.set_policy(
            statement="Receipted.",
            domain="a",
            set_by="anthony",
            source_refs=[{"type": "human", "ref": "anthony"}],
        )
        assert "human:anthony" in registry.current_policies()

    def test_corrupt_lines_skipped(self, registry):
        _register(registry)
        with open(registry.policies_path, "a") as f:
            f.write("not json\n")
            f.write('{"no_policy_id": true}\n')
        assert len(registry.load_records()) == 1
        assert "1 active" in registry.current_policies()


# ── current_policies: honesty + footer ──


class TestHonestyAndFooter:
    def test_empty_registry_honest_line_verbatim(self, registry):
        out = registry.current_policies()
        # Verbatim from the spec — pinned as a literal, not via the constant.
        assert "no policies registered yet; run season_review for candidates." in out
        assert EMPTY_REGISTRY_LINE in out

    def test_footer_derivation_text(self, registry):
        _register(registry)
        out = registry.current_policies()
        # Source-of-truth path + fold rule.
        assert str(registry.policies_path) in out
        assert "latest record per policy_id wins" in out
        # Counts.
        assert "1 active" in out
        assert "0 retired." in out
        # How to enact, human-gated.
        assert "set_policy" in out
        assert "human-gated" in out

    def test_footer_present_even_when_empty(self, registry):
        out = registry.current_policies()
        assert str(registry.policies_path) in out
        assert "set_policy" in out
        assert "0 active" in out

    def test_domain_filter_miss_is_honest(self, registry):
        _register(registry)
        out = registry.current_policies(domain="nonexistent")
        assert 'No policies match domain "nonexistent"' in out
        assert "1 registered" in out


# ── boot_line ──


class TestBootLine:
    def test_none_when_empty(self, registry):
        assert registry.boot_line() is None

    def test_counts_active(self, registry):
        _register(registry)
        assert registry.boot_line() == "Standing policies: 1 active — current_policies()"

    def test_all_retired_still_surfaces(self, registry):
        record = _register(registry)
        registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=record["policy_id"],
            status="retired",
        )
        assert registry.boot_line() == "Standing policies: 0 active — current_policies()"


# ── MCP tool schemas ──


class TestToolSchemas:
    def test_exactly_two_tools(self):
        assert [t.name for t in POLICY_TOOLS] == ["current_policies", "set_policy"]

    def test_descriptions_nonempty(self):
        for tool in POLICY_TOOLS:
            assert tool.description and len(tool.description) > 40

    def test_every_property_has_type_and_description(self):
        def check_properties(properties):
            for prop_name, prop in properties.items():
                assert "type" in prop, f"{prop_name} missing type"
                assert prop.get("description"), f"{prop_name} missing description"
                if prop.get("type") == "array" and "items" in prop:
                    items = prop["items"]
                    if items.get("type") == "object":
                        check_properties(items.get("properties", {}))

        for tool in POLICY_TOOLS:
            schema = tool.inputSchema
            assert schema["type"] == "object"
            check_properties(schema.get("properties", {}))

    def test_required_lists_valid(self):
        for tool in POLICY_TOOLS:
            schema = tool.inputSchema
            props = set(schema.get("properties", {}))
            for req in schema.get("required", []):
                assert req in props, f"{tool.name}: required {req} not in properties"

    def test_set_policy_required_args(self):
        schema = next(t for t in POLICY_TOOLS if t.name == "set_policy").inputSchema
        assert schema["required"] == ["statement", "domain", "set_by"]

    def test_tier_and_intent_maps(self):
        names = {t.name for t in POLICY_TOOLS}
        assert set(POLICY_TOOL_TIERS) == names
        assert set(POLICY_TOOL_INTENTS) == names
        assert POLICY_TOOL_TIERS["current_policies"] == "essential"
        assert POLICY_TOOL_TIERS["set_policy"] == "core"
        assert POLICY_TOOL_INTENTS["current_policies"] == "orient"
        assert POLICY_TOOL_INTENTS["set_policy"] == "govern"


# ── MCP dispatcher ──


class TestHandlePolicyTool:
    def test_current_policies_dispatch(self, registry):
        out = handle_policy_tool("current_policies", {}, registry)
        assert EMPTY_REGISTRY_LINE in out

    def test_current_policies_args_passthrough(self, registry):
        record = _register(registry, domain="writing,style")
        registry.set_policy(
            statement="",
            domain="",
            set_by="anthony",
            policy_id=record["policy_id"],
            status="retired",
        )
        out = handle_policy_tool(
            "current_policies", {"domain": "style", "include_retired": True}, registry
        )
        assert "(retired)" in out

    def test_set_policy_dispatch(self, registry):
        out = handle_policy_tool(
            "set_policy",
            {"statement": "A new policy.", "domain": "x", "set_by": "anthony"},
            registry,
        )
        assert "registered" in out
        assert "pol_" in out
        assert len(registry.load_records()) == 1

    def test_set_policy_amend_and_retire_verbs(self, registry):
        record = _register(registry)
        out = handle_policy_tool(
            "set_policy",
            {
                "statement": "Amended.",
                "domain": "",
                "set_by": "anthony",
                "policy_id": record["policy_id"],
            },
            registry,
        )
        assert "amended" in out
        out = handle_policy_tool(
            "set_policy",
            {
                "statement": "",
                "domain": "",
                "set_by": "anthony",
                "policy_id": record["policy_id"],
                "status": "retired",
            },
            registry,
        )
        assert "retired" in out

    def test_set_policy_rejection_is_text_not_exception(self, registry):
        out = handle_policy_tool(
            "set_policy", {"statement": "x", "domain": "y", "set_by": ""}, registry
        )
        assert "set_policy rejected" in out
        assert "human-gated" in out
        assert not registry.policies_path.exists()

    def test_none_arguments_tolerated(self, registry):
        out = handle_policy_tool("current_policies", None, registry)
        assert EMPTY_REGISTRY_LINE in out

    def test_unknown_tool(self, registry):
        assert "Unknown policy tool" in handle_policy_tool("retire_policy", {}, registry)


# ── Ledger rebuildability ──


class TestLedgerIntegrity:
    def test_fold_rebuilds_from_raw_file(self, registry):
        """The file alone is sufficient — a fresh registry over the same
        path folds to identical state."""
        record = _register(registry)
        registry.set_policy(
            statement="Amended.", domain="", set_by="anthony", policy_id=record["policy_id"]
        )
        fresh = PolicyRegistry(registry.policies_path)
        assert fresh.fold() == registry.fold()
        # And the raw lines are valid JSON with the spec's field set.
        for line in registry.policies_path.read_text().splitlines():
            parsed = json.loads(line)
            assert set(parsed) == {
                "policy_id",
                "version",
                "timestamp",
                "statement",
                "domain",
                "status",
                "set_by",
                "by",
                "source_refs",
            }
