"""Tests for the verbatim archive layer.

archive_exchange / recall_exchange / list_exchanges store the actual bytes of an
external exchange, content-addressed and hash-verified, separate from the curated
chronicle. These tests pin the integrity guarantee (a summary can never silently
stand in for a missing artifact) and the human-legible on-disk layout.
"""

import hashlib
import os

from sovereign_stack.memory import ExperientialMemory


def _em(tmp_path):
    return ExperientialMemory(root=str(tmp_path))


def test_archive_then_recall_verified(tmp_path):
    em = _em(tmp_path)
    content = "verbatim line one\nverbatim line two\n"
    rec = em.archive_exchange(
        content,
        source="gemini-3.5-flash",
        descriptor="v3 admission record",
        vector_id="prompt_source_tokens",
    )
    assert rec["archive_id"] == hashlib.sha256(content.encode()).hexdigest()
    got = em.recall_exchange(rec["archive_id"])
    assert got["integrity"] == "verified"
    assert got["content"] == content


def test_descriptor_layout_in_path(tmp_path):
    em = _em(tmp_path)
    rec = em.archive_exchange(
        "x",
        source="ChatGPT",
        descriptor="framing note",
        vector_id="chronicle_failure_profile",
    )
    # Human-legible: grouped by vector, filename carries source + descriptor,
    # short hash as the anchor suffix.
    assert "/chronicle_failure_profile/" in rec["path"]
    assert "chatgpt" in rec["path"] and "framing-note" in rec["path"]
    assert rec["path"].endswith(rec["archive_id"][:12] + ".txt")


def test_prefix_recall(tmp_path):
    em = _em(tmp_path)
    rec = em.archive_exchange("hello", source="claude")
    got = em.recall_exchange(rec["archive_id"][:10])
    assert got["integrity"] == "verified"


def test_tamper_detected_as_mismatch(tmp_path):
    em = _em(tmp_path)
    rec = em.archive_exchange("authentic bytes", source="claude")
    with open(rec["path"], "w", encoding="utf-8") as f:
        f.write("tampered")
    assert em.recall_exchange(rec["archive_id"])["integrity"] == "mismatch"


def test_missing_blob_detected(tmp_path):
    em = _em(tmp_path)
    rec = em.archive_exchange("bytes", source="claude")
    os.remove(rec["path"])
    assert em.recall_exchange(rec["archive_id"])["integrity"] == "missing"


def test_unknown_id_is_honest(tmp_path):
    em = _em(tmp_path)
    assert em.recall_exchange("00nope")["integrity"] == "unknown"


def test_list_filters_by_vector(tmp_path):
    em = _em(tmp_path)
    em.archive_exchange("a", source="gemini", vector_id="prompt_source_tokens")
    em.archive_exchange("b", source="chatgpt", vector_id="chronicle_failure_profile")
    assert len(em.list_exchanges()) == 2
    assert len(em.list_exchanges(vector_id="prompt_source_tokens")) == 1


def test_archives_do_not_pollute_insight_recall(tmp_path):
    """The whole point of a sibling layer: verbatim must not leak into curated recall."""
    em = _em(tmp_path)
    em.archive_exchange("VERBATIM payload that must not surface as an insight", source="gemini")
    em.record_insight(domain="test", content="a curated claim")
    insights = em.recall_insights(domain="test")
    assert all("VERBATIM payload" not in (i.get("content", "") or "") for i in insights)
