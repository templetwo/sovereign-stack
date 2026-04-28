"""
Tests for the simulator → governance integration revived from v1.0.0
on 2026-04-26 (the distillation pass + simulator-revival fireside).

Two surfaces:

  * GovernanceCircuit.run() — adds a `simulation` block to its result with
    Monte Carlo predictions (reversibility + 90% CI) per detected event.
  * runtime_compass_check(with_simulation=True) — appends a `simulation`
    field to the verdict with most_reversible + best_outcome, so PAUSE/
    WITNESS/PROCEED is backed by reversibility evidence, not hand-waved.

The simulator import is best-effort — if NetworkX is unavailable, both
paths must degrade gracefully (simulation.available=False, no crashes).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sovereign_stack.governance import (
    DecisionType,
    GovernanceCircuit,
    MetricType,
    SIMULATOR_AVAILABLE,
    StakeholderVote,
    runtime_compass_check,
)


# ── GovernanceCircuit.run() ─────────────────────────────────────────────────


class TestGovernanceCircuitSimulation:
    """Detection → Simulation → Deliberation → Intervention. The
    simulation step was missing for 81 days (orphaned simulator.py
    since v1.0.0 Feb 5). Revived 2026-04-26 — these tests guard the
    wiring so a regression doesn't silently re-orphan it."""

    def _vote(self) -> StakeholderVote:
        return StakeholderVote(
            stakeholder_id="auto",
            stakeholder_type="technical",
            vote=DecisionType.PROCEED,
            rationale="test",
            confidence=0.9,
        )

    def _circuit_with_threshold(self) -> GovernanceCircuit:
        circuit = GovernanceCircuit()
        # Default detector has no thresholds. Add one to trigger events.
        circuit.detector.add_threshold(
            MetricType.FILE_COUNT, limit=100, warning_ratio=0.8
        )
        return circuit

    def test_result_has_simulation_block(self):
        circuit = self._circuit_with_threshold()
        with tempfile.TemporaryDirectory() as td:
            for i in range(150):  # over the 100 threshold
                Path(td, f"f{i}.txt").touch()
            result = circuit.run(td, [self._vote()])

        # The circuit always returns a simulation block — even when empty.
        assert "simulation" in result
        assert "available" in result["simulation"]
        assert "predictions" in result["simulation"]

    @pytest.mark.skipif(
        not SIMULATOR_AVAILABLE,
        reason="NetworkX unavailable; simulator runs best-effort",
    )
    def test_predictions_populated_when_events_detected(self):
        circuit = self._circuit_with_threshold()
        with tempfile.TemporaryDirectory() as td:
            for i in range(150):
                Path(td, f"f{i}.txt").touch()
            result = circuit.run(td, [self._vote()])

        sims = result["simulation"]["predictions"]
        assert len(sims) >= 1
        prediction = sims[0]
        # Each prediction has the four expected scenarios.
        scenarios = {o["scenario"] for o in prediction["outcomes"]}
        assert "reorganize" in scenarios
        assert "defer" in scenarios
        assert "incremental" in scenarios

    @pytest.mark.skipif(
        not SIMULATOR_AVAILABLE,
        reason="NetworkX unavailable; simulator runs best-effort",
    )
    def test_outcomes_have_reversibility_and_ci(self):
        circuit = self._circuit_with_threshold()
        with tempfile.TemporaryDirectory() as td:
            for i in range(150):
                Path(td, f"f{i}.txt").touch()
            result = circuit.run(td, [self._vote()])

        outcome = result["simulation"]["predictions"][0]["outcomes"][0]
        # Every outcome carries reversibility + confidence_interval — these
        # are the load-bearing fields compass_check uses for evidence.
        assert "reversibility" in outcome
        assert 0.0 <= outcome["reversibility"] <= 1.0
        assert "confidence_interval" in outcome
        ci = outcome["confidence_interval"]
        assert len(ci) == 2
        assert ci[0] <= ci[1]

    def test_no_events_means_empty_predictions(self):
        # No threshold added → no events → no predictions, but still
        # a well-formed simulation block (available reflects NetworkX).
        circuit = GovernanceCircuit()
        with tempfile.TemporaryDirectory() as td:
            result = circuit.run(td, [self._vote()])
        assert result["simulation"]["predictions"] == []

    def test_circuit_does_not_crash_when_simulator_missing(self, monkeypatch):
        # Force the circuit's simulator to None, mirroring NetworkX-absent
        # environments. Detection + deliberation + intervention must still
        # complete; the simulation block reports unavailability.
        circuit = self._circuit_with_threshold()
        monkeypatch.setattr(circuit, "simulator", None)
        with tempfile.TemporaryDirectory() as td:
            for i in range(150):
                Path(td, f"f{i}.txt").touch()
            result = circuit.run(td, [self._vote()])
        assert result["simulation"]["available"] is False
        assert result["simulation"]["predictions"] == []
        assert result["circuit_complete"] is True


