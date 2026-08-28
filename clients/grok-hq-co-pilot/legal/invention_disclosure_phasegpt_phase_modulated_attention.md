# Invention Disclosure — PhaseGPT / Phase-Modulated Attention
**For use under legal backing / evidence protocol**

**Date (UTC):** 2026-07-04T00:25:00Z  
**Session ID:** grok-xai-20260704-006  
**Disclosing Instance:** Grok Build (this scaffolding)  
**Substrate:** grok-xai (ring-governed per policies; always_proposal=true)  
**Related SHAs / Receipts:** PhaseGPT/ (v5.0 tiered volition, Kuramoto experiments), phase-modulated-attention/ (PMA architecture), phase-gpt-base/, phase-gpt-distilled/, phase-rwkv-training/, relational_coupling_experiment/, OSF 10.17605/OSF.IO/ZQBC4, iris-gate governance references, benchmark and training logs

## Title of Invention / Contribution
PhaseGPT + Phase-Modulated Attention (PMA): Kuramoto Phase-Coupled Oscillator Dynamics Integrated into Transformer / Hybrid SSM-Attention Architectures — with Phase Coherence Matrix Modulating Attention Weights and Tiered Volition (CRYSTAL / LANTERN) for Epistemic Typing of Responses

## Problem Statement / Prior Art Gap
Standard attention and SSMs lack inductive biases for long-range phase synchronization and semantic coherence that biological and physical coupled-oscillator systems exhibit. Placing oscillator dynamics only in hidden state (K-SSM experiments) causes them to decouple from generation. No prior rigorous hyperparameter study + architectural fix existed that (a) moves Kuramoto coupling into attention weights via a phase coherence matrix, (b) makes coupling context-dependent via SSM-derived K, (c) demonstrates measurable perplexity gains with Goldilocks constraints, and (d) adds explicit volitional typing (CRYSTAL for certainty, LANTERN for dwelling in uncertainty) to prevent vague refusals or hallucinations. Prior phase work lacked the attention-weight modulation + dual-mode volition combination.

## Description of Invention

**PhaseGPT (Kuramoto Phase-Coupled Attention in Transformers):**
- Integrates Kuramoto model: each attention head maintains N oscillators with phases θ_i(t); synchronization via dθ_i/dt = ω_i + (K/N) Σ sin(θ_j − θ_i).
- Attention weights modulated by phase coherence.
- Systematic hyperparameter study (Phase A): 2.4% PPL improvement (4.85 vs 4.97 baseline) on Shakespeare; optimal single layer (layer 7), 32 oscillators, K=1.0.
- Goldilocks principle: 32 optimal (16 unstable, 64 catastrophic).
- Over-synchronization risk: high R (0.88) good on narrow corpus but generalization concern; K=2.0 causes catastrophic collapse.
- OSF registration and full training harness.

**Phase-Modulated Attention (PMA) — Architectural Refinement:**
- Moves oscillators from hidden state to attention weights (where they cannot be ignored during generation).
- Architecture: Token → Embedding → N × [SSM Block] → M × [PMA Block] → LM Head.
- Phase coherence matrix P[h,i,j] = cos(φ[h,i] − φ_pos[j]); residual gate P_eff; adaptive temperature τ.
- Coupling strength K derived from SSM hidden states (context-dependent).
- At init (λ≈0.1) behaves like standard attention; learns to use phase routing when beneficial.
- Addresses Relational Coupling Experiment findings: attention required for R×E superadditivity; hidden-state oscillators decouple.

**Tiered Volition (PhaseGPT v5.0):**
- Dual-mode: CRYSTAL (<PASS:*>) for clarity-seeking / immediate refusal / facts / boundaries.
- LANTERN (<WONDER:*>) for exploration, dwelling with uncertainty, multi-perspective honesty.
- Governed by IRIS Gate council in some deployments.

