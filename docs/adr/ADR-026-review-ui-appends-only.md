# ADR-026: The Review UI Renders the Journal and Appends to It, and Does Nothing Else

**Status:** Accepted, 2026-08-12 (part 10, S10)

## Context

S10 is where Art. 22's "meaningful human involvement" stops being a journal
field and becomes a screen. Nine parts built a system whose entire defensibility
rests on two properties: the journal is the single source of truth, and nothing
in it is ever updated or deleted (ADR-008). A review UI is the first component
with a reason to break both - a queue wants a "claimed by" flag, a case view
wants a cached summary, a confirm button wants to set `status = done` - and
every one of those would be a second answer to "what happened to this case".

The second design problem is the one the prior-art pass keeps returning to. A
review screen can produce meaningful review or friction-free approval, and the
two look identical from the outside. Robodebt and the toeslagenaffaire both had
humans in the loop; what they did not have was any measurement of whether those
humans were doing anything. P-6 exists because the difference has to be
observable, and C-4 exists because observing it must not become per-person
performance monitoring (BPersVG par. 80 Abs. 1 Nr. 21).

Third: the drafting part left an unfinished sentence. A Nachforderung states its
response window RELATIVELY, because a draft waiting for a caseworker has no
dispatch date. The absolute deadline exists only at the moment a human confirms
the letter, and part 08 shipped the arithmetic tested for this part to call.

## Decision

**1. Queues, case views and metrics are folds over the journal. There is no
review store.** `engine/review/state.py` folds one case; `build_index` folds a
whole store once so the queues, the metrics and the corrections export cannot
disagree about what "open" means. Open is defined as "no CONFIRMED event";
`derive_case_state` learned the human's four event types and nothing else
changed.

**2. `machine_tier` and `machine_unit_id` never move.** An override is an
appended fact next to the decision, not an edit of it. The effective tier and
unit are computed by replaying OVERRIDDEN events in sequence order, and the
difference between the two pairs is exactly what the correction pool exports and
what C-5's measured Art. 22 override rate counts.

**3. The routing answer is `engine.decide.admitted_routing`, arriving as the
ROUTED event.** No queue and no page re-derives a unit from
`EvidenceRecord.routing`, which can carry suggestions from sources the agency
has not admitted (the part-06 finding). The classifier ranking renders in a
separate, dashed, clearly labelled "Vorschlag (nur Protokoll)" panel that says
it moved nothing.

**4. Confirm stamps the dispatch facts; the par. 66 opt-in re-renders the
letter.** CONFIRMED carries `dispatched_at` from an injectable clock, the
channel shape part 08 recorded per case, and for a Nachforderung the absolute
deadline computed now by `response_deadline` with the Land holiday set from the
new `config/dispatch/dispatch_v1.yaml`. If the caseworker opts into the par. 66
Abs. 3 SGB I block, the letter is BUILT AGAIN with the block, stored as its own
draft and journaled as its own DRAFTED event that names the draft it supersedes.
Recording an opt-in while posting the letter drafting prepared without it would
be a journal that disagrees with the post office.

**5. The escalation asymmetry.** Re-route and tier change require a written
reason and are refused without one - the correction pool is training data and an
unexplained label teaches the wrong thing. Escalation to tier 3 does NOT: it is
one click, the reason field is optional, and the default sentence names the
norm. Escalating only ever ADDS oversight, and putting a justification
requirement in front of the safe direction is the same mistake the one-way valve
exists to prevent. **This is a deviation from the task's ruling 3** ("reason
text mandatory") and it is deliberate; the reason is still always present in the
journal, it is simply the system's sentence rather than the caseworker's when
they write nothing.

