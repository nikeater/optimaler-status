# Applicant Notifications: Channels, Formality, and What They Are Not

Compliance input for C-8 (Schriftform channel split), C-13 (Art. 50 AI Act) and
P-14 (FIT-Connect acknowledgement semantics), plus the Art. 13/14 notice block
that C-5 asks the receipt to carry. Written alongside the part-07 implementation
in `eingangslotse/engine/notify/` and
`eingangslotse/config/notifications/notifications_v1.yaml`.

Companion documents: `docs/vault-dpia-input.md` (the identity vault and the
sealed-field table), `docs/adr/ADR-005-notifications-as-journal-projections.md`
(why notifications are journal projections at all),
`docs/adr/ADR-022-notification-dispatch-ordering.md` (the delivery guarantee).

## 1. What this path sends, and what it may never send

Exactly two messages exist, both derived from the case journal:

| Trigger event | Template | Content |
|---|---|---|
| `received` | `eingangsbestaetigung_v1` | Eingangsbestaetigung: case id, arrival timestamp, channel, the procedure's display name when one was derived, and the Art. 13/14 notice block |
| `routed` | `zuordnung_v1` | Zwischenstand: case id, timestamp, the public name of the responsible organizational unit |

Both are **informationelle Realakte**. They regulate nothing, decide nothing and
create no legal effect, which is what makes full automation defensible: a
Verwaltungsakt would need the human-confirmed path of ADR-003, and it does not
travel here.

Three things this path may therefore never do, in descending order of how easy
they would be to slip in:

1. **Request a document.** A Nachforderung engages par. 66 SGB I duties, and a
   request made informally that a citizen then does not answer produces a
   consequence somebody has to justify. Requests go through the drafting path
   (part 08), which is prepared by the system and confirmed by a human.
2. **State a deadline.** Same reason, more directly: a date in an outbound
   message is read as a deadline whether it was meant as one or not.
3. **State a Rechtsfolge.** That is a Verwaltungsakt by content, whatever the
   channel calls it.

Two controls hold the line, and neither of them is a comment:

* **Topology.** Nothing on this path can produce a request: the worker renders
  from a fixed set of templates and never sees the drafting machinery. The
  NOTIFIED event carries `informational_only=True`, enforced by the contract
  validator in `schemas/events.py` - an event that claims otherwise cannot be
  constructed.
* **A loader tripwire.** `NotificationsConfig` refuses a template whose wording
  contains par. 66 trigger words ("Mitwirkungspflicht", "Frist", "Rechtsfolge",
  "vorzulegen", ...) or a date-shaped literal. It is a cheap string check, it is
  documented as one in the code, and it cannot decide whether a sentence creates
  a legal consequence. Its job is to catch the edit where somebody pastes a
  helpful-sounding request sentence into a receipt.

## 2. C-8: the channel/formality mapping

The inbound channel decides the outbound shape. All three shapes are
informational; nothing with procedural consequence leaves this way.

| Inbound channel | Outbound `delivery` | What a real adapter does | Formality |
|---|---|---|---|
| `fit_connect` | `status_event` | writes a status event into the submission's event log ("accepted by destination") | Realakt; the protocol's own acknowledgement semantics, no Zustellung in the legal sense |
| `email` | `mail` | plain-text e-mail to the sender address | Realakt; formless, no Zustellung, no Zugangsfiktion relied on |
| `scan` | `postal_stub` | a print job (part 08+) | Realakt on paper. The paper journey has no digital back-channel, and that limitation is stated rather than worked around |

Anything with procedural consequence - a par. 66(3) warning, a Nachforderung, a
Verwaltungsakt - leaves through **print or a qualified electronic form**
(par. 36a SGB I), which is part 08+ work and a different code path. The
enforcement point for the split is the `informational_only` flag on the NOTIFIED
event: every message this path produces carries it, and a message that needed to
be formal could not be produced here at all.

**What is done (07):** the mapping exists in config, is validated at load time
(every inbound channel must be mapped, or an item that arrived on it could not
be answered), is recorded per message in the outbox and in the journal payload,
and is documented here. **What is open:** the print path itself and the
qualified-electronic path, both part 08+.

## 3. C-13: Art. 50 AI Act on this path

