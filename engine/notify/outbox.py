"""The simulated applicant inbox: an outbox store behind a protocol.

Same shape as the journal (ADR-008) and the identity vault (ADR-018): one
protocol, an in-memory backend for tests and dev, a JSONL backend when an
operator points ``EINGANGSLOTSE_OUTBOX_DIR`` somewhere. A real deployment
replaces the backend with a FIT-Connect status callback, an SMTP relay or a
print spooler; nothing above this module knows which.

**Delivery is idempotent, and that is load-bearing.** :meth:`deliver` returns
False for an entry whose ``notification_id`` the store already holds, and the
id is derived from the source event id and the template id rather than drawn
fresh - so it is the SAME id on every replay. The projection worker (ruling 1)
dedupes against the journal; the outbox dedupes again against itself. Two
independent guards, because the failure this part must not have is the one where
a replayed journal sends a citizen the same receipt a second time.

Ordering the worker relies on: deliver FIRST, journal SECOND. A crash between
them leaves a delivered message with no NOTIFIED event, and the next run
re-derives the same notification, re-delivers it (a no-op, by the id above) and
writes the missing event. The other order would leave a NOTIFIED event claiming
a dispatch that never happened, and the journal may not contain that claim.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from schemas.common import StrictModel

OUTBOX_DIR_ENV = "EINGANGSLOTSE_OUTBOX_DIR"

_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class OutboxEntry(StrictModel):
    """One outbound message, as the applicant would receive it.

    Carries the rendered text, because this IS the applicant's copy. It carries
    no identity data all the same: the renderer refuses any body holding a
    placeholder, and every name in the text comes from config rather than from
    the submission (engine/notify/render.py).
    """

    notification_id: str
    case_id: str
    channel: str
    delivery: str
    template_id: str
    source_event_id: str
    source_event_type: str
    subject: str
    body: str
    created_at: datetime


def notification_id_for(source_event_id: str, template_id: str) -> str:
    """The stable id of the notification one journal event owes.

    A function of the two things that identify it and of nothing else - no
    counter, no uuid, no clock - so a replay computes the same id and the
    idempotence of the whole path is arithmetic rather than bookkeeping.
    """
    return f"{source_event_id}-{template_id}"


@runtime_checkable
class Outbox(Protocol):
    """The storage contract every outbox backend implements."""

    def deliver(self, entry: OutboxEntry) -> bool:
        """Store one entry; False when its notification_id is already there."""
        ...

    def entries(self, case_id: str) -> list[OutboxEntry]:
        """Every entry of a case, oldest first (empty list if unknown)."""
        ...

    def case_ids(self) -> list[str]:
        """All case ids this outbox holds, sorted."""
        ...


class InMemoryOutbox:
    """Process-local outbox; the default for tests, eval runs and dev."""

    def __init__(self) -> None:
        self._entries: dict[str, list[OutboxEntry]] = {}

    def deliver(self, entry: OutboxEntry) -> bool:
        entries = self._entries.setdefault(entry.case_id, [])
        if any(known.notification_id == entry.notification_id for known in entries):
            return False
        entries.append(entry)
        return True

    def entries(self, case_id: str) -> list[OutboxEntry]:
        return list(self._entries.get(case_id, []))

    def case_ids(self) -> list[str]:
        return sorted(self._entries)


class JsonlOutbox:
    """File-backed outbox: one append-only JSONL file per case.

    Deliberately dumb, for the journal's reason: a crash can truncate at most
    the last line and never corrupt an earlier delivery.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def deliver(self, entry: OutboxEntry) -> bool:
        if any(
            known.notification_id == entry.notification_id
            for known in self.entries(entry.case_id)
        ):
            return False
        line = json.dumps(entry.model_dump(mode="json"), ensure_ascii=False)
        with self._path(entry.case_id).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True

    def entries(self, case_id: str) -> list[OutboxEntry]:
        path = self._path(case_id)
        if not path.is_file():
            return []
        return [
            OutboxEntry.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def case_ids(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.jsonl"))

    def _path(self, case_id: str) -> Path:
        if not _SAFE_CASE_ID.match(case_id):
            raise ValueError(
                f"case_id {case_id!r} is not filesystem-safe; allowed: "
                "letters, digits, dot, underscore, hyphen (max 120 chars)"
            )
        return self.directory / f"{case_id}.jsonl"


def default_outbox() -> Outbox:
    """In-memory outbox, or a JSONL one when the env var points somewhere.

    Mirrors ``api.app.default_journal`` and ``default_vault`` deliberately: three
    stores with the same lifecycle should not need three conventions.
    """
    directory = os.environ.get(OUTBOX_DIR_ENV)
    if directory:
        return JsonlOutbox(directory)
    return InMemoryOutbox()
