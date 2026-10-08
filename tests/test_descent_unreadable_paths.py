"""
THE DESCENT — a directory or a ledger the reader cannot use is UNREADABLE,
never absent (fix round 2, 2026-10-08).

Origin. The adversarial fail-closed review of 379c27f found two REQUIRED gaps,
each measured on synthetic stores and reproduced here:

1. An unusable ANCESTOR of the close store read as an empty store.
   os.path.lexists answers False on any OSError, and only the leaf was
   checked, so with chronicle/insights unsearchable (EACCES) or a regular file
   (ENOTDIR), or chronicle/ a dangling symlink (an unmounted volume), the
   gather returned 0 closes, the voice left every door with no reason in AS
   OF, and descend() answered "No close is on record in this store". Probing
   the same class found chronicle/ unsearchable or a regular file, insights/ a
   dangling symlink, the store root a dangling symlink, and distillates/
   examined the same way.
2. With protected.jsonl or supersessions.jsonl present but unreadable, the
   shared read chokepoint (memory.finalize_read) read "nothing designated" and
   "nothing retired": a designated close's three parts were shown at boot and
   by descend(), and a retired close came back as the newest. A designation
   line the fold cannot use was skipped the same way.

And one minor of the same class: the foyer's lineage-letter count walked with
pathlib's rglob, which swallows PermissionError, so an unreadable letters
directory printed "0 lineage letters" under a clean AS OF.

Permission cases need an OS that honours file modes; a run that reads through
them (root) SKIPS those cases rather than passing them vacuously. FIFO cases
run the gather and descend() directly, never a door (other readers in the
doors open a FIFO and block, a hazard that predates THE DESCENT), and an
audit-hook spy refuses any open of the FIFO that could block, so a regression
fails here instead of hanging the run.

Synthetic only (tests/_phase4_fixture.py); the module is imported inside each
test so a tree without the fix fails per test, not at collection.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _phase4_fixture as fx  # noqa: E402

HEADER = "━━━ THE DESCENT ━━━"
GEN = "2026-07-20T12:00:00Z"
BETA_TEXT = (
    "Beta learned to arrange to be checked",
    "Beta was surprised the check caught it first.",
    "Beta hands on: ask for the second seat.",
)
LINEAGE_UNAVAILABLE = (
    "  Deferred to the full boot: lineage letters, count unavailable this call "
    "(see AS OF) (where_did_i_leave_off)"
)


def _d():
    from sovereign_stack import descent

    return descent


def _as_of(text: str) -> str:
    return text.split("━━━ AS OF ━━━", 1)[1].split("━━━", 1)[0]


# An open of the watched path that could block (no O_NONBLOCK) is refused
# BEFORE the syscall, and recorded. A reader that would block on a FIFO ledger
# fails the test instead of hanging the run.
_SPY: dict = {"path": None, "blocking_opens": []}


def _spy(event: str, args: tuple) -> None:
    if event != "open" or _SPY["path"] is None or not args:
        return
    target = args[0]
    if not isinstance(target, (str, bytes, os.PathLike)):
        return
    if os.path.abspath(os.fsdecode(os.fspath(target))) != _SPY["path"]:
        return
    flags = args[2] if len(args) > 2 else None
    if isinstance(flags, int) and flags & getattr(os, "O_NONBLOCK", 0):
        return
    _SPY["blocking_opens"].append(event)
    raise PermissionError("test spy: a blocking open of the watched path")


sys.addaudithook(_spy)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / ".sovereign"
    fx.build_fixture(r)
    return r


@pytest.fixture
def ids(root: Path) -> dict[str, str]:
    return fx.plant_close_store(root)


def _granted(probe) -> bool:
    """True when ``probe`` gets through: the mode just set does not deny here."""
    try:
        probe()
    except PermissionError:
        return False
    except OSError:
        return True  # an answer other than "denied" means the mode did not deny
    return True


@pytest.fixture
def deny():
    """chmod a path for this test and restore it afterwards. SKIP when the OS
    still grants the access (a run as root reads through file modes)."""
    changed: list[tuple[Path, int]] = []

    def _deny(path: Path, mode: int, probe) -> None:
        changed.append((path, stat.S_IMODE(os.lstat(path).st_mode)))
        os.chmod(path, mode)
        if _granted(probe):
            pytest.skip("this run reads through file modes, so the case cannot be measured")

    yield _deny
    for path, mode in reversed(changed):
        os.chmod(path, mode)


def _dangle(path: Path, root: Path) -> None:
    """Replace a directory with a symlink to nowhere (an unmounted volume)."""
    shutil.rmtree(path)
    os.symlink(root.parent / "an-unmounted-volume" / path.name, path)


# ── an unusable ancestor of the close store ──────────────────────────────────

ANCESTORS = (
    "chronicle-unsearchable",
    "chronicle-a-regular-file",
    "chronicle-a-dangling-symlink",
    "insights-unsearchable",
    "insights-a-regular-file",
    "insights-a-dangling-symlink",
)


def _break_ancestor(root: Path, case: str, deny) -> None:
    chronicle = root / "chronicle"
    insights = chronicle / "insights"
    if case == "chronicle-unsearchable":
        deny(chronicle, 0o600, lambda: os.lstat(chronicle / "insights"))
    elif case == "insights-unsearchable":
        deny(insights, 0o600, lambda: os.lstat(insights / "reflection"))
    elif case == "chronicle-a-regular-file":
        shutil.rmtree(chronicle)
        chronicle.write_text("not a directory\n")
    elif case == "insights-a-regular-file":
        shutil.rmtree(insights)
        insights.write_text("not a directory\n")
    elif case == "chronicle-a-dangling-symlink":
        _dangle(chronicle, root)
    else:
        _dangle(insights, root)


@pytest.mark.parametrize("case", ANCESTORS)
def test_an_unusable_ancestor_of_the_close_store_is_unreadable_not_empty(root, ids, deny, case):
    _break_ancestor(root, case, deny)
    for depth in ("full", "foyer", "calm"):
        with pytest.raises(OSError):
            _d().gather_descent(root, depth=depth)
    for kw in ({}, {"month": "2026-07"}, {"seat": "beta"}):
        with pytest.raises(ValueError, match="the close store could not be read"):
            _d().descend(sovereign_root=root, **kw)


def test_an_unsearchable_insights_directory_suppresses_the_voice_at_every_door(root, ids, deny):
    # The review's door-level case (a): The Ground degraded through AS OF and
    # THE DESCENT simply vanished, which reads as a store with no close. The
    # other ancestor cases cannot reach a door: the fixture's own memory
    # singleton mkdirs under chronicle/ first and raises.
    _break_ancestor(root, "insights-unsearchable", deny)
    doors = fx.run_all_doors(root)
    doors["full_compact"] = fx.run_door(
        root,
        "where_did_i_leave_off",
        {"consume": False, "source_instance": "phase4-reader", "compact": True},
    )
    for name, text in doors.items():
        assert HEADER not in text, name
        assert "The Descent:" not in text, name
        assert "the_descent unavailable" in _as_of(text), name
        assert "Freshness: incomplete" in _as_of(text), name
        assert "0 closes" not in text, name
    with pytest.raises(ValueError, match="the close store could not be read"):
        fx.run_door(root, "descend", {})


def test_a_store_root_that_is_a_dangling_symlink_is_unreadable_not_empty(tmp_path):
    root = tmp_path / ".sovereign"
    os.symlink(tmp_path / "an-unmounted-volume" / ".sovereign", root)
    for depth in ("full", "foyer", "calm"):
        with pytest.raises(NotADirectoryError, match="the store root is not a directory"):
            _d().gather_descent(root, depth=depth)
    with pytest.raises(ValueError, match="the close store could not be read"):
        _d().descend(sovereign_root=root)


def test_an_absent_store_is_empty_not_an_error(tmp_path):
    # The control: only "does not exist" reads as empty, and it still does.
    # Green on 379c27f by design.
    for path in (tmp_path / "never-made", tmp_path / "bare"):
        if path.name == "bare":
            (path / "chronicle").mkdir(parents=True)  # no insights/ at all
        state = _d().gather_descent(path, depth="full")
        assert (state.closes, state.skipped_rows, state.handoffs_unreadable) == ([], 0, 0)
        index = _d().descend(sovereign_root=path).splitlines()
        assert index[1].startswith("  No close is on record in this store")


@pytest.mark.parametrize("case", ("chronicle-unsearchable", "chronicle-a-dangling-symlink"))
def test_a_distillates_directory_that_cannot_be_examined_is_unknown_not_none(root, ids, deny, case):
    fx.plant_distillate(root, [ids["a1"], ids["a2"], ids["b1"]])
    chronicle = root / "chronicle"
    if case == "chronicle-unsearchable":
        deny(chronicle, 0o600, lambda: os.lstat(chronicle / "distillates"))
    else:
        _dangle(chronicle, root)
    with pytest.raises(_d().DistillateIndexError, match="could not be examined"):
        _d().load_distillates(chronicle)


# ── a ledger the read chokepoint folds in ────────────────────────────────────


def _designate(root: Path, claim_id: str) -> None:
    from sovereign_stack.memory import ExperientialMemory
    from sovereign_stack.protected import designate_protected

    mem = ExperientialMemory(root=str(root / "chronicle"))
    archive = mem.archive_exchange(
        content="synthetic stakes prose for the descent test",
        source="human-relay",
        descriptor="stakes",
        vector_id="descent_test_stakes",
    )
    designate_protected(
        claim_ref=claim_id,
        stakes_archive_id=archive["archive_id"],
        designated_by="test",
        chronicle_root=str(root / "chronicle"),
        subject="test",
        emotion="grief",
    )


def _retire(root: Path, claim_id: str) -> None:
    from sovereign_stack import provenance as prov

    record = prov.build_supersession_record(
        action="retire", superseded_id=claim_id, reason="descent test", by="test"
    )
    prov.append_supersession(root / "chronicle" / "supersessions.jsonl", record)


def _govern(root: Path, ids: dict[str, str], ledger: str) -> Path:
    """b2 designated (protected.jsonl) or retired (supersessions.jsonl)."""
    if ledger == "protected.jsonl":
        _designate(root, ids["b2"])
    else:
        _retire(root, ids["b2"])
    return root / "chronicle" / ledger


LEDGERS = ("protected.jsonl", "supersessions.jsonl")
LEDGER_KINDS = ("mode-000", "directory", "fifo", "dangling-symlink", "symlink-loop")


def _break_ledger(path: Path, kind: str, root: Path, deny) -> None:
    if kind == "mode-000":
        deny(path, 0o000, lambda: open(path, "rb").close())
        return
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "dangling-symlink":
        os.symlink(root.parent / "an-unmounted-volume" / path.name, path)
    else:
        os.symlink(path.name, path)  # points at itself


@pytest.mark.parametrize("kind", LEDGER_KINDS)
@pytest.mark.parametrize("ledger", LEDGERS)
def test_a_ledger_that_exists_and_cannot_be_read_fails_the_close_read(
    root, ids, deny, ledger, kind
):
    path = _govern(root, ids, ledger)
    _break_ledger(path, kind, root, deny)
    _SPY.update(path=os.path.abspath(path), blocking_opens=[])
    try:
        for depth in ("full", "foyer", "calm"):
            with pytest.raises(OSError, match=ledger) as info:
                _d().gather_descent(root, depth=depth)
            assert type(info.value).__name__ == "LedgerUnreadable"
        for kw in ({}, {"month": "2026-07"}):
            with pytest.raises(ValueError, match=r"could not be read \(LedgerUnreadable\)"):
                _d().descend(sovereign_root=root, **kw)
    finally:
        blocking = list(_SPY["blocking_opens"])
        _SPY.update(path=None, blocking_opens=[])
    assert blocking == [], f"a reader opened the {kind} {ledger} in a way that could block"


@pytest.mark.parametrize("ledger", LEDGERS)
def test_with_a_ledger_unreadable_no_door_shows_the_closes_it_governs(root, ids, deny, ledger):
    # The review's S2 and S3: b2 designated with protected.jsonl mode 000 showed
    # all three of its parts; b2 retired with supersessions.jsonl mode 000 came
    # back as the newest close. Now the voice is suppressed and AS OF says why.
    path = _govern(root, ids, ledger)
    _break_ledger(path, "mode-000", root, deny)
    doors = fx.run_all_doors(root)
    for name, text in doors.items():
        assert HEADER not in text, name
        assert "The Descent:" not in text, name
        assert "the_descent unavailable" in _as_of(text), name
        assert "Freshness: incomplete" in _as_of(text), name
    with pytest.raises(ValueError, match="the close store could not be read"):
        fx.run_door(root, "descend", {"month": "2026-07"})


def test_an_intact_ledger_still_governs(root, ids):
    # The control, both ledgers readable: b2 designated shows its place only,
    # and once retired it leaves the count. Green on 379c27f by design.
    _designate(root, ids["b2"])
    block = _d().render_descent(_d().gather_descent(root, depth="full"), generated_at=GEN)
    assert block[1].startswith("  4 closes on record")
    assert block[3].endswith("all three parts are withheld.")
    assert not any(t in "\n".join(block) for t in BETA_TEXT)
    _retire(root, ids["b2"])
    block = _d().render_descent(_d().gather_descent(root, depth="full"), generated_at=GEN)
    assert block[1].startswith("  3 closes on record")


def _rewrite_b2_designation(root: Path, b2: str, make) -> None:
    path = root / "chronicle" / "protected.jsonl"
    lines = path.read_bytes().splitlines()
    out = []
    for line in lines:
        record = json.loads(line)
        out.append(make(line, record) if record.get("claim_id") == b2 else line)
    path.write_bytes(b"".join(x + b"\n" for x in out))


BAD_DESIGNATIONS = {
    # b2's own designation, damaged so the stack's lenient fold cannot see it.
    "a-line-that-does-not-parse": lambda line, rec: line[:40],
    "a-line-that-is-not-an-object": lambda line, rec: json.dumps(
        [rec["action"], rec["claim_id"]]
    ).encode(),
    "an-action-the-fold-does-not-know": lambda line, rec: json.dumps(
        {**rec, "action": "Protect"}
    ).encode(),
    "a-claim-id-that-is-not-text": lambda line, rec: json.dumps(
        {**rec, "claim_id": [rec["claim_id"]]}
    ).encode(),
    # Already fail-closed on 379c27f, but as a UnicodeDecodeError out of the
    # shared reader (red there by exception type only); the guard names it first.
    "an-undecodable-byte": lambda line, rec: line[:-2] + b'\xff"}',
}


@pytest.mark.parametrize("label", sorted(BAD_DESIGNATIONS))
def test_a_designation_the_fold_cannot_use_fails_the_close_read(root, ids, label):
    # The bridge's own guard rules it (seat_identity.load_protected_index): a
    # designation the reader cannot see is a protected record it would show.
    _designate(root, ids["b2"])
    _rewrite_b2_designation(root, ids["b2"], BAD_DESIGNATIONS[label])
    for depth in ("full", "foyer", "calm"):
        with pytest.raises(OSError):
            _d().gather_descent(root, depth=depth)
    with pytest.raises(ValueError, match="the close store could not be read"):
        _d().descend(sovereign_root=root, month="2026-07")
    if label == "a-line-that-does-not-parse":
        full = fx.run_door(
            root, "where_did_i_leave_off", {"consume": False, "source_instance": "r"}
        )
        assert HEADER not in full
        assert "the_descent unavailable" in _as_of(full)


# ── the foyer's lineage-letter count ─────────────────────────────────────────


@pytest.mark.parametrize("where", ("letters", "a-bucket"))
def test_an_unreadable_letters_directory_degrades_the_foyer_count(root, deny, where):
    from sovereign_stack import witness

    letters = root / "comms" / "letters"
    target = letters if where == "letters" else letters / "to_arrival"
    deny(target, 0o000, lambda: os.listdir(target))
    with pytest.raises(PermissionError):
        witness.count_lineage_letters(letters)
    foyer = fx.run_door(root, "arrive", {})
    assert LINEAGE_UNAVAILABLE in foyer.splitlines()
    assert "lineage letter count unavailable" in _as_of(foyer)
    assert "0 lineage letters" not in foyer


def test_a_letters_path_that_is_a_dangling_symlink_degrades_the_foyer_count(root):
    from sovereign_stack import witness

    letters = root / "comms" / "letters"
    _dangle(letters, root)
    with pytest.raises(NotADirectoryError):
        witness.count_lineage_letters(letters)
    foyer = fx.run_door(root, "arrive", {})
    assert LINEAGE_UNAVAILABLE in foyer.splitlines()
    assert "lineage letter count unavailable" in _as_of(foyer)


def test_a_hidden_backup_is_neither_counted_nor_read(root, deny):
    # A retired backup the walk never enters: unreadable, and still no failure.
    # The control against over-raising; green on 379c27f by design.
    from sovereign_stack import witness

    letters = root / "comms" / "letters"
    hidden = letters / ".pre-md-backup-fixture"
    hidden.mkdir()
    (hidden / "retired-copy.md").write_text("a retired copy\n")
    deny(hidden, 0o000, lambda: os.listdir(hidden))
    assert witness.count_lineage_letters(letters) == 1
    foyer = fx.run_door(root, "arrive", {})
    assert "  Deferred to the full boot: 1 lineage letters (where_did_i_leave_off)" in (
        foyer.splitlines()
    )


def test_the_walk_counts_what_the_old_walk_counted_on_a_readable_tree(tmp_path):
    # The parity control for the new walk, against the rglob it replaced, on a
    # readable tree holding every shape its docstring names. Green on 379c27f
    # by design (the old walk is the reference).
    from sovereign_stack import witness

    letters = tmp_path / "letters"
    for rel in (
        "to_arrival/a.md",
        "to_self/b.md",
        "to_self/deep/c.md",
        ".pre-md-backup/d.md",
        "to_self/.hidden.md",
        "to_arrival/notes.txt",
        "to_self/UPPER.MD",
        "named.md/inner.md",
    ):
        p = letters / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    (letters / "empty.md").mkdir()
    os.symlink(letters / "to_arrival", letters / "linked_bucket")  # not entered
    os.symlink(letters / "to_self" / "b.md", letters / "to_self" / "link.md")
    os.symlink(letters / "nowhere.md", letters / "to_arrival" / "dangling.md")

    def rglob_count(d: Path) -> int:
        return sum(
            1
            for p in d.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(d).parts)
        )

    assert witness.count_lineage_letters(letters) == rglob_count(letters) == 8