**Zero model-generated text exists in an applicant notification.** Every word
comes from `config/notifications/notifications_v1.yaml`, which an agency edits
and versions; the only values substituted into it are the case id, journal
timestamps, and display names that come from the config files themselves. No
LLM is called anywhere in `engine/notify/`, and both rendered texts say so in
their closing paragraph.

Art. 50's transparency obligation for AI-generated text is therefore satisfied
trivially here: there is no AI-generated text to disclose. The Art. 50(4)
carve-out for content under documented human editorial review is not needed and
not relied on.

**This is the whole of C-13's 07 slice, and it is deliberately the easy half.**
The question moves to part 08, where the LLM does draft outbound text: there
the carve-out has to be earned with a documented human editorial review step,
and the review must be real - a confirm button that a caseworker presses without
reading is not editorial review, and the eval has to be able to tell the
difference (measured override rates, C-5's part-10 slice).

The optional paraphrase pass of ADR-012 touches only the synthetic corpus and
never a citizen-facing message.

## 4. P-14: the receipt as a protocol-conformant acknowledgement

FIT-Connect's submission API is event-log shaped: a subscriber acknowledges a
submission by appending an event to its log, and the sender's client reads that
log to learn what happened to what it sent. The states that matter for an
inbound-processing system are "accepted by destination" and "rejected by
destination".

The instant receipt maps onto that directly:

* it is triggered by the journal's own `received` event, which is written after
  ingest has validated and sealed the submission - so "accepted" means the same
  thing to the destination system and to the applicant;
* it carries the case id, which is derived from the submission id
  (`case_id_for`), so the acknowledgement can be correlated back to the exact
  submission without a lookup table;
* it is idempotent (ADR-022), which an event log requires: appending the same
  acknowledgement twice must not mean two acknowledgements.

The **rejection** side is genuinely absent: an item the redaction boundary
refuses produces a 422 and no case at all, so there is nothing to acknowledge
and no NOTIFIED event. A real adapter would have to map that refusal onto a
"rejected by destination" event, and it should - a submission that vanishes is
worse than one that is rejected loudly. That is adapter work.

**What is done (07):** the acknowledgement semantics, the correlation key and
the idempotence requirement are documented and implemented; the `status_event`
delivery shape is recorded per message. **What is open:** the real FIT-Connect
adapter (pilot scope), including the rejection path and the client-side event
log; and KE-1's OCR-confidence-at-source note, which is scan-adapter work that
part 07 does not touch.

## 5. C-5 (07 slice): the Art. 13/14 notice block

The receipt carries the notice block. Every controller-specific item is a
clearly marked placeholder in square brackets - `[Platzhalter Behoerde: ...]` -
so an unfilled one is visible to a reader rather than silently absent. They are
deliberately NOT the reserved `[[PII|KIND|TOKEN]]` syntax: nothing on this path
is ever re-hydrated from the vault, and reusing the syntax would blur the one
distinction the redaction boundary rests on.

Items in the block: controller identity and address, the data protection
officer's contact, purpose of processing, legal basis, recipients, storage
period, the data subject's rights (access, rectification, erasure, restriction,
portability, objection, complaint) and the supervisory authority.

Four of these are **controller decisions, not engineering decisions**, and they
stay in section 9 of `docs/vault-dpia-input.md` as open items for the design
partner: the legal basis, the storage period, the supervisory authority and the
controller's own identity. Shipping the block with a plausible-looking guess
would be worse than shipping it with placeholders, because a plausible-looking
guess is the kind of thing that survives review.

**What is done (07):** the notice block exists, is versioned with the wording it
sits in, and is asserted by test. **What is open:** the controller items above,
the Art. 30 record, and the measured Art. 22 override rates (part 10).

## 6. What a reader can verify

* `eingangslotse/config/notifications/notifications_v1.yaml` - the whole of the
  wording, the channel mapping and the display names.
* `eingangslotse/tests/golden/notification_*.txt` - the two rendered German
  texts exactly as shipped, byte-frozen.
* `eingangslotse/tests/test_notify.py` - the informational boundary in the
  rendered output, the Art. 13/14 items, the "no language model" statement, the
  idempotence property and the loader refusals.
* `eingangslotse/tests/test_redact_canaries.py` - that no seeded identity value
  reaches the outbox, the NOTIFIED payloads, `GET /inbox` or the outbox files on
  disk.
* `GET /inbox` on a running instance - what an applicant would have received.
