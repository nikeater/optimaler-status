"""Which notifications does a case owe? A pure fold over its event list.

The naming follows ``engine/journal/projection.py`` because this IS the same
kind of object: a fold over the append-only journal that produces derived state
and never a second source of truth. ADR-005 fixes the two triggers - "received"
owes the instant receipt, "routed" owes the status update - and this module is
where that sentence becomes executable.

**The property of this part (ruling 1): running it twice emits nothing new.**
:func:`owed_notifications` answers "what does this case owe" from the event list
ALONE, and subtracts what the journal already records as sent by matching on the
source event id carried in each NOTIFIED payload. Replaying a whole journal
directory is therefore a no-op, and so is a worker that ran, crashed after
delivering, and ran again. Two things make that true rather than likely:

* the notification id is a function of the source event id and the template id
  (``engine/notify/outbox.py``), so it is identical on every replay, and
* nothing in the fold reads a clock, a counter or a random source.

**Nothing here writes any event type but NOTIFIED**, and every NOTIFIED it
writes carries ``informational_only=True`` and a ``template_id`` - both required
by the contract validator in ``schemas/events.py`` since part 01. The payload
carries the source event id, the channel, the delivery shape and the
notifications config version; it carries NO message text. The applicant's copy
lives in the outbox, the journal records that a dispatch happened.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.config_loader import ConfigBundle
from engine.journal.store import JournalStore, emit
from engine.notify.outbox import Outbox, OutboxEntry, notification_id_for
from engine.notify.render import build_context, render
from schemas.events import Event, EventType

#: The journal event types that owe a notification, keyed by the ``trigger``
#: string a template declares in config. Read off the contract enum so a config
#: trigger and a journal event type cannot drift apart.
TRIGGER_EVENT_TYPES: dict[str, EventType] = {
    "received": EventType.RECEIVED,
    "routed": EventType.ROUTED,
}


@dataclass(frozen=True)
class OwedNotification:
    """One notification a case owes but has not been recorded as sending."""

    case_id: str
    template_id: str
    source_event_id: str
    source_event_type: str
    source_event_at: datetime
    channel_id: str | None
    procedure_id: str | None
    unit_id: str | None


@dataclass(frozen=True)
class NotifyOutcome:
    """What one worker run did to one case."""

    case_id: str
    delivered: tuple[OutboxEntry, ...] = ()
    events: tuple[Event, ...] = ()
    skipped: int = 0

    @property
    def count(self) -> int:
        """How many notifications this run actually sent."""
        return len(self.events)


def notified_source_event_ids(events: Iterable[Event]) -> frozenset[str]:
    """Source event ids the journal already records a NOTIFIED for.

    Read defensively, like every other projection in this repo: a NOTIFIED whose
    payload lost its source id degrades to "not recorded", which re-sends at
    most one message. The alternative - raising - would take the case view down
    over a malformed payload, and a rendering bug may not break the audit trail.
    """
    return frozenset(
        source_id
        for event in events
        if event.type is EventType.NOTIFIED
        for source_id in [event.payload.get("source_event_id")]
        if isinstance(source_id, str)
    )


def owed_notifications(
    events: Sequence[Event], *, config: ConfigBundle
) -> tuple[OwedNotification, ...]:
    """Every notification this event list owes and has not been sent.

    Pure: no store is read, no clock is called, nothing is written. Order is the
    journal's own sequence order, so a replay produces the same list in the same
    order every time.
    """
    notifications = config.notifications
    if notifications is None:
        return ()
    ordered = sorted(events, key=lambda event: event.sequence)
    already = notified_source_event_ids(ordered)
    state = _fold(ordered)
    owed: list[OwedNotification] = []
    for event in ordered:
        template = _template_for(event, config=config)
        if template is None or event.event_id in already:
            continue
        owed.append(
            OwedNotification(
                case_id=event.case_id,
                template_id=template,
                source_event_id=event.event_id,
                source_event_type=event.type.value,
                source_event_at=event.occurred_at,
                channel_id=state["channel"],
                procedure_id=state["procedure_id"],
                unit_id=(
                    _as_str(event.payload.get("unit_id"))
                    if event.type is EventType.ROUTED
                    else state["unit_id"]
                ),
            )
        )
    return tuple(owed)


def notify_case(
    events: Sequence[Event],
    *,
    config: ConfigBundle,
    journal: JournalStore,
    outbox: Outbox,
    now: datetime | None = None,
) -> NotifyOutcome:
    """Deliver and journal everything one case owes. Idempotent by construction.

    Deliver first, journal second - see ``engine/notify/outbox.py`` for why that
    order and not the other one.
    """
    notifications = config.notifications
    owed = owed_notifications(events, config=config)
    if notifications is None or not owed:
        return NotifyOutcome(case_id=_case_id(events))
    delivered: list[OutboxEntry] = []
    written: list[Event] = []
    skipped = 0
    for item in owed:
        template = notifications.template(item.template_id)
        if template is None:  # pragma: no cover - the loader guarantees it exists
            continue
        channel = notifications.channel(item.channel_id)
        rendered = render(
            template,
            build_context(
                case_id=item.case_id,
                config=config,
                received_at=(
                    item.source_event_at
                    if item.source_event_type == "received"
                    else _received_at(events)
                ),
                routed_at=item.source_event_at,
                channel_id=item.channel_id,
                procedure_id=item.procedure_id,
                unit_id=item.unit_id,
            ),
        )
        entry = OutboxEntry(
            notification_id=notification_id_for(item.source_event_id, item.template_id),
            case_id=item.case_id,
            channel=item.channel_id or "unknown",
            delivery=channel.delivery if channel is not None else "unknown",
            template_id=item.template_id,
            source_event_id=item.source_event_id,
            source_event_type=item.source_event_type,
            subject=rendered.subject,
            body=rendered.body,
            created_at=now or item.source_event_at,
        )
        if not outbox.deliver(entry):
            # Already in the outbox but not in the journal: a crash between the
            # two writes. Journal it now rather than dropping it - that is the
            # whole point of the deliver-first order.
            skipped += 1
        else:
            delivered.append(entry)
        written.append(
            emit(
                journal,
                case_id=item.case_id,
                event_type=EventType.NOTIFIED,
                versions=config.version_stamp(),
                occurred_at=now,
                informational_only=True,
                template_id=item.template_id,
                payload={
                    "notification_id": entry.notification_id,
                    "source_event_id": item.source_event_id,
                    "source_event_type": item.source_event_type,
                    "channel": entry.channel,
                    "delivery": entry.delivery,
                    "notifications_version": notifications.version,
                    # Length, never text: the applicant's copy lives in the
                    # outbox and the journal records that a dispatch happened.
                    "body_chars": len(rendered.body),
                },
            )
        )
    return NotifyOutcome(
        case_id=owed[0].case_id,
        delivered=tuple(delivered),
        events=tuple(written),
        skipped=skipped,
    )


def notify_journal(
    *,
    config: ConfigBundle,
    journal: JournalStore,
    outbox: Outbox,
    now: datetime | None = None,
) -> tuple[NotifyOutcome, ...]:
    """Run the worker over every case a journal holds. Replay-safe."""
    return tuple(
        notify_case(
            journal.read(case_id),
            config=config,
            journal=journal,
            outbox=outbox,
            now=now,
        )
        for case_id in journal.case_ids()
    )


def _template_for(event: Event, *, config: ConfigBundle) -> str | None:
    """The template id this event owes, or None."""
    notifications = config.notifications
    if notifications is None:  # pragma: no cover - callers check first
        return None
    for trigger, event_type in TRIGGER_EVENT_TYPES.items():
        if event.type is event_type:
            template = notifications.template_for(trigger)
            return template.template_id if template is not None else None
    return None


def _fold(events: Sequence[Event]) -> dict[str, Any]:
    """The three facts a notification may name, folded out of the event list.

    Its own fold rather than :func:`engine.journal.projection.derive_case_state`
    on purpose: this one reads exactly the keys a template may reference and
    nothing else, so a future field on the case view cannot become reachable
    from a citizen-facing text by accident.
    """
    state: dict[str, Any] = {"channel": None, "procedure_id": None, "unit_id": None}
    for event in events:
        if event.type is EventType.RECEIVED:
            state["channel"] = _as_str(event.payload.get("channel"))
        elif event.type is EventType.EVIDENCE_ASSEMBLED:
            procedure = event.payload.get("procedure")
            if isinstance(procedure, dict):
                state["procedure_id"] = _as_str(procedure.get("procedure_id"))
        elif event.type is EventType.ROUTED:
            state["unit_id"] = _as_str(event.payload.get("unit_id"))
    return state


def _received_at(events: Sequence[Event]) -> datetime | None:
    for event in events:
        if event.type is EventType.RECEIVED:
            return event.occurred_at
    return None


def _case_id(events: Sequence[Event]) -> str:
    return events[0].case_id if events else ""


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
