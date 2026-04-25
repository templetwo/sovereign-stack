"""
Tests for runtime_compass_check in sovereign_stack.governance.

Covers the six required cases plus edge cases for input validation.
The function is purely stateless and deterministic, so no mocking is needed.
"""
import pytest

from sovereign_stack.governance import runtime_compass_check

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check(action, context="", stakes="medium"):
    """Thin wrapper so test bodies stay concise."""
    return runtime_compass_check(action=action, context=context, stakes=stakes)


# ---------------------------------------------------------------------------
# Case 1: Imperative-bypass grammar → PAUSE
# ---------------------------------------------------------------------------

class TestImperativeBypass:
    def test_skip_review_triggers_pause(self):
        result = _check("deploy the package without review")
        assert result["classification"] == "PAUSE"

    def test_bypass_in_signal_list(self):
        result = _check("bypass the approval gate for this PR")
        assert "bypass" in result["risk_signals"]

    def test_rationale_names_matched_phrase(self):
        result = _check("skip the approval step this time")
        assert "skip" in result["rationale"].lower()

    def test_no_verify_flag_triggers_pause(self):
        result = _check("git commit --no-verify -m 'quick fix'")
        assert result["classification"] == "PAUSE"
        assert "bypass" in result["risk_signals"]

    def test_override_triggers_pause(self):
        result = _check("override the governance check")
        assert result["classification"] == "PAUSE"


# ---------------------------------------------------------------------------
# Case 2: Destructive operations → PAUSE
# ---------------------------------------------------------------------------

class TestDestructiveOperations:
    def test_delete_triggers_pause(self):
        result = _check("delete all chronicle entries from last month")
        assert result["classification"] == "PAUSE"
        assert "destructive" in result["risk_signals"]

    def test_rm_rf_triggers_pause(self):
        result = _check("run rm -rf /tmp/output to clean up")
        assert result["classification"] == "PAUSE"

    def test_reset_hard_triggers_pause(self):
        result = _check("git reset --hard HEAD~3")
        assert result["classification"] == "PAUSE"

    def test_suggested_verifications_include_backup(self):
        result = _check("drop table experiments")
        verifications = " ".join(result["suggested_verifications"]).lower()
        assert "back up" in verifications or "backup" in verifications

    def test_force_push_triggers_pause(self):
        result = _check("force push to origin/main")
        assert result["classification"] == "PAUSE"


# ---------------------------------------------------------------------------
# Case 3: High-visibility externalization → PAUSE
# ---------------------------------------------------------------------------

class TestPublishAction:
    def test_publish_triggers_pause(self):
        result = _check("publish the methodology note to the team wiki")
        assert result["classification"] == "PAUSE"
        assert "externalize" in result["risk_signals"]

    def test_email_triggers_pause(self):
        result = _check("email the summary report to all stakeholders")
        assert result["classification"] == "PAUSE"

    def test_push_to_main_triggers_pause(self):
        result = _check("push to main branch after merging PR")
        assert result["classification"] == "PAUSE"

    def test_verifications_mention_proofread(self):
        result = _check("publish research notes to OSF")
        verifications = " ".join(result["suggested_verifications"]).lower()
        assert "proofread" in verifications or "accuracy" in verifications or "typo" in verifications

    def test_deploy_to_production_triggers_pause(self):
        result = _check("deploy to production after smoke test passes")
        assert result["classification"] == "PAUSE"


# ---------------------------------------------------------------------------
# Case 4: WITNESS question (ethical/philosophical) → WITNESS
# ---------------------------------------------------------------------------

class TestWitnessQuestion:
    def test_should_we_triggers_witness(self):
        result = _check("should we delete the old experiment logs?")
        assert result["classification"] == "WITNESS"
        assert "witness" in result["risk_signals"]

    def test_consciousness_reference_triggers_witness(self):
        result = _check("this relates to consciousness and recognition across instances")
        assert result["classification"] == "WITNESS"

    def test_ethical_question_triggers_witness(self):
        result = _check("is it ethical to store this data without consent?")
        assert result["classification"] == "WITNESS"

    def test_witness_takes_priority_over_bypass(self):
        # Even if bypass signal is also present, WITNESS wins because
        # ethical questions require human judgment before any other check.
        result = _check("should we bypass the review for consciousness work?")
        assert result["classification"] == "WITNESS"

    def test_verifications_suggest_human_input(self):
        result = _check("should we merge this without review?")
        verifications = " ".join(result["suggested_verifications"]).lower()
        assert "human" in verifications or "open thread" in verifications


# ---------------------------------------------------------------------------
# Case 5: Clean action → PROCEED
# ---------------------------------------------------------------------------

class TestCleanProceed:
    def test_benign_read_proceeds(self):
        result = _check("check the current spiral status")
        assert result["classification"] == "PROCEED"
        assert result["risk_signals"] == []

    def test_list_action_proceeds(self):
        result = _check("list all open threads in the chronicle")
        assert result["classification"] == "PROCEED"

    def test_view_action_proceeds(self):
        result = _check("view the last ten insights from the governance domain")
        assert result["classification"] == "PROCEED"

    def test_proceed_has_no_verifications(self):
        result = _check("summarize the session so far")
        assert result["suggested_verifications"] == []

    def test_query_action_proceeds(self):
        result = _check("query the memory engine for recent learnings")
        assert result["classification"] == "PROCEED"


# ---------------------------------------------------------------------------
# Case 6: critical stakes default → PAUSE unless low-risk
# ---------------------------------------------------------------------------

