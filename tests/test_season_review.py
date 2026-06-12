"""
Tests for v1.7.0 season_review (seasons.py).

Spec section 6: cross-domain marker detection finds the
DEFINITIVE/CORRECTED fixture pair; ready-to-paste call syntax;
sentinel-budget stat; read-only invariant (filesystem hash unchanged
after the call — D3's test, shipped not promised).

Hermetic — every chronicle lives under tmp_path; nothing touches
~/.sovereign live data.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from sovereign_stack.policies import PolicyRegistry
from sovereign_stack.provenance import (
    append_supersession,
    build_supersession_record,
    derive_claim_id,
    display_id,
)
from sovereign_stack.seasons import (
    SEASON_FOOTER,
    handle_season_tool,
    season_review,
)

# The children-exclusion-shaped pair: cross-domain, legacy marker on the
# newer entry, token-Jaccard 8/11 ≈ 0.73 >= 0.5.
PRED_CONTENT = "the children exclusion supersession pair spans the same domain dirs"
SUCC_CONTENT = "CORRECTED: the children exclusion supersession pair spans different domain dirs"

POLICY_CONTENT = "Never expand the iMessage allowlist without explicit human approval."

Q1 = "Does the jetson mirror verify rsync checksums after each pull?"
Q2 = "Does the jetson mirror verify checksums on pull?"
Q3 = "Who rotates the cloudflare tunnel credential file?"


# ── Fixture builders (live-shaped chronicle, no ExperientialMemory) ──


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def write_entry(root, domain, content, *, days_ago=1, intensity=0.5, layer="hypothesis", **extra):
    entry = {
        "timestamp": _iso(days_ago),
        "domain": domain,
        "content": content,
        "intensity": intensity,
        "session_id": "session_20260601_000000",
        "layer": layer,
    }
    entry.update(extra)
    domain_dir = root / "insights" / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    with open(domain_dir / "session_20260601_000000.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def write_thread(root, question, *, domain="general", days_ago=40):
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:8]
    thread = {
        "timestamp": _iso(days_ago),
        "thread_id": f"thread_20260101_000000_{digest}",
        "question": question,
        "context": "",
        "domain": domain,
        "session_id": "s",
        "layer": "open_thread",
        "resolved": False,
    }
    threads_dir = root / "open_threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    with open(threads_dir / f"{domain}.jsonl", "a") as f:
        f.write(json.dumps(thread) + "\n")
    return thread


def hash_tree(root):
    """sha256 of every file under root, keyed by relative path."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def season_root(tmp_path):
    """A live-shaped chronicle exercising all five report sections."""
    root = tmp_path / "chronicle"

    # 1. Supersession candidate: cross-domain marker pair.
    write_entry(root, "memory-architecture", PRED_CONTENT, days_ago=10)
    write_entry(root, "spiral-state", SUCC_CONTENT, days_ago=1, intensity=0.7)

    # 3. Policy candidate: policy-shaped ground_truth sentinel, unregistered.
    write_entry(
        root, "governance", POLICY_CONTENT, days_ago=2, intensity=0.92, layer="ground_truth"
    )

    # 4. Pin-loss: a >=0.9 sentinel superseded by a 0.5 successor.
    pin_pred = write_entry(
        root,
        "edge",
        "the orin swap file lives on nvme at slot two",
        days_ago=30,
        intensity=0.95,
        layer="ground_truth",
    )
    pin_succ = write_entry(
        root, "edge", "orin swap relocated to the sd card overflow slot", days_ago=3
    )
    ledger = root / "supersessions.jsonl"
    append_supersession(
        ledger,
        build_supersession_record(
            action="supersede",
            superseded_id=derive_claim_id(pin_pred),
            successor_id=derive_claim_id(pin_succ),
            carry_forward_summary="swap moved to sd",
            predecessor=pin_pred,
        ),
    )
    # 4. Dangling pointer: predecessor id that derives from no entry.
    append_supersession(
        ledger,
        build_supersession_record(
            action="supersede",
            superseded_id="ab" * 32,
            successor_id=derive_claim_id(pin_succ),
            carry_forward_summary="ghost predecessor",
        ),
    )

    # 4. Receipt re-verify failure: stamped verified, bytes changed since.
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("the bytes have changed since the stamp\n")
    write_entry(
        root,
        "receipts",
        "the artifact hash was checked at write time",
        days_ago=4,
        verified_by=[
            {
                "kind": "file",
                "ref": str(artifact),
                "sha256": "0" * 64,
                "checked_at_write": "verified",
            }
        ],
    )

    # 5. Dormant domain: nothing recorded for 200 days.
    write_entry(root, "ancient-lore", "the first tunnel config used quick tunnels", days_ago=200)

    # 2. Thread-family candidate cluster + one unrelated loner.
    write_thread(root, Q1)
    write_thread(root, Q2)
    write_thread(root, Q3)

    return root


