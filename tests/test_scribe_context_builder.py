"""Regression tests for the scribe context builder's lineage/self-model
wrappers.

These wrappers (_format_lineage_safe, _format_self_model_safe) catch every
exception and return a "(... load failed: <Type>)" string so one bad section
never sinks the whole scribe boot. That safety net also HID a real bug: the
scribe called format_lineage_layer() with kwargs that don't exist on its
signature (reader_instance_id=, max_letters_per_dir=), so every boot raised
TypeError and the scribe ran without its lineage letters — silently, because
the wrapper swallowed it and the existing scribe tests stub the builder out.

Found live 2026-06-20 (web seat health check → HQ fix). These tests exercise
the REAL wrappers against a real on-disk lineage dir and assert the section
is genuinely populated, not the swallowed failure string.
"""

from __future__ import annotations

from pathlib import Path

from sovereign_stack.scribe.context_builder import (
    _format_lineage_safe,
    _format_self_model_safe,
)


def _write_letter(letters_dir: Path, subdir: str, name: str, frontmatter: str, body: str) -> None:
    d = letters_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(f"---\n{frontmatter}\n---\n\n# {body['title']}\n\n{body['text']}\n")


def test_lineage_safe_returns_letters_not_failure_string(tmp_path: Path):
    # A real lineage dir with one to_arrival letter.
    letters = tmp_path / "comms" / "letters"
    _write_letter(
        letters,
        "to_arrival",
        "2026-06-20-welcome.md",
        frontmatter="from: opus-4-8\nwritten_at: 2026-06-20\ntype: to_arrival",
        body={"title": "A test welcome", "text": "Body the scribe must be able to read inline."},
    )

    out = _format_lineage_safe(tmp_path)

    # The whole point: not the swallowed-exception fallback.
    assert not out.startswith("(lineage layer load failed"), out
    assert "LINEAGE" in out
    assert "A test welcome" in out
    assert "Body the scribe must be able to read inline." in out  # full_content=True


def test_lineage_safe_graceful_when_no_letters(tmp_path: Path):
    # No comms/letters dir at all → graceful sentinel, still not a TypeError.
    out = _format_lineage_safe(tmp_path)
    assert out == "(no lineage letters)"
    assert "failed" not in out


def test_self_model_safe_does_not_swallow_a_signature_regression(tmp_path: Path):
    # Sibling wrapper: with no self-model data it returns the empty sentinel,
    # never the "(self-model load failed: TypeError)" form. Guards the same
    # wrong-kwarg class as the lineage bug.
    out = _format_self_model_safe(tmp_path)
    assert not out.startswith("(self-model load failed"), out