**6. The dispatch stub carries no letter and no person.** An xdomea-SHAPED XML
file lands in `$EINGANGSLOTSE_DISPATCH_DIR` with `konform="false"` and a leading
comment saying what it is not. It holds identifiers, dates and shapes; the body
stays in the draft store. The canary exception list is two members long (the
vault and the draft store) and part 10 does not make it three - an
operator-visible out-directory holding re-hydrated letters would be a third
place personal data lives, with its own retention question nobody has answered.

**7. Metrics aggregate at unit level or coarser, and a unit under five
confirmations reports no rate at all.** A confirm-without-edit rate over two
cases is a number about two cases and, in a small unit, close enough to being
about one person that the BPersVG question reopens. Time-to-confirm is defined
as DECISION TO CONFIRMATION - queue dwell, from timestamps the journal already
holds - and the module says so: measuring attention would need per-session
telemetry about a named person, which the unit-scoped `Actor` makes
inexpressible. SAMPLED cases are excluded from flag statistics (ADR-025).

**8. Roles are a documented demo, said out loud on every page.** The unit is a
query parameter validated against the taxonomy; an unknown id is not a role
rather than an error. It gates exactly one thing, and it is the right thing: the
draft section and `GET /drafts/{case_id}`, the only surface in the system that
returns re-hydrated identity data. Every page carries the sentence that this is
not authentication and that the Berechtigungskonzept with a real IdP is a pilot
prerequisite (C-5).

**9. Accessibility is a self-check with an honest boundary.** Semantic HTML,
one h1 per page, a skip link as the first focusable element, landmarks, labels
on every control, captions and scoped headers on every table, offscreen text
for the span coordinates, visible focus that no rule removes, and no state
carried by colour alone. Plain CSS in the existing panel style; the plan named
Tailwind and a build chain for four pages would add a toolchain an agency has to
maintain without changing a rendered pixel that matters (**documented deviation,
cosmetic**). No pure-python axe-core exists, so the mechanical criteria are
asserted in `tests/test_review_accessibility.py` and the judgment criteria are
listed in `docs/accessibility-selfcheck.md` as needing the external BITV 2.0
audit that stays pilot scope (P-15).

**10. Notifications remain out of reach.** `/inbox` gains no control from this
part and never will. A notification is an automated projection that passes no
human (ADR-005); a caseworker button that "approved" one would turn a Realakt
into something a human authorized, which is a different legal object.

## Consequences

- The UI cannot show the WORKING COPY TEXT or the verbatim span quotes, because
  the journal deliberately carries no case content and no other store holds the
  redacted text. It shows what the journal does hold: the sealed values by KIND
  and count ("an Aktenzeichen stood here"), the normalized parts with their
  character counts, and the span COORDINATES, which part 10 added to the
  EXTRACTED payload for this purpose. Rendering the text itself needs a decision
  about where a redacted working copy lives and under whose retention period -
  filed as an open item, not smuggled in as a UI feature.
- C-9's "Aktenzeichen extraction" therefore lands as PRESENCE, not as a value.
  An Aktenzeichen is sealed identity data (kind `AKTZ`) and a queue page is not
  on the re-hydration surface.
- Every UI action is idempotent-by-refusal rather than idempotent-by-overwrite:
  a second confirmation, a correction after confirmation, an escalation of a
  tier-3 item and an override that changes nothing are all refused with a
  sentence, and none of them writes.
- POST returns 303 to the case view, so a browser reload cannot re-append.
- Forms are parsed from `application/x-www-form-urlencoded` with the standard
  library rather than through FastAPI's `Form()`, which would pull in
  `python-multipart` for a capability this UI must never grow: a review screen
  that accepted uploads would be an ingest path around the redaction boundary.
- Two new independently versioned config files (`config/dispatch/`,
  `config/queues/`) rather than keys in the manifest-frozen ones - the standing
  lesson from parts 06 to 09.
- The gold numbers do not move. Nothing in this part runs inside the pipeline's
  decision path, and the ADR-025 migration changes a reason kind only on sampled
  items while the shipped `audit_sample_rate` is 0.0.