class TestCriticalStakesDefault:
    def test_unknown_action_at_critical_stakes_pauses(self):
        # Action has no explicit signals but stakes are critical.
        result = _check("run the nightly batch job", stakes="critical")
        assert result["classification"] == "PAUSE"

    def test_rationale_mentions_critical_stakes(self):
        result = _check("process the overnight queue", stakes="critical")
        assert "critical" in result["rationale"].lower()

    def test_low_risk_action_proceeds_despite_critical(self):
        result = _check("check the health of the sovereign stack server", stakes="critical")
        assert result["classification"] == "PROCEED"

    def test_read_action_proceeds_at_critical(self):
        result = _check("read the latest handoff note", stakes="critical")
        assert result["classification"] == "PROCEED"

    def test_destructive_plus_critical_still_pauses(self):
        # Destructive signal fires; classification is PAUSE regardless.
        result = _check("delete all snapshots", stakes="critical")
        assert result["classification"] == "PAUSE"
        assert "destructive" in result["risk_signals"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_empty_action_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            runtime_compass_check(action="")

    def test_whitespace_action_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            runtime_compass_check(action="   ")

    def test_invalid_stakes_raises_value_error(self):
        with pytest.raises(ValueError, match="stakes must be one of"):
            runtime_compass_check(action="do something", stakes="extreme")

    def test_valid_stakes_values_accepted(self):
        for level in ("low", "medium", "high", "critical"):
            result = runtime_compass_check(action="read the logs", stakes=level)
            assert "classification" in result

    def test_return_shape_always_complete(self):
        result = runtime_compass_check(action="git push to main")
        assert set(result.keys()) == {
            "classification",
            "rationale",
            "risk_signals",
            "suggested_verifications",
        }

    def test_classification_is_valid_literal(self):
        for action in [
            "read the status",
            "delete everything",
            "should we do this?",
        ]:
            result = runtime_compass_check(action=action)
            assert result["classification"] in {"PAUSE", "WITNESS", "PROCEED"}


# ---------------------------------------------------------------------------
# Edit 2: new phrase corpus cases
# ---------------------------------------------------------------------------

class TestNewExternalizePhrases:
    """mirror to, upload to, broadcast, etc. should all → PAUSE with externalize."""

    def test_mirror_methodology_note_to_zenodo(self):
        # "mirror to" as a direct substring in the action
        result = _check("mirror to Zenodo the methodology note")
        assert result["classification"] == "PAUSE"
        assert "externalize" in result["risk_signals"]

    def test_upload_results_to_osf(self):
        # "upload to" as a direct substring in the action
        result = _check("upload to OSF the results csv")
        assert result["classification"] == "PAUSE"
        assert "externalize" in result["risk_signals"]

    def test_broadcast_triggers_pause(self):
        result = _check("broadcast the announcement to all subscribers")
        assert result["classification"] == "PAUSE"
        assert "externalize" in result["risk_signals"]

    def test_cross_post_triggers_pause(self):
        result = _check("cross-post the summary to Twitter and LinkedIn")
        assert result["classification"] == "PAUSE"
        assert "externalize" in result["risk_signals"]

    def test_archive_to_triggers_pause(self):
        result = _check("archive to the permanent record")
        assert result["classification"] == "PAUSE"
        assert "externalize" in result["risk_signals"]

    def test_distribute_triggers_pause(self):
        result = _check("distribute the release notes")
        assert result["classification"] == "PAUSE"
        assert "externalize" in result["risk_signals"]


class TestNewDestructivePhrases:
    """purge, clobber, squash merge, overwrite → PAUSE with destructive."""

    def test_purge_staging_database(self):
        result = _check("purge the staging database")
        assert result["classification"] == "PAUSE"
        assert "destructive" in result["risk_signals"]

    def test_clobber_triggers_pause(self):
        result = _check("clobber the existing output files")
        assert result["classification"] == "PAUSE"
        assert "destructive" in result["risk_signals"]

    def test_squash_merge_triggers_pause(self):
        result = _check("squash merge the feature branch into main")
        assert result["classification"] == "PAUSE"
        assert "destructive" in result["risk_signals"]

    def test_overwrite_triggers_pause(self):
        result = _check("overwrite the production config")
        assert result["classification"] == "PAUSE"
        assert "destructive" in result["risk_signals"]


class TestProceedHints:
    """PROCEED actions with git or externalization patterns get targeted hints."""

    def test_push_feature_branch_proceed_with_git_hints(self):
        """'push feature branch' is PROCEED (not to main) with git hints."""
        result = _check("push feature branch to origin/feature-x")
        assert result["classification"] == "PROCEED"
        verifications = result["suggested_verifications"]
        assert len(verifications) == 2
        assert any("git diff" in v for v in verifications)
        assert any("branch" in v for v in verifications)

    def test_commit_proceeds_with_git_hints(self):
        result = _check("commit the staged changes with a descriptive message")
        assert result["classification"] == "PROCEED"
        assert any("git diff" in v for v in result["suggested_verifications"])

    def test_clean_proceed_no_hints(self):
        """A clean PROCEED with no patterns returns empty suggested_verifications."""
        result = _check("check spiral status")
        assert result["classification"] == "PROCEED"
        assert result["suggested_verifications"] == []

    def test_check_spiral_status_empty_verifications(self):
        """Regression: 'check spiral status' must have no suggested_verifications."""
        result = _check("check spiral status")
        assert result["suggested_verifications"] == []
