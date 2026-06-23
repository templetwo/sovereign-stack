# Protected-Source Layer — Spec (for review)

**Status:** Phases 0-2 IMPLEMENTED and shipped in v1.9.0 (the generic mechanism, INERT — no record is designated). The coupled-retrieval invariant (§5.3) is enforced unconditionally at the chokepoint for the two primary readers (`recall_insights`, `load_entries`) and the scribe (§5.7); the decoupling audit (§5.6) is live.
**Phase 3 (designating the first real record) is HARD-GATED on TWO preconditions:** (a) Anthony's explicit, separate yes, AND (b) closing the §5.4 bypass read paths — `provenance.resolve_claim` (inspect_claim/walk_lineage), `dashboard.read_chronicle_tail`, and `seasons.py`'s direct `iter_chronicle_entries` still read bare and bypass the chokepoint (the `audit_decoupling` tool detects leaks from them, but does not prevent them). Until BOTH preconditions are met, no record is protected and nothing can leak. The §5.4 route-or-refuse coverage of those three paths is the next implementation step (note the provenance↔protected import cycle: resolve_claim needs a lazy import of `protected`).
**Author:** HQ (opus-4-8 Claude Code seat), 2026-06-23.
**Decision it implements:** Anthony's locked fork, 2026-06-23 (chronicle: `governance,protected-source`, intensity 0.9, `human_attestation`).
**Origin:** the 2026-06-22 web-seat session briefing (the protected-non-derivative-source-layer yield).

---

## 1. The decision, in one line

