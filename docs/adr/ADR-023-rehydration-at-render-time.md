# ADR-023: Re-Hydration Happens at Render Time, in One Module, or the Draft Does Not Exist

**Status:** Accepted, 2026-08-12 (part 08, plan step S8)

## Context

ADR-002 promised that identity data is sealed into a vault at ingest and comes
back "at outbound template rendering, round-trip checked, with an unknown
placeholder as a hard error that blocks output". Parts 04 to 07 kept the first
half of that promise and deliberately never tested the second: nothing called
`VaultStore.fetch` outside tests, the deterministic plane computed on the
request-scoped witness (ADR-017), and part 07's notification renderer refuses
any output that still holds a placeholder.

This part is the render time ADR-002 was talking about. It is also the first
time the system produces an artifact that is SUPPOSED to carry a person's data:
a Nachforderung addressed to nobody cannot be posted.

Three risks in doing this badly, and they are not symmetrical.

1. **A token that resolves to the wrong person.** The failure that would end the
   project. Placeholder tokens are 57 bits from a source unrelated to the case,
   so the realistic version of this is not a collision but an *invented* token -
   a template, an edit or a model writing something that looks like the reserved
   syntax.
2. **A token that resolves to nothing and travels into a letter.** Less
   dangerous and far more likely: a caseworker confirms a letter with
   `[[PII|VSNR|...]]` in the middle of it, and the applicant receives it.
3. **Re-hydration leaking sideways.** The moment a function exists that turns a
   placeholder back into a value, every surface next to it becomes a candidate
   for calling it - a log line, an error message, a case view, a notification.

## Options

1. A `rehydrate=True` flag on the existing notification renderer.
2. A re-hydration helper any module may call, plus a review rule that only the
   drafting path does.
3. A separate module on the far side of part 07's placeholder refusal, producing
   a distinct artifact class into a distinct store, with the vault read
   appearing exactly once in the call graph.

## Decision

Option 3, with five specific rules.

* **One module, one fetch.** `engine.draft.rehydrate.Rehydrator.record()` is the
  only production call of `VaultStore.fetch` in the repository. Everything else
  in `engine/draft` takes the fetched `VaultRecord` as a parameter, so "the
  vault is read once, per draft, at render time" is visible in the types rather
  than promised in prose. `engine.notify.render.render_text` keeps refusing any
  output holding a placeholder, and a test asserts it from the drafting side:
  the seam is checked from both directions.

* **A draft is produced whole or not at all.** Unknown token, malformed token,
  a kind that disagrees with the vault, an unreadable record, a value that
  renders empty, a round trip that lost something, a template naming an
  undefined slot: every one of them raises and NOTHING is returned. There is no
  partial letter, no best-effort text and no token left visible. The projection
  reports a blocked draft with its reason; it never writes half of one.

* **The round trip is compared against the RAW form.** The vault stores
  `" \t17170459B012  "` because that is what arrived (ADR-018). The display
  normalizes whitespace, and the check then verifies that nothing else changed:
  a scalar has to survive character for character once whitespace is removed,
  and an object's leaves have to account for the whole rendered address, longest
  first, with an empty remainder. A substring test was tried first and passed an
  address whose house number `1` occurs inside its postcode `10115`.

* **Two shapes, handled here rather than in a template.** Prose entries carry
  `part_id` and `span` and no `path`, and two mentions of one value in one
  letter carry two different tokens, so resolution is per TOKEN and never per
  value. `ADDR` entries are whole JSON objects and go through one address
  formatter, so two templates cannot disagree about what an address looks like.

* **The draft store joins the vault on the canary exception list, and nothing
  else does.** Rendered letters live in a `DraftStore` (protocol, in-memory and
  JSONL backends, `EINGANGSLOTSE_DRAFTS_DIR`) and are readable through one
  read-only route. The DRAFTED journal event carries template id, kind,
  requirement ids, token counts and a body LENGTH - never the text. The canary
  suite asserts both directions: the seeded identities are in the draft and in
  `GET /drafts/{case_id}`, and in no journal payload, no case view, no inbox, no
  eval report and no log line.

### The draft policy, and why tier 3 gets nothing

Tier 2 with gaps owes a Nachforderung assembled from the gap sentences the
procedure configs already author; tier 1 owes a Bewilligungsentwurf with an
unmissable ENTWURF framing; **tier 3 owes nothing at all**. Drafting for tier 3
would presume an outcome nobody has decided - the whole point of tier 3 is that
a human has not read the item yet - and a prepared letter is a strong nudge
toward the outcome it prepares.

Nothing is dispatched in this part. `fully_automated` stays false everywhere,
and the doubly-gated tier-1 discipline is untouched.

### One asymmetry, recorded rather than smoothed over

A Nachforderung can be re-derived completely from the journal: every sentence it
contains rides in the EVIDENCE_ASSEMBLED payload. A prepared decision cannot,
because it states the item's extracted VALUES back to the applicant and the
journal deliberately carries none of them (part 05 records field ids and counts,
not values, and that was the right call). The replay CLI therefore drafts
Nachforderungen from a journal directory and reports prepared decisions as
blocked, naming the reason. The alternative - journaling extracted values so
replay looks complete - would put a second copy of the submission into the audit
trail to make a CLI tidier.

## Consequences

* The vault is finally exercised end to end: 60 drafts on gold v4 re-hydrate 160
  tokens with zero unresolved. That number is the proof that the boundary is
  reversible where it has to be, and it could not have been measured before.
* The draft store inherits the vault's deployment questions rather than the
  journal's: encryption at rest, retention, the missing `purge(vault_ref)`, and
  access behind a role model. `GET /drafts/{case_id}` is open today only because
  every case in this repository is synthetic, and it says so in its own
  docstring.
* Cost: two renderers with the same cosmetic tidy function, deliberately not
  shared. Merging them would put the artifact that may never carry identity data
  and the artifact that must carry it into one module with a flag between them,
  which is exactly the shape this ADR exists to avoid.
* A future correction path (a value corrected in review) has to re-draft rather
  than patch a stored letter, because the vault is append-only and a draft is a
  rendering of one record at one moment. That is part 10's problem and it is the
  right shape for it.