def review(root, **kwargs):
    return season_review(chronicle_root=root, **kwargs)


# ── Section 1: supersession candidates ──


class TestSupersessionCandidates:
    def test_cross_domain_marker_pair_found(self, season_root):
        report = review(season_root)
        # claim_id derives from timestamp too — recompute from the written lines.
        pred_line = (
            season_root / "insights" / "memory-architecture" / "session_20260601_000000.jsonl"
        ).read_text()
        succ_line = (
            season_root / "insights" / "spiral-state" / "session_20260601_000000.jsonl"
        ).read_text()
        pred_id = display_id(derive_claim_id(json.loads(pred_line)))
        succ_id = display_id(derive_claim_id(json.loads(succ_line)))

        assert "1. SUPERSESSION CANDIDATES" in report
        assert "CROSS-DOMAIN" in report
        assert pred_id in report
        assert succ_id in report
        # Ready-to-paste, link-existing, predecessor/successor in causal order.
        assert f'supersede_insight(predecessor_id="{pred_id}", successor_id="{succ_id}"' in report
        assert "carry_forward_summary=" in report

    def test_already_ledgered_pair_excluded(self, season_root):
        pred = json.loads(
            (
                season_root / "insights" / "memory-architecture" / "session_20260601_000000.jsonl"
            ).read_text()
        )
        succ = json.loads(
            (
                season_root / "insights" / "spiral-state" / "session_20260601_000000.jsonl"
            ).read_text()
        )
        append_supersession(
            season_root / "supersessions.jsonl",
            build_supersession_record(
                action="supersede",
                superseded_id=derive_claim_id(pred),
                successor_id=derive_claim_id(succ),
                carry_forward_summary="formalized",
                predecessor=pred,
            ),
        )
        report = review(season_root)
        assert "supersede_insight(predecessor_id=" not in report

    def test_max_candidates_caps_with_honest_overflow(self, tmp_path):
        root = tmp_path / "chronicle"
        write_entry(root, "a", "alpha beta gamma delta epsilon zeta old take", days_ago=9)
        write_entry(root, "b", "CORRECTED: alpha beta gamma delta epsilon zeta", days_ago=1)
        write_entry(root, "c", "eta theta iota kappa lambda mu first take", days_ago=9)
        write_entry(root, "d", "DEFINITIVE: eta theta iota kappa lambda mu", days_ago=1)
        report = review(root, max_candidates=1)
        assert report.count("supersede_insight(predecessor_id=") == 1
        assert "…and 1 more" in report


# ── Section 2: thread-family candidates ──


class TestThreadFamilyCandidates:
    def test_cluster_found_with_ready_to_paste_link_call(self, season_root):
        report = review(season_root)
        q1_id = "thread_20260101_000000_" + hashlib.sha1(Q1.encode()).hexdigest()[:8]
        q2_id = "thread_20260101_000000_" + hashlib.sha1(Q2.encode()).hexdigest()[:8]
        q3_id = "thread_20260101_000000_" + hashlib.sha1(Q3.encode()).hexdigest()[:8]

        assert "2. THREAD-FAMILY CANDIDATES" in report
        call = re.search(r'link_threads\(thread_ids=\[([^\]]+)\], label="([a-z0-9-]+)"\)', report)
        assert call, "expected a ready-to-paste link_threads call"
        assert q1_id in call.group(1)
        assert q2_id in call.group(1)
        assert q3_id not in call.group(1)

    def test_already_familied_threads_not_proposed(self, season_root):
        q1_id = "thread_20260101_000000_" + hashlib.sha1(Q1.encode()).hexdigest()[:8]
        q2_id = "thread_20260101_000000_" + hashlib.sha1(Q2.encode()).hexdigest()[:8]
        (season_root / "thread_families.jsonl").write_text(
            json.dumps(
                {
                    "action": "link",
                    "timestamp": _iso(0),
                    "family_id": "fam_20260601_000000_deadbeef",
                    "label": "jetson-mirror",
                    "member_thread_ids": [q1_id, q2_id],
                    "primary_thread_id": None,
                    "note": "",
                    "by": "",
                }
            )
            + "\n"
        )
        report = review(season_root)
        assert "link_threads(thread_ids=[" not in report


