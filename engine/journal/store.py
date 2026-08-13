"""Journal store protocol and two S1 implementations.

Append-only means three things here, all enforced by the stores rather than by
convention:

* events are keyed by ``case_id`` and carry a monotonic ``sequence`` starting
  at 0,
* a write whose sequence is not exactly the next one is rejected
  (:class:`SequenceConflictError`) - that is optimistic concurrency control and
  it is what makes "the journal is the truth" defensible,
* an ``event_id`` may never be written twice into the same case.

Nothing is ever updated or deleted. Correcting an item means appending a new
event (for example ``OVERRIDDEN``), never rewriting an old one.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from schemas.common import VersionStamp
from schemas.events import Actor, ActorKind, Event, EventType

SYSTEM_ACTOR = Actor(kind=ActorKind.SYSTEM)

_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class SequenceConflictError(RuntimeError):
    """Raised when an append would break the monotonic sequence of a case."""


class DuplicateEventError(RuntimeError):
    """Raised when an event_id is appended twice to the same case."""


@runtime_checkable
class JournalStore(Protocol):
    """The storage contract every journal backend implements."""

    def append(self, event: Event) -> Event:
        """Append one event; raise on a stale sequence or duplicate id."""
        ...

    def read(self, case_id: str) -> list[Event]:
        """All events of a case in sequence order (empty list if unknown)."""
        ...

    def next_sequence(self, case_id: str) -> int:
        """Sequence number the next append for this case must carry."""
        ...

    def case_ids(self) -> list[str]:
        """All case ids known to the store."""
        ...


def make_event(
    *,
    case_id: str,
    event_type: EventType,
    sequence: int,
    versions: VersionStamp,
    payload: dict[str, object] | None = None,
    actor: Actor = SYSTEM_ACTOR,
    occurred_at: datetime | None = None,
    informational_only: bool | None = None,
    template_id: str | None = None,
) -> Event:
    """Build a journal event with a fresh id and a UTC timestamp."""
    return Event(
        event_id=uuid.uuid4().hex,
        case_id=case_id,
        sequence=sequence,
        type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        actor=actor,
        versions=versions,
        payload=payload or {},
        informational_only=informational_only,
        template_id=template_id,
    )


def emit(
    store: JournalStore,
    *,
    case_id: str,
    event_type: EventType,
    versions: VersionStamp,
    payload: dict[str, object] | None = None,
    actor: Actor = SYSTEM_ACTOR,
    occurred_at: datetime | None = None,
    informational_only: bool | None = None,
    template_id: str | None = None,
) -> Event:
    """Append the next event of a case; the store assigns nothing, we do."""
    event = make_event(
        case_id=case_id,
        event_type=event_type,
        sequence=store.next_sequence(case_id),
        versions=versions,
        payload=payload,
        actor=actor,
        occurred_at=occurred_at,
        informational_only=informational_only,
        template_id=template_id,
    )
    return store.append(event)


class InMemoryJournalStore:
    """Process-local store; the default for tests, eval runs and dev."""

    def __init__(self) -> None:
        self._events: dict[str, list[Event]] = {}

    def append(self, event: Event) -> Event:
        events = self._events.setdefault(event.case_id, [])
        _check_appendable(event, events)
        events.append(event)
        return event

    def read(self, case_id: str) -> list[Event]:
        return list(self._events.get(case_id, []))

    def next_sequence(self, case_id: str) -> int:
        return len(self._events.get(case_id, []))

    def case_ids(self) -> list[str]:
        return sorted(self._events)


class JsonlJournalStore:
    """File-backed store: one append-only JSONL file per case.

    Deliberately dumb - open, append one line, close - so that a crash can
    truncate at most the last line and never corrupt earlier history. The
    PostgreSQL event store replaces it when the compose profile lands.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> Event:
        events = self.read(event.case_id)
        _check_appendable(event, events)
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self._path(event.case_id).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return event

    def read(self, case_id: str) -> list[Event]:
        path = self._path(case_id)
        if not path.is_file():
            return []
        events = [
            Event.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return sorted(events, key=lambda event: event.sequence)

    def next_sequence(self, case_id: str) -> int:
        return len(self.read(case_id))

    def case_ids(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.jsonl"))

    def _path(self, case_id: str) -> Path:
        if not _SAFE_CASE_ID.match(case_id):
            raise ValueError(
                f"case_id {case_id!r} is not filesystem-safe; allowed: "
                "letters, digits, dot, underscore, hyphen (max 120 chars)"
            )
        return self.directory / f"{case_id}.jsonl"


def _check_appendable(event: Event, existing: list[Event]) -> None:
    expected = len(existing)
    if event.sequence != expected:
        raise SequenceConflictError(
            f"case {event.case_id}: expected sequence {expected}, got {event.sequence}"
        )
    if any(known.event_id == event.event_id for known in existing):
        raise DuplicateEventError(
            f"case {event.case_id}: event_id {event.event_id} already recorded"
        )
