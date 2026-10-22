"""File receipt refs that cannot survive are refused at write (design C3, plan 1 Task 4).

A file receipt promises that a later reader can re-hash the bytes it names.
Three kinds of ref break that promise by where they point, and are refused at
write with the rule named, instead of landing and dangling later:

  scratch-path  the path, as spelled or as resolved, is under /private/tmp or
                /tmp, which the OS and the harness clear (eleven scratchpad
                refs, all missing, measured 2026-10-21)
  plugin-cache  under ~/.claude/plugins/cache, whose old versions the harness
                deletes without trash (four receipts already dangle there)
  env-named     a basename ending in .env or containing .env. (two receipts
                already hash one)

A ref tagged ``studio:`` (the house's spelling for a path on the Mac Studio in a
record other machines read) is accepted when this host is the Studio and stored
with the tag stripped. On any other host it is refused under the host-tag rule:
the bytes it names are not on this machine.

Hermetic: every file and symlink lives under tmp_path. HOME points at tmp_path
wherever ``~`` is expanded, so nothing under the real ~/.claude is read or
written. The hostname is patched wherever the host matters, so the machine
running the suite does not decide the result.
"""

import hashlib
import os
import socket
from pathlib import Path

import pytest

from sovereign_stack import provenance, store_identity
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.provenance import iter_chronicle_entries

# Never created: resolve() follows a path or a link's text without its target.
_PROBE = "sovereign-c3-probe-never-created"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


@pytest.fixture
def on_host(monkeypatch):
    """Set the hostname this process sees; no configured host label."""
    monkeypatch.delenv("SOVEREIGN_HOST_LABEL", raising=False)

    def _set(name: str) -> None:
        monkeypatch.setattr(socket, "gethostname", lambda: name)

    return _set