# ── Section 3: policy candidates ──


class TestPolicyCandidates:
    def test_policy_shaped_sentinel_proposed(self, season_root):
        report = review(season_root)
        assert "3. POLICY CANDIDATES" in report
        assert "set_policy(statement=" in report
        assert "Never expand the iMessage allowlist" in report
        assert 'set_by="<approving human>"' in report

    def test_registered_policy_not_proposed(self, season_root, tmp_path):
        PolicyRegistry(tmp_path / "policies" / "policies.jsonl").set_policy(
            statement=POLICY_CONTENT, domain="governance", set_by="anthony"
        )
        report = review(season_root)
        assert "set_policy(statement=" not in report

    def test_low_intensity_normative_text_not_a_candidate(self, tmp_path):
        root = tmp_path / "chronicle"
        write_entry(root, "style", "We should never rush the boot ritual.", intensity=0.5)
        report = review(root)
        assert "set_policy(statement=" not in report


# ── Section 4: hygiene ──


class TestHygiene:
    def test_dangling_pointer_named(self, season_root):
        report = review(season_root)
        assert "4. HYGIENE" in report
        assert f"dangling predecessor {('ab' * 32)[:16]}" in report

    def test_receipt_reverify_failure_surfaced(self, season_root):
        report = review(season_root)
        assert "re-verifies as mismatch" in report

    def test_unreceipted_sentinels_counted(self, season_root):
        report = review(season_root)
        assert "2 unreceipted ground_truth sentinel(s) at >=0.9" in report

    def test_pin_loss_warning(self, season_root):
        report = review(season_root)
        assert "pin loss: sentinel" in report
        assert "will not pin at boot" in report

    def test_sentinel_budget_vs_boot_slots(self, season_root):
        report = review(season_root)
        assert re.search(
            r"sentinel budget: 2 marker\(s\) at >=0\.9 competing for 5 boot slots", report
        )


# ── Section 5: dormancy / fragmentation ──


class TestStats:
    def test_dormant_domain_listed(self, season_root):
        report = review(season_root)
        assert "5. DORMANT DOMAINS / FRAGMENTATION" in report
        assert "dormant: [ancient-lore]" in report

    def test_fragmentation_stats_present(self, season_root):
        report = review(season_root)
        assert re.search(r"fragmentation: \d+ entries across \d+ domain\(s\)", report)
        assert re.search(r"threads: 3 open", report)


# ── Scoping ──


class TestScoping:
    def test_domain_filter_scopes_candidate_sections(self, season_root):
        report = review(season_root, domain="spiral-state")
        # Marker entry lives in spiral-state: the pair survives the filter.
        assert "supersede_insight(predecessor_id=" in report
        # Policy sentinel (governance) and threads (general) are filtered out.
        assert "set_policy(statement=" not in report
        assert "link_threads(thread_ids=[" not in report

    def test_window_excludes_old_markers(self, season_root):
        report = review(season_root, window_days=0)
        assert "supersede_insight(predecessor_id=" not in report

    def test_all_five_sections_always_render(self, tmp_path):
        report = review(tmp_path / "empty-chronicle")
        for header in (
            "1. SUPERSESSION CANDIDATES",
            "2. THREAD-FAMILY CANDIDATES",
            "3. POLICY CANDIDATES",
            "4. HYGIENE",
            "5. DORMANT DOMAINS / FRAGMENTATION",
        ):
            assert header in report


# ── Read-only invariant + footer ──


class TestReadOnly:
    def test_filesystem_hash_unchanged(self, season_root, tmp_path):
        """The shipped invariant: sha256 every file before and after."""
        before = hash_tree(tmp_path)
        review(season_root)
        assert hash_tree(tmp_path) == before

    def test_empty_chronicle_creates_nothing(self, tmp_path):
        root = tmp_path / "chronicle"
        review(root)
        assert not root.exists()
        assert hash_tree(tmp_path) == {}

    def test_footer_verbatim(self, season_root):
        report = review(season_root)
        assert "This pass changed nothing." in report
        assert report.rstrip().endswith(SEASON_FOOTER)


# ── Dispatcher ──


class TestDispatcher:
    def test_handle_season_review(self, season_root, tmp_path):
        before = hash_tree(tmp_path)
        report = handle_season_tool(
            "season_review", {"window_days": 90, "max_candidates": 5}, season_root
        )
        assert "SEASON REVIEW" in report
        assert SEASON_FOOTER in report
        assert hash_tree(tmp_path) == before