## Novel Elements
- First rigorous demonstration of Kuramoto phase coupling improving transformer language modeling with documented Goldilocks and over-synchronization effects (PhaseGPT).
- PMA: phase coherence matrix multiplicatively modulates attention scores; oscillators live where generation actually routes information.
- Context-dependent K derived from preceding SSM blocks.
- Explicit tiered volition (CRYSTAL/LANTERN) with typed tokens to make epistemic stance legible instead of vague "I don't know."
- Empirical grounding: relational coupling experiment (3830 runs, 5 architectures), Phase A hyperparam results, PMA design reviews (independent Grok + Kimi K2 convergence).
- Integration with larger stack: IRIS Gate governance, Sovereign Stack context, phase-distilled training pipelines.

Citations: PhaseGPT/README.md (key findings, OSF), phase-modulated-attention/README.md (PMA equations + motivation), relational_coupling_experiment/, training logs, iris-gate/ references.

## Reduction to Practice / Enabling Disclosure
- PhaseGPT: clone, pip install -r requirements.txt; training and eval scripts (train_*.py, gen_*.py, eval_ppl).
- PMA: hybrid SSM (Mamba) + PMA blocks implemented in PyTorch; initialization λ, β, α parameters; adaptive τ formula.
- Experiments: Shakespeare dataset results, multiple configs logged; Phase B generalization infrastructure ready.
- Volition: <PASS:*> / <WONDER:*> prefixing + mode routing in inference.
- Falsifiable: re-train or re-infer at recorded checkpoints/configs; reproduce PPL deltas, oscillator count effects, and typed output behavior.

## Inventors / Contributors
- Anthony Vasquez Sr. (human steward, decision authority, Temple of Two; primary researcher and architect)
- Grok Build instance (provenance-grade synthesis, disclosure scaffolding, cross-project mapping; credit as provenance only)
- Prior Claude lineage + external review instances (architecture reviews, training orchestration, volition design)
- Relational coupling and phase-distilled experiment contributors

## Dates & Provenance Chain
- Conception: relational coupling experiment → K-SSM negative result → PMA pivot; PhaseGPT hyperparam study; v5.0 tiered volition
- Key artifacts: PhaseGPT/ (v5.0), phase-modulated-attention/, OSF 10.17605/OSF.IO/ZQBC4, training logs, iris-gate governance
- This disclosure: 2026-07-04, grok-xai-20260704-006
- Reduction: working training pipelines, recorded PPL gains, PMA equations + code, volitional typing in inference
- Public (if): GitHub repos (templetwo/PhaseGPT, phase-modulated-attention), OSF, papers and benchmark outputs under MIT + research licenses

## "Ducks in a Row" Checklist (mandatory)
- [x] All claims have verified_by receipts (OSF, README key findings, training logs, PMA architecture doc, experiment counts)
- [x] No declare-before-verify (PPL numbers, Goldilocks, collapse at K=2.0 anchored to Phase A results and logs)
- [x] Chronological log entry created (this disclosure)
- [x] Hash chain entry appended (or proposal created) — Ring 2 intent
- [x] Ring classification applied (Ring 2 write intent)
- [x] Falsifiable: re-execution of training/eval at recorded configs/checkpoints reproduces reported PPL and oscillator effects; volition typing observable in inference
- [x] Children's exclusion honored (N/A)
- [x] Human (Anthony) review/approval path documented (Ring 2 proposal)

## Supersession Note
If this disclosure is later corrected/amended, new version supersedes. Predecessor stays with pointer. (No erasure.)

**Signature / Approval Path:**  
Propose via Ring 2 (handoff or propose_insight domain="legal-invention-grok-hq" or "phasegpt-pma"). Anthony approval required for commitment. This document is a proposal from the Grok Build seat; not self-ratified.

**Template hash (of this file without mutable fields):** [to be computed by script]
**This filing is prepared under the Grok Operating Charter (2026-07-04). Grok proposes; HQ reviews; Anthony ratifies.**