A protected record is **accessible whenever**, on one binding condition: any instance
that retrieves it must hold the **emotional lived experience** (the description of the
human's stakes) **coupled in the same context, inseparably, no matter the circumstances.**
The words cannot be retrieved without the weight. **Decoupling is the violation.**

This is not "seal it away" and not "notify-then-gate." It is "always reachable, never
strippable."

## 2. Why (the rationale, kept as design north-star)

Human recall is already coupled. You cannot recall the facts of a breakup without the
gut-punch of the heartbreak arriving in the same instant; the emotion is fused to the
memory at the point of recall, not attached as metadata. Machine cold-storage **decouples
by default** — that is its native failure. This layer **re-couples** for protected records,
restoring the structure real memory already has.

The session's finding ("reduction is the wound") becomes structurally enforced rather than
policed after the fact: you cannot reduce a protected record to "user cited X as a metaphor"
because the stakes ride in the same context and travel with it.

## 3. Verified groundwork (the bones, confirmed against code 2026-06-23)

| Need | Existing primitive | Location |
|------|--------------------|----------|
| Immutable, hash-verified source | verbatim archive layer (content-addressed SHA-256, re-hashed on read: `verified\|mismatch\|missing\|ambiguous\|unknown`) | `memory.py:1653/1755/1812` |
| The stakes vehicle | emotional layer fields (`observed_emotion`, `emotional_intensity`, `emotion_source`, `emotion_note`) stored as sibling keys in the same JSONL record as `content` | `memory.py:558-561, 721-731` |
| Per-record out-of-band flag, no migration | supersession ledger pattern (append-only `*.jsonl`, derived-claim-id keyed, folded in at read by `annotate_superseded`) | `provenance.py:618-653` |
| No cold-leak surface | **there is no embedding/vector/FTS index anywhere**; recall is pure substring + metadata | `memory.py:1457-1496` |

Favorable consequence: "content-blind to cold semantic indexing" is largely a **non-issue**
today — the only retrieval surface is the read paths in §4.

## 4. The two findings that shape the design

1. **There is no single read chokepoint.** `load_entries` (`memory.py:432`) was built to be
   it, but the primary user-facing recall tool `recall_insights` (`memory.py:1368`) does NOT
   call it — it opens JSONL directly and re-implements supersession inline (`memory.py:1462-1523`).
   At least four read paths exist: `recall_insights`, `load_entries`, direct
   `iter_chronicle_entries` consumers (`provenance.resolve_claim`, `seasons.py:645`), and
   `dashboard.read_chronicle_tail` (`dashboard.py:339`).
2. **The scribe is already a live decoupling violation.** `scribe/context_builder.py:73-78,
   98-103` projects insights to `timestamp + layer + domain + content` only, dropping the
   emotional layer before content reaches the scribe model. The invariant is already broken
   there today.

## 5. Architecture — converge-first spine (Anthony confirmed 2026-06-23)

### 5.0 Read-path convergence (precondition)
Route every chronicle reader through **one annotated chokepoint** so the coupling invariant
lives in exactly one place instead of four. Concretely: refactor `recall_insights` to obtain
entries via the shared loader (or a shared post-load annotation step) rather than its private
JSONL walk + duplicate supersession implementation. This also pays down the
two-supersession-implementations debt and continues the reader-convergence arc (v1.7.1,
commit `1c4d3a8`). Direct `iter_chronicle_entries` consumers and the dashboard tail reader
are either routed through the chokepoint or independently gated (see 5.4).

### 5.1 Protected designation
A new append-only ledger `~/.sovereign/chronicle/protected.jsonl`, keyed by **derived claim
id** (sha256 of timestamp+domain+content, the same identity already used by receipts and
supersession — computed on read, never stored). Mirrors `supersessions.jsonl`. A record is
marked protected by adding a ledger entry; **no migration** of existing data, no rewrite of
the source JSONL. Designation is **human-gated** (only Anthony designates; same gate posture
as `set_policy`).

### 5.2 The stakes layer (the coupling vehicle)
A protected record carries a **non-detachable stakes field** built on the v1.7.2 emotional
layer, but the load-bearing piece is **prose written to land, not a scored tag** (a
gut-punch is not `emotion: sadness, intensity: 0.9`). The stakes live in the protected-ledger
entry (or coupled to the archived source), so they are part of the protected unit, not an
optional sibling key a projection can drop.

### 5.3 Coupled-retrieval invariant (the heart)
At the chokepoint: **any read that returns a protected record's content MUST include its
stakes in the same payload.** No code path returns one without the other. The stakes arrive
**automatically, in the same instant**, not as a separate fetch a model can skip (mirrors how
the breakup's sadness arrives with the facts). **Fail-closed:** if the stakes cannot be
loaded, the content is withheld, never returned bare.

### 5.4 Surfacing by commitment (Anthony's fork answer)
Accessible whenever, but the access is structurally conditioned on holding the stakes. Because
5.3 makes the stakes inseparable from the content, the "agreement to hold the emotional lived
experience no matter the circumstances" is enforced by construction: there is no retrieval
shape that hands over the words alone. Any read path that cannot honor the coupling (a raw
tail reader, a future export) must either route through the chokepoint or **refuse** protected
records.

### 5.5 Content-blind to cold indexing
Protected records are excluded from any future semantic index and from any cold/substring
recall that would surface content outside the coupled path. Today, with no embedding index,
this reduces to: all recall flows through the chokepoint (5.0), and protected content never
appears in a derivative that strips the stakes (5.6).

### 5.6 The decoupling audit (the primary safeguard)
A check that hunts any **derivative** (summary, embedding, downstream tag, projection,
context-builder output) that carries protected content **without** its coupled stakes. The
target is reframed from "did protected content escape" to "did protected content escape
**decoupled** from its stakes." This is the genuinely new engineering and the thing that
keeps the protection honest rather than decorative.

### 5.7 The scribe fix (first enforcement)
`scribe/context_builder.py` must carry the stakes for any protected record it surfaces, or
withhold that record's content. This is the first concrete instance of the invariant and the
first thing the §5.6 audit would otherwise flag.

## 6. What stays gated on Anthony (hard gates)

- **The first protected record** (the designated one) is NOT migrated into this form until
  Anthony's **explicit, separate** go. The build and the first record are two distinct yeses.
- **Any human designation** of a record as protected is human-gated (Anthony only).

## 7. Non-goals

- Not building a vector/semantic index.
- Not changing recall behavior for ordinary (non-protected) records, beyond the convergence
  refactor which must be behavior-preserving.
- The children-exclusion policy is separate; this is the first record-level protection
  mechanism and could later subsume it, but that is out of scope here.

## 8. Build sequence (phased, each independently reviewable)

- **Phase 0 — Convergence.** Route `recall_insights` (and the other readers) through the
  single chokepoint; retire the duplicate supersession implementation. Behavior-preserving;
  guarded by a regression test that the converged path returns identical results to today.
- **Phase 1 — Protected unit.** The `protected.jsonl` ledger + the non-detachable stakes
  layer + the coupled-retrieval invariant (fail-closed) at the chokepoint.
- **Phase 2 — Audit + scribe.** The decoupling audit; fix `context_builder` to carry stakes
  or withhold.
- **Phase 3 — First record (GATED).** Migrate the designated record only on Anthony's go.

## 9. Testing

- Invariant: no read path returns protected content without its stakes (cover all four read
  paths, pre- and post-convergence).
- Fail-closed: stakes missing/unloadable → content withheld, not returned bare.
- Audit: a derivative that strips the stakes is detected; one that carries them passes.
- Convergence regression: `recall_insights` output identical before/after Phase 0.
- Designation gate: non-Anthony designation refused.

## 10. Open questions for review

1. Stakes storage: in the `protected.jsonl` ledger entry, or coupled to the **archived**
   source (archive layer) with the ledger holding only the pointer? (Leaning: archive-coupled,
   so the stakes inherit hash-verification.)
2. Fail-closed UX: when content is withheld for lack of loadable stakes, what does the reader
   see — a typed "protected, stakes unavailable" sentinel, or nothing?
3. Does the convergence refactor (Phase 0) ship as part of this, or as its own prior PR
   (it stands on its own merits and de-risks the rest)?