# ── runtime_compass_check(with_simulation=True) ─────────────────────────────


class TestCompassCheckSimulation:
    """The simulation pass is opt-in via with_simulation=True. Default
    behavior must be unchanged for existing callers — that's the
    contract that lets us roll the change without touching every
    compass_check call site."""

    def test_default_behavior_unchanged(self):
        # No simulation field when with_simulation absent or False.
        result = runtime_compass_check(action="git commit -m 'normal commit'")
        assert "simulation" not in result

        result_explicit = runtime_compass_check(
            action="ls", with_simulation=False
        )
        assert "simulation" not in result_explicit

    def test_with_simulation_attaches_block(self):
        # With the flag on, every result has a simulation block —
        # callers can rely on the shape.
        result = runtime_compass_check(
            action="delete chronicle entries", with_simulation=True
        )
        assert "simulation" in result
        assert "available" in result["simulation"]

    @pytest.mark.skipif(
        not SIMULATOR_AVAILABLE,
        reason="NetworkX unavailable; simulator runs best-effort",
    )
    def test_simulation_includes_evidence_fields(self):
        result = runtime_compass_check(
            action="git push --force origin main",
            stakes="high",
            with_simulation=True,
        )
        sim = result["simulation"]
        assert sim["available"] is True
        # Evidence shape — these are what compass_check's PAUSE rationale
        # uses to answer "is this reversible?".
        assert "most_reversible" in sim
        assert "best_outcome" in sim
        assert "all_outcomes" in sim
        # most_reversible has the load-bearing fields.
        most_rev = sim["most_reversible"]
        assert "scenario" in most_rev
        assert "reversibility" in most_rev
        assert "confidence_interval" in most_rev

    def test_classification_unaffected_by_simulation(self):
        # Simulation is evidence-only; it must NOT change the
        # PAUSE/WITNESS/PROCEED verdict that the rules-first heuristic
        # produces. (If it ever does, compass_check has stopped being
        # rules-first and that's a separate decision.)
        action = "delete chronicle entries"
        without_sim = runtime_compass_check(action=action)
        with_sim = runtime_compass_check(
            action=action, with_simulation=True
        )
        assert without_sim["classification"] == with_sim["classification"]
        assert without_sim["risk_signals"] == with_sim["risk_signals"]

    def test_simulation_failure_reports_available_false(self, monkeypatch):
        # Force the simulator import path to fail. The compass_check call
        # should still return a usable result; the simulation block
        # reports the failure rather than raising.
        import sovereign_stack.governance as gov

        monkeypatch.setattr(gov, "SIMULATOR_AVAILABLE", False)
        result = runtime_compass_check(
            action="some action", with_simulation=True
        )
        # Verdict still produced.
        assert result["classification"] in {"PAUSE", "WITNESS", "PROCEED"}
        # Simulation block reports unavailability.
        assert result["simulation"]["available"] is False
        assert "reason" in result["simulation"]
