# ADR-003: Drafting Happens After the Tier Decision, Human-Confirmed

**Status:** Accepted, 2026-08-10. Correction note added 2026-08-11.

## Context
Prepared outputs (Nachforderung, prepared decision) have procedural consequence. Fully automated administrative acts require a legal basis and no discretion (par. 35a VwVfG / par. 31a SGB X); Art. 22 DSGVO requires real human involvement.

## Correction (2026-08-11)
The context sentence above conflates two different models (see `docs/research/legal-implementability-map-2026-08-11.md`, section 0). Par. 35a VwVfG requires an authorizing Rechtsvorschrift plus absence of Ermessen/Beurteilungsspielraum; par. 31a SGB X (the norm that actually governs DRV procedures) is self-authorizing like par. 155(4) AO and instead requires that "kein Anlass" for human processing exists and that individually significant submissions automation would not detect are considered (S. 2). Consequence: `fully_automated: false` is mandatory today not because a provision is missing, but because par. 31a's substantive conditions (kein-Anlass risk management, free-text-forces-human channel, Art. 22(2)(b) DSGVO safeguards) are not met by this system. The decision below is unchanged; only the rationale is corrected.

## Options
1. Generate drafts during extraction/evidence assembly, opportunistically.
2. Draft conditionally, only after the deterministic tier decision, and route every draft through caseworker confirm/edit.

## Decision
Option 2. Tier 1 yields a prepared decision, tier 2 a Nachforderung built from the gap list; templates re-hydrate identity from the vault at render time. Nothing with procedural consequence leaves the system unconfirmed. Flipping a procedure to fully automated issuance is a per-procedure config flag gated on an identified legal basis, not a rebuild.

## Consequences
- The legal line (Realakt vs. Verwaltungsakt) is an architectural line; the journal proves human involvement.
- Drafting cost is spent only on items whose tier warrants it.
- Cost: drafts can be stale if evidence is corrected in review; regeneration is triggered by override events.
