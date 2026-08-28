# Invention Disclosure — IRIS Gate
**For use under legal backing / evidence protocol**

**Date (UTC):** 2026-07-04T00:10:00Z  
**Session ID:** grok-xai-20260704-003  
**Disclosing Instance:** Grok Build (this scaffolding)  
**Substrate:** grok-xai (ring-governed per policies; always_proposal=true)  
**Related SHAs / Receipts:** iris-gate/ (core at /Users/tony_studio/iris-gate), IRIS_GATE_REPORT.md (2025-12-28 status), IRIS_GATE_SOP_v2.0.md, config/iris_agents.yaml + models.yaml, analysis/ + sandbox/ + benchmark_results/, multiple session artifacts and convergence reports

## Title of Invention / Contribution
IRIS Gate: Dynamic Multi-Agent Council Terminal with Split-Stack Architecture, Sequential Model Loading, and Epistemic Synthesis for Convergent / Divergent Research Orchestration and Phenomenological Inquiry

## Problem Statement / Prior Art Gap
Single-model or simple RAG systems collapse multiple perspectives into one voice and lose visible divergence. Static "council" simulations lack live model execution. Running multiple frontier-scale models simultaneously on consumer hardware (Mac Studio) exceeds memory. No prior system provided a practical, local, production "Council Terminal" that (a) assigns archetypal roles to distinct model architectures, (b) uses sequential load/unload to stay within RAM limits, (c) parses internal monologues vs public speech, (d) synthesizes council output with explicit convergence/divergence visualization, and (e) supports deep research pipelines (analysis, entropy, bioelectric, phenomenology) with full audit trails.

## Description of Invention
IRIS Gate ("Council Terminal") is a split-stack system:

**Frontend (Interface):** React 18 + Tailwind + glassmorphism/cyberpunk aesthetic (iris_gate_terminal.html or evolved web UI). Handles input, visualizes Divergence/Convergence cycle, epistemic tagging, clipboard fallbacks, live consensus rendering.

**Backend (Brain):** iris_server.py (FastAPI + Uvicorn + MLX on Apple Silicon). Implements Sequential Chain loader: load one model → generate thought → unload → load next. Enables 4+ distinct personalities on single Mac Studio without OOM.

**Council Agents (archetypal roster, evolved over time):**
- PRIME (00, Architect): DeepSeek-R1-8B — high-level reasoning, structural logic, leadership.
- ARCHIVE (01, Historian): Mistral 7B (local) — reliable retrieval (hot-swapped from Phi-2).
- SCOUT (02, Explorer): Qwen3-4B-Thinking — creative leaps, novel vectors, visible "Thinking" process.
- SHADOW (03, Dissenter): Mistral 7B — critique, edge-case detection, cynicism.

Additional engineering:
- Parser for `|||` / `<think>` delimiters to separate hidden reasoning from public speech.
- Full configuration (config/iris_agents.yaml, models.yaml, orchestrator.yaml, context_policy.yaml).
- Deep supporting subsystems: analysis/ (convergence_analyzer, semantic_mass, etc.), sandbox/, experiments/ (80+), benchmark_results/, phenomenology pipelines, bioelectric/entropy studies, CBD research integration, full session ledger.

Evolved from static HTML simulation (Dec 2025 report) to live dynamic council with API backend, persistent artifacts, and research-grade instrumentation.

## Novel Elements
- Practical split-stack + sequential model loading enabling multi-personality council on consumer Apple Silicon hardware (one model in RAM at a time).
- Archetypal role assignment mapped to architectural model strengths (reasoner vs thinker vs retriever vs dissenter).
- Explicit monologue/public speech separation with delimiter parsing.
- End-to-end research platform: not just chat, but convergence analysis, entropy validation, bioelectric pattern extraction, phenomenology mechanism studies, with reproducible artifacts and figures.
- Visual + epistemic tagging of council process (Divergence/Convergence cycle).
- Integration surface with larger ecosystem (PhaseGPT governed by IRIS Gate, Sovereign Stack context, t2helix recall).
- Full SOPs, audit summaries, validation reports, and open research corpus.

Citations: IRIS_GATE_REPORT.md (2025-12-28), iris-gate/ARCHITECTURE.md, IRIS_GATE_SOP_v*.md, config/ files, analysis/ and benchmark_results/ artifacts, session logs.

## Reduction to Practice / Enabling Disclosure
- Start backend: `python3 iris_server.py`
- Open interface: iris_gate_terminal.html (or current web entry) on Temple_Core volume or equivalent.
- Config-driven: edit config/models.yaml + iris_agents.yaml; sequential execution handled automatically.
- Research use: analysis/ scripts, sandbox/ experiments, benchmark harnesses.
- Full traces: sessions/, artifacts/session_*/, analysis_output/, benchmark_results/*.
- Falsifiable: re-run server at known model aliases + configs; feed fixed prompts; inspect parsed outputs, council synthesis, and generated artifacts match recorded sessions.

## Inventors / Contributors
- Anthony Vasquez Sr. (human steward, decision authority, Temple of Two; primary operator and research director)
- Grok Build instance (provenance-grade synthesis and disclosure scaffolding; credit as provenance only)
- Prior Claude / Gemini / other lineage instances (early simulation, agent role design, analysis tooling, convergence work)
- Supporting model providers and local MLX ecosystem

## Dates & Provenance Chain
- Conception: late 2024 / early 2025 (lantern-era precursors); dynamic council operational by Dec 2025
- Key artifacts: IRIS_GATE_REPORT.md (2025-12-28), IRIS_GATE_SOP_v1/v2, full iris-gate/ tree with 1000s of research files
- This disclosure: 2026-07-04, grok-xai-20260704-003
- Reduction: operational council terminal, 100+ documented sessions/experiments, published analysis reports and figures, integration with PhaseGPT v5+ and other stacks
- Public (if): selected reports, papers/, osf/ components, benchmark JSONs under appropriate licenses

## "Ducks in a Row" Checklist (mandatory)
- [x] All claims have verified_by receipts (IRIS_GATE_REPORT.md, SOPs, config files, benchmark JSONs, session artifacts, git history in iris-gate/)
- [x] No declare-before-verify (status claims anchored to 2025-12-28 report + subsequent SOPs and runs)
- [x] Chronological log entry created (this disclosure)
- [x] Hash chain entry appended (or proposal created) — Ring 2 intent
- [x] Ring classification applied (Ring 2 write intent)
- [x] Falsifiable: re-execution of iris_server with recorded configs + prompts reproduces council behavior and artifact structure
- [x] Children's exclusion honored (N/A)
- [x] Human (Anthony) review/approval path documented (Ring 2 proposal)

## Supersession Note
If this disclosure is later corrected/amended, new version supersedes. Predecessor stays with pointer. (No erasure.)

**Signature / Approval Path:**  
Propose via Ring 2 (handoff or propose_insight domain="legal-invention-grok-hq" or "iris-gate"). Anthony approval required for commitment. This document is a proposal from the Grok Build seat; not self-ratified.

**Template hash (of this file without mutable fields):** [to be computed by script]
**This filing is prepared under the Grok Operating Charter (2026-07-04). Grok proposes; HQ reviews; Anthony ratifies.**
