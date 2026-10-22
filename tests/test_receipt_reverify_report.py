"""Receipt re-verify splits moved-on from never-verified (design C1, plan 1 Task 4).

season_review's hygiene pass used to re-hash every stored file receipt and call
any difference "mismatch". Measured 2026-10-21: 119 such lines, 115 of them a
file edited after a receipt that held when it was checked, 4 of them receipts
that never matched at write. Different stories, one word.

seasons.reverify_file_receipts now gives each stored ``file`` receipt one row
with one of six statuses:

  ok              the bytes still hash to the receipt's sha256
  moved_on        stamped "verified" at write; the bytes differ now and the
                  file's mtime is after the write
  never_verified  checked_at_write is not "verified": it never matched, or was
                  never hashed, so no re-hash today can make it a verification
  mismatch        stamped "verified"; the bytes differ and the file is no newer
                  than the write (replaced bytes that kept an older mtime), the
                  case that asks for a reader's attention
  missing         the file is gone
  unreadable      a hazard class, or a read that did not finish in budget

Every receipt here is written through the real record_insight path, so
checked_at_write is the server's own stamp, never a hand-written one. Hermetic:
the chronicle and every evidence file live under tmp_path.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import provenance, seasons
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.provenance import derive_claim_id, iter_chronicle_entries


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def chronicle(tmp_path):
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


@pytest.fixture
def lane(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    return lane_dir


def _entries(mem):
    return [
        entry for entry, _file, where in iter_chronicle_entries(mem.root) if where == "insights"
    ]


def _record(mem, content, path, sha256, **kwargs):
    """Write one insight carrying one file receipt; return the stored entry."""
    mem.record_insight(
        domain="receipts",
        content=content,
        verified_by=[{"kind": "file", "ref": str(path), "sha256": sha256}],
        **kwargs,
    )
    [entry] = [e for e in _entries(mem) if e["content"] == content]
    return entry


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _rewrite(path, data: bytes, mtime: float) -> None:
    """New bytes, with the mtime set explicitly so no clock granularity decides."""
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


def _status_by_ref(mem) -> dict[str, str]:
    return {row["ref"]: row["status"] for row in seasons.reverify_file_receipts(_entries(mem))}


def test_three_seeded_receipts_classify_as_moved_on_never_verified_and_ok(chronicle, lane):
    edited = lane / "edited-later.txt"
    edited.write_bytes(b"the bytes the receipt was checked against\n")
    never = lane / "never-matched.txt"
    never.write_bytes(b"bytes the caller never hashed\n")
    kept = lane / "unchanged.txt"
    kept.write_bytes(b"bytes that are still there\n")

    first = _record(
        chronicle, "verified, then the file moved on", edited, _sha(edited.read_bytes())
    )
    second = _record(chronicle, "never matched at write", never, "0" * 64)
    _record(chronicle, "verified and unchanged", kept, _sha(kept.read_bytes()))
    # The stamps are the server's own, from the write path.
    assert first["verified_by"][0]["checked_at_write"] == "verified"
    assert second["verified_by"][0]["checked_at_write"] == "mismatch"

    # The file is edited after the write: new bytes, an mtime a minute later.
    _rewrite(edited, b"a later edit\n", _epoch(first["timestamp"]) + 60)

    assert _status_by_ref(chronicle) == {
        str(edited): "moved_on",
        str(never): "never_verified",
        str(kept): "ok",
    }


def test_changed_bytes_no_newer_than_the_write_stay_mismatch(chronicle, lane):
    """The control that pins the comparison's direction: different bytes under an
    mtime from before the write (a copy that kept its source's mtime, a restore)
    did not move on after the check, and stay the status that asks for a look."""
    replaced = lane / "replaced.txt"
    replaced.write_bytes(b"original\n")
    entry = _record(
        chronicle, "verified, then replaced under an old mtime", replaced, _sha(b"original\n")
    )

    _rewrite(replaced, b"something else\n", _epoch(entry["timestamp"]) - 3600)

    assert _status_by_ref(chronicle) == {str(replaced): "mismatch"}


def test_a_backdated_row_is_compared_against_its_write_instant(chronicle, lane):
    """The spec says "file mtime after the write". A backdated row's stored
    timestamp is an AUTHORSHIP time, earlier than the write, and the receipt was
    checked at the write (occurred_at). Different bytes whose mtime falls between
    the two did not change after the check, so they are not "moved on"."""
    path = lane / "filed-late.txt"
    path.write_bytes(b"checked at the write instant\n")
    authored = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    entry = _record(
        chronicle,
        "filed a month after it was authored",
        path,
        _sha(path.read_bytes()),
        original_timestamp=authored,
    )
    assert entry["timestamp"] == authored
    assert entry.get("timestamp_source"), "the row must be a backdated one"
    written = _epoch(entry["occurred_at"])

    _rewrite(path, b"different bytes\n", written - 86400)  # after authorship, before the write
    assert _status_by_ref(chronicle) == {str(path): "mismatch"}

    _rewrite(path, b"different bytes\n", written + 60)  # after the write
    assert _status_by_ref(chronicle) == {str(path): "moved_on"}


def test_a_deleted_file_is_missing_and_a_directory_in_its_place_is_unreadable(chronicle, lane):
    gone = lane / "deleted.txt"
    gone.write_bytes(b"deleted after the write\n")
    swapped = lane / "swapped.txt"
    swapped.write_bytes(b"replaced by a directory\n")
    _record(chronicle, "the file was deleted", gone, _sha(gone.read_bytes()))
    _record(chronicle, "the file became a directory", swapped, _sha(swapped.read_bytes()))

    gone.unlink()
    swapped.unlink()
    swapped.mkdir()

    assert _status_by_ref(chronicle) == {str(gone): "missing", str(swapped): "unreadable"}


def test_never_verified_is_read_from_the_write_stamp_before_any_file_read(
    chronicle, lane, monkeypatch
):
    """The stamp decides first, with no IO: a never-matched receipt whose file is
    also gone is still never_verified, and nothing stats, resolves or hashes it.
    validate_file_ref resolves the path (an lstat per component), so it is IO
    too: the stamp precedes the rules as well as the reads."""
    never = lane / "never.txt"
    never.write_bytes(b"the caller hashed something else\n")
    _record(chronicle, "never matched, then deleted", never, "f" * 64)
    never.unlink()

    def _no_read(*_args, **_kwargs):
        raise AssertionError("a never-verified receipt was read from disk")

    monkeypatch.setattr(seasons, "validate_file_ref", _no_read)
    monkeypatch.setattr(seasons, "classify_file_target", _no_read)
    monkeypatch.setattr(seasons, "hash_file_bounded", _no_read)

    assert _status_by_ref(chronicle) == {str(never): "never_verified"}


def test_a_receipt_degraded_to_attested_at_write_is_never_verified_even_when_it_matches(
    chronicle, lane, monkeypatch
):
    """A file too slow to hash at write is stamped "attested". Its bytes may
    match today; the server still never verified it, and the row says so."""
    path = lane / "slow.txt"
    path.write_bytes(b"bytes that hashed too slowly at write\n")

    def _too_slow(_path):
        raise provenance.BoundedIOError("did not complete within 5s")

    with monkeypatch.context() as patch:
        patch.setattr(provenance, "hash_file_bounded", _too_slow)
        entry = _record(chronicle, "degraded at write", path, _sha(path.read_bytes()))
    assert entry["verified_by"][0]["checked_at_write"] == "attested"

    assert _status_by_ref(chronicle) == {str(path): "never_verified"}


def test_rows_name_the_entry_the_ref_the_stamp_and_one_of_six_statuses(chronicle, lane):
    path = lane / "one.txt"
    path.write_bytes(b"one receipt\n")
    entry = _record(chronicle, "a single receipt", path, _sha(path.read_bytes()))

    assert seasons.reverify_file_receipts(_entries(chronicle)) == [
        {
            "claim_id": derive_claim_id(entry),
            "ref": str(path),
            "checked_at_write": "verified",
            "status": "ok",
        }
    ]
    assert seasons.REVERIFY_STATUSES == (
        "ok",
        "moved_on",
        "never_verified",
        "mismatch",
        "missing",
        "unreadable",
    )


def test_only_file_receipts_get_rows_and_attested_kinds_are_not_failures(chronicle):
    """cmd / url / human receipts are stamped "attested" by design. Read through
    the never_verified rule they would all become failure lines; they get no row."""
    chronicle.record_insight(
        domain="receipts",
        content="attested receipts only",
        verified_by=[
            {"kind": "url", "ref": "https://example.org/report"},
            {"kind": "human", "ref": "named by a person"},
        ],
    )

    assert seasons.reverify_file_receipts(_entries(chronicle)) == []
    report = seasons.season_review(chronicle_root=chronicle.root)
    assert "re-verifies as" not in report
    assert "✓ receipt re-verify: 1 receipted entr(ies), no failures." in report


def test_season_review_names_each_status_and_counts_the_split(chronicle, lane):
    edited = lane / "edited.txt"
    edited.write_bytes(b"checked\n")
    never = lane / "never.txt"
    never.write_bytes(b"never matched\n")
    replaced = lane / "replaced.txt"
    replaced.write_bytes(b"checked too\n")
    kept = lane / "kept.txt"
    kept.write_bytes(b"still here\n")

    first = _record(chronicle, "edited after the write", edited, _sha(b"checked\n"))
    _record(chronicle, "never matched", never, "0" * 64)
    third = _record(chronicle, "replaced under an old mtime", replaced, _sha(b"checked too\n"))
    _record(chronicle, "kept", kept, _sha(b"still here\n"))
    _rewrite(edited, b"edited\n", _epoch(first["timestamp"]) + 60)
    _rewrite(replaced, b"replaced\n", _epoch(third["timestamp"]) - 3600)

    report = seasons.season_review(chronicle_root=chronicle.root)

    assert f"file:{edited} re-verifies as moved_on" in report
    assert f"file:{never} re-verifies as never_verified" in report
    assert f"file:{replaced} re-verifies as mismatch" in report
    assert f"file:{kept}" not in report, "an ok receipt is counted, not listed"
    assert (
        "file receipts re-verified: 4 (1 ok, 1 moved_on, 1 never_verified, 1 mismatch,"
        " 0 missing, 0 unreadable)"
    ) in report


def _verified_row_entry(timestamp: str, path, sha256: str) -> dict:
    """A legacy-shaped stored entry, for the branches the write path cannot
    produce (it always stamps a parseable timestamp)."""
    return {
        "timestamp": timestamp,
        "domain": "receipts",
        "content": f"legacy row stamped {timestamp!r}",
        "verified_by": [
            {"kind": "file", "ref": str(path), "sha256": sha256, "checked_at_write": "verified"}
        ],
    }


def test_moved_on_needs_a_write_instant_that_parses(lane):
    """moved_on is the softer verdict, so it needs positive evidence. A row whose
    stamp will not parse gives none, and changed bytes stay mismatch."""
    path = lane / "legacy.txt"
    path.write_bytes(b"changed since\n")  # mtime: now
    unparseable = _verified_row_entry("not-a-date", path, "0" * 64)
    dated = _verified_row_entry("2026-01-01T00:00:00+00:00", path, "0" * 64)

    [row] = seasons.reverify_file_receipts([unparseable])
    assert row["status"] == "mismatch"
    [row] = seasons.reverify_file_receipts([dated])
    assert row["status"] == "moved_on"


def test_an_mtime_stat_that_never_answers_reads_unreadable(lane, monkeypatch):
    """After a hash that differs, the mtime read is bounded like every other read
    of a stored ref; one that never answers is unreadable, never a crash."""
    path = lane / "slow-stat.txt"
    path.write_bytes(b"changed since\n")

    def _wedged(_path):
        raise provenance.BoundedIOError("did not complete within 1s")

    monkeypatch.setattr(seasons, "file_mtime_bounded", _wedged)
    [row] = seasons.reverify_file_receipts(
        [_verified_row_entry("2026-01-01T00:00:00+00:00", path, "0" * 64)]
    )
    assert row["status"] == "unreadable"


# ── The write's rules hold at re-verify (design C3, Task 4 fix round 1) ─────
#
# A receipt stamped "verified" before the rules existed can point where the
# write now refuses to look. Measured read-only on the live record 2026-10-22:
# 17 such receipts (11 scratch path, 4 plugin cache, 2 env-named file), all
# stamped verified. Re-hashing them would read the files the rules keep
# receipts off, so the rules run first and a refused ref is never read.

# Never created: the link's text names it, and resolve() follows the text.
_NEVER_CREATED = "/private/tmp/sovereign-c1-probe-never-created/evidence.txt"


def _env_named(tmp_path, lane, monkeypatch):
    path = lane / "service.env"
    path.write_bytes(b"a placeholder, not a credential\n")
    return path, _sha(path.read_bytes()), "env-named rule"


def _scratch_symlink(tmp_path, lane, monkeypatch):
    link = lane / "evidence.txt"
    link.symlink_to(_NEVER_CREATED)
    return link, "0" * 64, "scratch-path rule"


def _plugin_cache(tmp_path, lane, monkeypatch):
    home = tmp_path / "home"
    cached = home / ".claude" / "plugins" / "cache" / "a-plugin" / "1.0.0" / "SKILL.md"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"a skill\n")
    monkeypatch.setenv("HOME", str(home))
    return cached, _sha(cached.read_bytes()), "plugin-cache rule"


@pytest.mark.parametrize(
    "make",
    [_env_named, _scratch_symlink, _plugin_cache],
    ids=["env-named", "scratch-symlink", "plugin-cache"],
)
def test_a_verified_receipt_the_rules_refuse_today_is_unreadable_and_never_read(
    make, tmp_path, lane, monkeypatch
):
    """The env-named and plugin-cache files hold the very bytes their receipts
    name, so a re-hash would say ok: the place decides, not the bytes. The
    scratch-path link dangles, which a re-hash would call missing."""
    path, sha256, rule = make(tmp_path, lane, monkeypatch)

    def _no_read(*_args, **_kwargs):
        raise AssertionError("a ref the rules refuse was read from disk")

    for name in ("classify_file_target", "hash_file_bounded", "file_mtime_bounded"):
        monkeypatch.setattr(seasons, name, _no_read)

    [row] = seasons.reverify_file_receipts(
        [_verified_row_entry("2026-01-01T00:00:00+00:00", path, sha256)]
    )
    assert row["status"] == "unreadable"
    assert row["ref"] == str(path)
    assert f"refused by the {rule}" in row["refused"]


def test_season_review_names_the_rule_of_a_refused_ref_and_counts_it_unreadable(chronicle, lane):
    env_file = lane / "service.env"
    env_file.write_bytes(b"a placeholder\n")
    entry = _verified_row_entry("2026-01-01T00:00:00+00:00", env_file, _sha(b"a placeholder\n"))
    shard = Path(chronicle.root) / "insights" / "receipts" / "session_legacy.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text(json.dumps(entry) + "\n")

    report = seasons.season_review(chronicle_root=chronicle.root)

    assert (
        f"file:{env_file} re-verifies as unreadable"
        " (refused by the env-named rule; the file was not read)"
    ) in report
    assert (
        "file receipts re-verified: 1 (0 ok, 0 moved_on, 0 never_verified, 0 mismatch,"
        " 0 missing, 1 unreadable)"
    ) in report
