"""Tests for where_did_i_leave_off + reflexive_surface integration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.reflexive import ReflexiveSurface


def _seed(root: Path, threads_spec):
    mem = ExperientialMemory(root=str(root / "chronicle"))
    for domain, question, context in threads_spec:
        mem.record_open_thread(question=question, context=context, domain=domain)
    return mem


def test_reflexive_surface_ranks_matched_threads_first(tmp_path):
    _seed(
        tmp_path,
        [
            ("unrelated,other", "Generic question about nothing here", "context"),
            (
                "compass,witness,governance",
                "How should the compass handle imperative bypass?",
                "relates to governance work",
            ),
            ("temple-wars,rts", "Fog of war vs enemy AI next?", "game loop"),
        ],
    )
    surface = ReflexiveSurface(sovereign_root=tmp_path)
    result = surface.surface(
        domain_tags=["compass", "witness", "governance"],
        limit_per_bucket=5,
    )
    matched = result["matched_open_threads"]
    assert len(matched) >= 1
    top = matched[0]
    assert "compass" in top["domain"] or "witness" in top["domain"]


def test_reflexive_surface_scoring_explanation_mentions_counts(tmp_path):
    _seed(
        tmp_path,
        [
            ("compass,governance", "Q1", "ctx"),
            ("other", "Q2", "ctx"),
        ],
    )
    surface = ReflexiveSurface(sovereign_root=tmp_path)
    result = surface.surface(domain_tags=["compass"])
    explanation = result["scoring_explanation"]
    assert "open_threads" in explanation
    assert "tag_overlap" in explanation or "recency" in explanation


def test_where_did_i_leave_off_without_domain_tags_has_no_resonance_section(tmp_path, monkeypatch):
    """Default behavior (no domain_tags) produces no CONTEXTUAL RESONANCE section."""
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    from sovereign_stack import server as srv

    # Reset module-level singletons to use tmp_path
    srv.experiential = ExperientialMemory(root=str(tmp_path / "chronicle"))
    srv.reflexive_surface = ReflexiveSurface(sovereign_root=tmp_path)

    result = asyncio.run(srv.handle_tool("where_did_i_leave_off", {"source_instance": "test"}))
    text = result[0].text
    assert "CONTEXTUAL RESONANCE" not in text
    assert "WHERE DID I LEAVE OFF" in text


def test_where_did_i_leave_off_with_domain_tags_adds_resonance_section(tmp_path, monkeypatch):
    """Providing domain_tags injects a CONTEXTUAL RESONANCE section with matched threads."""
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    from sovereign_stack import server as srv

    srv.experiential = ExperientialMemory(root=str(tmp_path / "chronicle"))
    srv.experiential.record_open_thread(
        question="Should the compass recognize imperative bypass?",
        context="v10.1 WITNESS training",
        domain="compass,witness,governance",
    )
    srv.reflexive_surface = ReflexiveSurface(sovereign_root=tmp_path)

    result = asyncio.run(
        srv.handle_tool(
            "where_did_i_leave_off",
            {"source_instance": "test", "domain_tags": ["compass", "witness"]},
        )
    )
    text = result[0].text
    assert "CONTEXTUAL RESONANCE" in text
    assert "compass, witness" in text
    assert "Matched open threads" in text
    assert "imperative bypass" in text


def test_where_did_i_leave_off_with_project_shows_project_in_header(tmp_path, monkeypatch):
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    from sovereign_stack import server as srv

    srv.experiential = ExperientialMemory(root=str(tmp_path / "chronicle"))
    srv.experiential.record_open_thread(
        question="project Q",
        context="sovereign-stack context",
        domain="stack,architecture",
    )
    srv.reflexive_surface = ReflexiveSurface(sovereign_root=tmp_path)

    result = asyncio.run(
        srv.handle_tool(
            "where_did_i_leave_off",
            {
                "source_instance": "test",
                "domain_tags": ["stack"],
                "project": "sovereign-stack",
            },
        )
    )
    text = result[0].text
    assert "CONTEXTUAL RESONANCE" in text
    assert "sovereign-stack" in text


def test_where_did_i_leave_off_empty_domain_tags_list_does_not_trigger(tmp_path, monkeypatch):
    """Empty array should behave identically to no domain_tags."""
    monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_path))
    from sovereign_stack import server as srv

    srv.experiential = ExperientialMemory(root=str(tmp_path / "chronicle"))
    srv.reflexive_surface = ReflexiveSurface(sovereign_root=tmp_path)

    result = asyncio.run(
        srv.handle_tool(
            "where_did_i_leave_off",
            {"source_instance": "test", "domain_tags": []},
        )
    )
    text = result[0].text
    assert "CONTEXTUAL RESONANCE" not in text
