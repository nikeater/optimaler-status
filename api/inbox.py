"""The simulated applicant inbox, server-rendered.

The demo's "applicant view": what a citizen would have received, as plain HTML
in the metrics-panel style. It reads the outbox and computes nothing, exactly as
``api/metrics.py`` reads the eval report and computes nothing - the page can
never disagree with what was actually delivered.

Read-only on purpose. There is no send button, no edit box and no confirmation
step, because a notification on this path never passes a human (ADR-005): it is
a projection of the journal, and a UI that let somebody change one would make it
something else. The human-confirmed path is part 08 and it is a different
surface entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.i18n import PageContext
from api.metrics import render_template
from engine.notify.outbox import Outbox, OutboxEntry


@dataclass(frozen=True)
class InboxCase:
    """One case's messages, newest last (the order they were sent)."""

    case_id: str
    entries: list[OutboxEntry]


@dataclass(frozen=True)
class InboxView:
    """Everything the inbox template needs."""

    cases: list[InboxCase]
    outbox_kind: str

    @property
    def message_count(self) -> int:
        return sum(len(case.entries) for case in self.cases)


def build_view(outbox: Outbox, *, case_id: str | None = None) -> InboxView:
    """The view over one case, or over everything the outbox holds."""
    case_ids = [case_id] if case_id is not None else outbox.case_ids()
    return InboxView(
        cases=[
            InboxCase(case_id=known, entries=outbox.entries(known))
            for known in case_ids
        ],
        outbox_kind=type(outbox).__name__,
    )


def render_page(view: InboxView, page: PageContext | None = None) -> str:
    """The whole inbox page."""
    return render_template("inbox.html", view, page)


def as_payload(entry: OutboxEntry) -> dict[str, Any]:
    """One entry as the JSON API returns it."""
    return entry.model_dump(mode="json")