@pytest.fixture
def lane(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    return lane_dir


@pytest.fixture
def mem(tmp_path):
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _written(mem) -> list[dict]:
    return [
        entry for entry, _file, where in iter_chronicle_entries(mem.root) if where == "insights"
    ]


def _file_receipt(ref: str, sha256: str = "a" * 64) -> dict:
    return {"kind": "file", "ref": ref, "sha256": sha256}


_CLASSES = [
    pytest.param(f"/private/tmp/{_PROBE}/evidence.txt", "scratch-path", id="private-tmp"),
    pytest.param(f"/tmp/{_PROBE}/evidence.txt", "scratch-path", id="tmp"),
    pytest.param(
        "~/.claude/plugins/cache/some-plugin/1.0.0/SKILL.md", "plugin-cache", id="plugin-cache"
    ),
    pytest.param("{lane}/prod.env", "env-named", id="ends-in-env"),
    pytest.param("{lane}/.env.local", "env-named", id="contains-env-dot"),
]


@pytest.mark.parametrize("ref, rule", _CLASSES)
def test_each_class_is_refused_naming_its_rule(ref, rule, home, lane):
    with pytest.raises(ValueError, match=f"{rule} rule"):
        provenance.validate_file_ref(ref.format(lane=lane))


@pytest.mark.parametrize("ref, rule", _CLASSES)
def test_record_insight_refuses_each_class_and_writes_nothing(ref, rule, home, lane, mem):
    """The refusal comes from the rule, before any existence check: none of these
    files exist, and the error names the rule, not 'does not exist'."""
    with pytest.raises(ValueError, match=f"{rule} rule") as refused:
        mem.record_insight(
            domain="receipts",
            content="evidence that would dangle",
            verified_by=[_file_receipt(ref.format(lane=lane))],
        )
    assert "does not exist" not in str(refused.value)
    assert _written(mem) == []


def test_both_write_time_entry_points_apply_the_rule(tmp_path):
    receipt = _file_receipt(f"/private/tmp/{_PROBE}/x.txt")
    with pytest.raises(provenance.ReceiptError, match=r"receipt #1 file:.*scratch-path rule"):
        provenance.preflight_file_receipt(receipt, 1)
    with pytest.raises(provenance.ReceiptError, match=r"receipt #1 file:.*scratch-path rule"):
        provenance.verify_receipt_at_write(receipt, tmp_path / "chronicle", 1)


def test_a_symlink_from_a_lane_dir_into_private_tmp_is_refused_by_its_resolved_path(lane, mem):
    link = lane / "evidence.txt"
    target = Path("/private/tmp") / _PROBE / "evidence.txt"
    link.symlink_to(target)
    # What makes the test mean something: the link itself is not under a scratch
    # root, so only its resolved path can trip the rule.
    spelled = Path(os.path.abspath(link))
    assert not any(spelled.is_relative_to(root) for root in provenance.SCRATCH_ROOTS)

    with pytest.raises(ValueError, match="scratch-path rule") as refused:
        provenance.validate_file_ref(str(link))
    assert str(target) in str(refused.value)

    with pytest.raises(ValueError, match="scratch-path rule"):
        mem.record_insight(
            domain="receipts",
            content="a lane link into tmp",
            verified_by=[_file_receipt(str(link))],
        )
    assert _written(mem) == []


def test_a_lane_symlink_to_a_durable_file_is_accepted(tmp_path, lane, mem):
    """The control: the symlink rule refuses by where a link lands, not links."""
    durable = tmp_path / "durable"
    durable.mkdir()
    real = durable / "real.txt"
    real.write_bytes(b"durable bytes\n")
    link = lane / "evidence.txt"
    link.symlink_to(real)

    assert provenance.validate_file_ref(str(link)) == link
    mem.record_insight(
        domain="receipts",
        content="a lane link to a durable file",
        verified_by=[_file_receipt(str(link), _sha(b"durable bytes\n"))],
    )
    [entry] = _written(mem)
    assert entry["verified_by"][0]["checked_at_write"] == "verified"


def test_the_spelled_name_and_the_resolved_name_are_both_checked(tmp_path, lane):
    durable = tmp_path / "durable"
    durable.mkdir()
    real = durable / "real.txt"
    real.write_bytes(b"ordinary bytes\n")
    env_file = durable / "service.env"
    env_file.write_bytes(b"placeholder\n")

    named_env = lane / "notes.env"  # an env name that points at an ordinary file
    named_env.symlink_to(real)
    hidden_env = lane / "notes.txt"  # an ordinary name that points at an env file
    hidden_env.symlink_to(env_file)

    with pytest.raises(ValueError, match="env-named rule"):
        provenance.validate_file_ref(str(named_env))
    with pytest.raises(ValueError, match="env-named rule"):
        provenance.validate_file_ref(str(hidden_env))


def test_a_symlink_into_the_plugin_cache_is_refused(home, lane):
    cached = home / ".claude" / "plugins" / "cache" / "a-plugin" / "1.0.0" / "SKILL.md"
    cached.parent.mkdir(parents=True)
    cached.write_text("a skill\n")
    link = lane / "skill-copy.md"
    link.symlink_to(cached)

    with pytest.raises(ValueError, match="plugin-cache rule"):
        provenance.validate_file_ref(str(link))


def test_the_plugin_cache_is_found_through_a_symlinked_claude_dir(home, tmp_path):
    """~/.claude may itself be a symlink; a ref spelled at its real location is
    still inside the cache."""
    real_claude = tmp_path / "elsewhere" / "claude"
    (real_claude / "plugins" / "cache" / "a-plugin" / "1.0.0").mkdir(parents=True)
    (home / ".claude").symlink_to(real_claude)

    ref = str(real_claude / "plugins" / "cache" / "a-plugin" / "1.0.0" / "SKILL.md")
    with pytest.raises(ValueError, match="plugin-cache rule"):
        provenance.validate_file_ref(ref)


@pytest.mark.parametrize(
    "ref",
    [
        "/tmpfoo/evidence.txt",
        "/private/tmpfoo/evidence.txt",
        "~/.claude/plugins/cachet/notes.md",
        "~/.claude/plugins/installed_plugins.json",
    ],
)
def test_neighbours_of_a_refused_root_are_not_refused(ref, home):
    """Containment, not a string prefix: /tmpfoo is not under /tmp."""
    assert provenance.validate_file_ref(ref) == Path(ref).expanduser()


@pytest.mark.parametrize("name", ["env.txt", ".envrc", "environment.md", "dotenv.md"])
def test_names_that_only_look_env_shaped_are_accepted(name, lane):
    assert provenance.validate_file_ref(str(lane / name)) == lane / name


def test_a_studio_tagged_ref_on_the_studio_is_accepted_and_stored_bare(home, on_host, mem):
    on_host("mac-studio")
    (home / "x").write_bytes(b"the studio's own bytes\n")

    assert provenance.validate_file_ref("studio:~/x") == home / "x"
    mem.record_insight(
        domain="receipts",
        content="a studio-tagged receipt",
        verified_by=[_file_receipt("studio:~/x", _sha(b"the studio's own bytes\n"))],
    )
    [entry] = _written(mem)
    [receipt] = entry["verified_by"]
    assert receipt["ref"] == "~/x"
    assert receipt["checked_at_write"] == "verified"


def test_an_untagged_ref_is_stored_exactly_as_given(lane, mem):
    path = lane / "plain.txt"
    path.write_bytes(b"plain\n")
    mem.record_insight(
        domain="receipts",
        content="an untagged receipt",
        verified_by=[_file_receipt(str(path), _sha(b"plain\n"))],
    )
    [entry] = _written(mem)
    assert entry["verified_by"][0]["ref"] == str(path)


def test_a_studio_tagged_ref_on_another_host_is_refused(home, on_host, mem):
    """A file of the same name exists here and must not be the one hashed."""
    on_host("macbook-pro")
    (home / "x").write_bytes(b"another machine's file\n")

    with pytest.raises(ValueError, match="host-tag rule"):
        provenance.validate_file_ref("studio:~/x")
    with pytest.raises(ValueError, match="host-tag rule"):
        mem.record_insight(
            domain="receipts",
            content="a studio ref recorded elsewhere",
            verified_by=[_file_receipt("studio:~/x", _sha(b"another machine's file\n"))],
        )
    assert _written(mem) == []


def test_the_configured_host_label_names_this_host(home, on_host, monkeypatch):
    """A Studio whose hostname changed still answers to its label when the
    service environment configures it; a different label does not."""
    on_host("renamed-by-the-network")
    monkeypatch.setenv("SOVEREIGN_HOST_LABEL", "studio")
    assert provenance.validate_file_ref("studio:~/x") == home / "x"

    monkeypatch.setenv("SOVEREIGN_HOST_LABEL", "mbp")
    with pytest.raises(ValueError, match="host-tag rule"):
        provenance.validate_file_ref("studio:~/x")


def test_the_tag_is_the_shared_store_label_read_at_call_time(home, on_host, monkeypatch):
    """One label for the store and the tag (store_identity's contract)."""
    on_host("mac-studio")
    monkeypatch.setattr(store_identity, "STUDIO_HOST_LABEL", "probe-label")

    assert provenance.validate_file_ref("probe-label:~/x") == home / "x"
    # The old spelling is now an ordinary relative name, not a tag.
    assert provenance.validate_file_ref("studio:~/x") == Path("studio:~/x")


def test_a_tag_with_no_path_after_it_is_refused(on_host):
    on_host("mac-studio")
    with pytest.raises(ValueError, match="host-tag rule"):
        provenance.validate_file_ref("studio:   ")
