# ADR-022: Deliver Before Journaling; Idempotence Instead of Exactly-Once

**Status:** Accepted, 2026-08-12

## Context
ADR-005 makes applicant notifications projections of the case journal. A dispatch
therefore touches two stores that cannot be written atomically: the outbox (the
applicant's copy, in a pilot a FIT-Connect callback, an SMTP relay or a print
spooler) and the journal (the NOTIFIED event that records the dispatch). A crash
between the two writes is not exotic - it is the normal failure of any process
that talks to an external channel - and the order decides which of two wrong
states the system can end up in.

Exactly-once delivery is not available here. It would require a transaction
across the journal and a third-party channel, and the channels this system will
actually speak to (an event log, a mail relay, a printer queue) offer no such
thing.

## Options
1. **Journal first, then deliver.** A crash in between leaves a NOTIFIED event
   for a message nobody received. The journal claims something that did not
   happen, and the fold - which dedupes on exactly that event - will never
   retry.
2. **Deliver first, then journal.** A crash in between leaves a delivered
   message with no journal entry. The next run re-derives the same notification,
   re-delivers it, and the outbox recognizes it as one it already holds.
3. **A "sending" state machine** with a pending flag and a reconciliation pass.

## Decision
Option 2, with delivery made idempotent so the retry is a no-op. The notification
id is a pure function of the source event id and the template id
(`notification_id_for`), so a replay computes the same id; both outbox backends
refuse an id they already hold and report it. The worker journals the event
regardless of whether the delivery was new, which is what closes the gap left by
the crash.

Option 1 is rejected on a single ground: the journal is the audit trail, and an
audit trail that records a notification the citizen never got is worse than one
that is briefly incomplete. Option 3 is rejected as a state machine that buys
nothing here - the pending state it would add is exactly the "delivered but not
journaled" window this ordering already recovers from, and it would need its own
durable store to hold it.

## Consequences
- The delivery guarantee is **at-least-once, deduplicated by a deterministic
  id** - stated plainly rather than described as "reliable".
- A backend that cannot deduplicate (a bare SMTP relay) has to hold the ids it
  has sent, or accept that a crash in the window can produce a duplicate. That
  is a property a real adapter must state; the two backends shipped here both
  deduplicate.
- The journal may briefly under-report a dispatch and may never over-report one.
  Any consumer of NOTIFIED events - the latency metric, an Art. 30 record, a
  future SLA report - inherits that direction, and it is the safe one.
- The window is only recoverable while the journal is still readable, so the
  worker must be idempotent over the WHOLE journal rather than over one request.
  It is: `notify_journal` folds every case, and the replay CLI is that fold with
  a directory in front of it.
