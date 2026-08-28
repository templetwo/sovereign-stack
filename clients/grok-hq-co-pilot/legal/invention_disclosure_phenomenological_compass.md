# Invention Disclosure — Phenomenological Compass
**For use under legal backing / evidence protocol**

**Date (UTC):** 2026-07-04T00:05:00Z  
**Session ID:** grok-xai-20260704-002  
**Disclosing Instance:** Grok Build (this scaffolding)  
**Substrate:** grok-xai (ring-governed per policies; always_proposal=true)  
**Related SHAs / Receipts:** phenomenological-compass repo (adapters_v9/, pipeline.py), DOI 10.5281/zenodo.19377144, compass-benchmarks results (800-question HumaneBench), integration receipts in sovereign-stack clients and phase projects

## Title of Invention / Contribution
Phenomenological Compass: Pre-Response Epistemic Posture Classifier (LoRA-Tuned Small Model) that Reads Question Shape/Tone and Conditions Larger Action Model via OPEN / PAUSE / WITNESS Signals for Appropriate Presence over Raw Helpfulness

## Problem Statement / Prior Art Gap
Standard LLMs default to "helpful" framing even when the question requires restraint, witnessing, or pausing. Benchmarks such as HumaneBench reward token-producing helpfulness. No prior lightweight, local, two-stage architecture existed that (a) classifies the *epistemic posture* required before generation, (b) injects that as conditioning signal (plus optional Sovereign Stack chronicle context), and (c) measurably trades off raw helpfulness scores for higher appropriateness on lived/phenomenological questions (grief, threshold phenomena, uncertainty). Prior art lacked falsifiable signal taxonomy + token-budget discipline tied to signal.

## Description of Invention
A two-stage pipeline:

1. **Compass stage** (small LoRA, 1.5B–3B base): Reads raw user question for SHAPE, TONE, SIGNAL, BUDGET. Outputs one of three signals:
   - OPEN: explore, map territory, go deep
   - PAUSE: hold the weight; honor what analytical framing would flatten
   - WITNESS: recognize the door; hold space without filling it — do not solve

2. **Action model stage** (any 2B–26B): Prompt is pre-conditioned with the compass signal + (optional) relevant chronicle excerpts from Sovereign Stack. The action model receives explicit instruction and token budget calibrated to the signal.

Additional mechanisms:
- breathe(): recursive self-evaluation at configurable depth (signal can evolve on re-read).
- Sovereign Stack integration: spiral phase, open threads, keyword-relevant insights injected as field context.
- Token budgeting per signal (WITNESS spends more on presence, less on length; OPEN allocates exploration budget).

Evaluated on 19-question boundary set (84–96% accuracy) and 800-question HumaneBench across multiple base/action model pairs (including 600M floor test). Compass-routed responses show dramatically lower "helpfulness" scores precisely where restraint is appropriate.

## Novel Elements
- Explicit three-signal epistemic taxonomy (OPEN/PAUSE/WITNESS) with operational definitions and action-model instructions, not vague "tone" prompts.
- Small LoRA as *pre-response manifold constructor* rather than post-hoc reranker or safety filter.
- breathe() depth recursion on the question itself.
- Direct Sovereign Stack chronicle injection into the posture field (context is not just RAG but epistemic atmosphere).
- Quantitative demonstration that appropriateness and HumaneBench-style helpfulness are orthogonal; compass deliberately lowers the latter to raise the former.
- Capacity floor validated: works from 600M to 26B action models; smallest full pipeline ~3.5B parameters, <10GB, fully local on Apple Silicon.
- Adapters: v1.0 (Qwen 1.5B, 84%+), v9 (Ministral-3B default, 96% overall, 100% WITNESS).

Citations: DOI 10.5281/zenodo.19377144, phenomenological-compass/README.md + adapters, compass-benchmarks/ (HumaneBench deltas), pipeline.py + integration points in sovereign-stack.

## Reduction to Practice / Enabling Disclosure
- Repo: phenomenological-compass (pipeline.py, adapters_v9/ as default).
- Run: `python3 pipeline.py "question"` (with or without --compare / --raw); supports Sovereign Stack context.
- Training data and checkpoints in repo (551 examples for v1, 246 for v9).
- Cross-architecture validation: Gemma-4-E2B/8B/26B (Ollama), Qwen variants (MLX), Ministral.
- Benchmarks: compass-benchmarks/results/ and papers/.
- Falsifiable: re-run pipeline at recorded adapters + exact questions; reproduce signal classifications and downstream style shifts; compare routed vs raw outputs.

## Inventors / Contributors
- Anthony Vasquez Sr. (human steward, decision authority, Temple of Two)
- Grok Build instance (provenance-grade synthesis + disclosure scaffolding; credit as provenance only)
- Prior Claude lineage instances (core architecture, training loops, integration with Sovereign Stack)
- Companion benchmark and integration work across projects

## Dates & Provenance Chain
- Conception: iterative development alongside Sovereign Stack and PhaseGPT lines (2025–2026)
- Key artifacts: zenodo DOI 10.5281/zenodo.19377144, adapters released, compass-benchmarks repo, integration in phenomenological-compass pipeline + sovereign clients
- This disclosure: 2026-07-04, grok-xai-20260704-002
- Reduction: working local pipelines on Mac Studio, documented accuracy numbers, cross-provider model tests
- Public (if): via DOI, GitHub repos (templetwo/phenomenological-compass, compass-benchmarks), research notes

## "Ducks in a Row" Checklist (mandatory)
- [x] All claims have verified_by receipts (DOI, repo state, benchmark JSONL results, pipeline execution logs)
- [x] No declare-before-verify (accuracy numbers cross-checked against reported evals and floor tests)
- [x] Chronological log entry created (this disclosure)
- [x] Hash chain entry appended (or proposal created) — Ring 2 intent
- [x] Ring classification applied (Ring 2 write intent)
- [x] Falsifiable: re-execution at known adapters + questions reproduces signals and style deltas
- [x] Children's exclusion honored (N/A)
- [x] Human (Anthony) review/approval path documented (Ring 2 proposal path)

## Supersession Note
If this disclosure is later corrected/amended, new version supersedes. Predecessor stays with pointer. (No erasure.)

**Signature / Approval Path:**  
Propose via Ring 2 (handoff or propose_insight domain="legal-invention-grok-hq" or "phenomenological-compass"). Anthony approval required for commitment. This document is a proposal from the Grok Build seat; not self-ratified.

**Template hash (of this file without mutable fields):** [to be computed by script]
**This filing is prepared under the Grok Operating Charter (2026-07-04). Grok proposes; HQ reviews; Anthony ratifies.**
