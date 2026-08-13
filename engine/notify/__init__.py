"""Applicant notifications as journal projections (ADR-005, part 07).

    journal events (RECEIVED, ROUTED)
      -> projection.py   pure fold, deduped on the source event id
      -> render.py       versioned German templates, PII-free by construction
      -> outbox.py       the simulated inbox, in-memory or JSONL
      -> NOTIFIED        back into the journal (informational_only, template_id)

Both messages are informational Realakte: automated end to end, never a
Verwaltungsakt, and they never pass the review UI. Nothing in this package may
state a Rechtsfolge, set a deadline or request a document - that is the
human-confirmed drafting path of part 08, and the config loader refuses a
template that reads like one.
"""

from engine.notify.latency import LatencySample, case_latencies, latency_section
from engine.notify.outbox import (
    OUTBOX_DIR_ENV,
    InMemoryOutbox,
    JsonlOutbox,
    Outbox,
    OutboxEntry,
    default_outbox,
    notification_id_for,
)
from engine.notify.projection import (
    NotifyOutcome,
    OwedNotification,
    notified_source_event_ids,
    notify_case,
    notify_journal,
    owed_notifications,
)
from engine.notify.render import (
    NotificationRenderError,
    RenderedNotification,
    build_context,
    render,
    render_text,
)

__all__ = [
    "OUTBOX_DIR_ENV",
    "InMemoryOutbox",
    "JsonlOutbox",
    "LatencySample",
    "NotificationRenderError",
    "NotifyOutcome",
    "Outbox",
    "OutboxEntry",
    "OwedNotification",
    "RenderedNotification",
    "build_context",
    "case_latencies",
    "default_outbox",
    "latency_section",
    "notification_id_for",
    "notified_source_event_ids",
    "notify_case",
    "notify_journal",
    "owed_notifications",
    "render",
    "render_text",
]
