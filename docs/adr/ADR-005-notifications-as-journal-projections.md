# ADR-005: Applicant Notifications as Informational Journal Projections

**Status:** Accepted, 2026-08-10

## Context
Citizens report processing-status opacity as a top pain point. Receipt confirmations and routing status updates regulate nothing and create no legal effect: they are Realakte, not Verwaltungsakte, and may be fully automated. Anything with procedural consequence must stay human-confirmed (ADR-003).

## Options
1. Send notifications from the review UI or pipeline stages ad hoc.
2. A projection worker turns journal events into notifications: received -> instant receipt; routed -> status update.

## Decision
Option 2. Notifications are projections of the append-only case journal, automated end to end, marked `informational_only=True` in the NOTIFIED event (schema-enforced), with identity re-hydrated in code at template render. Notifications never pass through the review UI. The demo channel is a simulated inbox pane; pilot channels are FIT-Connect status callbacks or email.

## Consequences
- The Realakt/Verwaltungsakt legal line is enforced by the event schema and the pipeline topology, not by convention.
- The paper journey has no digital back-channel; that limitation is stated honestly rather than worked around.
- Cost: notification latency is journal-projection latency (acceptable: seconds).
