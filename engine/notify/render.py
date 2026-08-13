"""Template rendering, and the assertion that keeps this path PII-free.

**Ruling 2 of part 07, spelled as code.** There is no vault re-hydration on the
notification path. Not "we do not do it yet" as a habit - the render context is
built by :func:`build_context` out of exactly three sources, and none of them can
carry identity data:

* the case id and the journal timestamps,
* names the CONFIG supplies (procedure display name, channel display name),
* the routed unit's public name from ``config/taxonomy/``.

The submission is never read. That is the important sentence: a receipt that
echoed ``procedureHint`` back at the applicant would be echoing applicant-
controlled text through a channel the redaction boundary spent part 04 sealing,
and an id whose display name is unknown therefore renders as nothing at all
rather than as itself.

The belt to that suspenders is :func:`render_text`, which refuses to return a
string containing a placeholder or anything imitating the reserved syntax. It
uses ``engine.redact.placeholders`` - the single definition (part 04) - so
"what is a placeholder" cannot drift between the code that mints them and the
code that refuses them. Today nothing can put one in a notification; the check
is what makes that still true after part 08 adds a re-hydrator next door.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import jinja2

from engine.config_loader import ConfigBundle, NotificationTemplate
from engine.redact.placeholders import PLACEHOLDER_SHAPED_RE, contains_placeholder

#: How a journal timestamp is written for an applicant. German order, minute
#: resolution, and the zone spelled out - a receipt that said "09:00" without
#: saying which 09:00 would be a support ticket.
TIMESTAMP_FORMAT = "%d.%m.%Y, %H:%M Uhr"


class NotificationRenderError(RuntimeError):
    """Raised when a notification could not be rendered safely."""


@dataclass(frozen=True)
class RenderedNotification:
    """One rendered message: subject and body, both plain text."""

    template_id: str
    subject: str
    body: str


def environment() -> jinja2.Environment:
    """The Jinja environment for notification text.

    Autoescape is OFF and must be: these are plain-text messages, and escaping
    them would put ``&amp;`` in front of an applicant. The inbox page escapes
    them again on the way into HTML (``ui/templates/inbox.html``).

    ``undefined=StrictUndefined`` turns a typo in a template into a loud render
    error instead of an empty space in a citizen's receipt.
    """
    return jinja2.Environment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def format_timestamp(moment: datetime | None) -> str:
    """A journal timestamp as an applicant reads it, or an empty string."""
    if moment is None:
        return ""
    return f"{moment.strftime(TIMESTAMP_FORMAT)} ({moment.tzname() or 'UTC'})"


def build_context(
    *,
    case_id: str,
    config: ConfigBundle,
    received_at: datetime | None = None,
    routed_at: datetime | None = None,
    channel_id: str | None = None,
    procedure_id: str | None = None,
    unit_id: str | None = None,
) -> dict[str, Any]:
    """Everything a template may reference, and nothing else.

    Every name resolves through config: an unknown procedure id yields an empty
    ``procedure_name`` (the template drops the line), an unknown channel or unit
    yields the configured neutral fallback. Nothing here reads the submission,
    the envelope, the extraction set or the vault.
    """
    notifications = config.notifications
    if notifications is None:  # pragma: no cover - callers check first
        raise NotificationRenderError("no notification config is loaded")
    channel = notifications.channel(channel_id)
    unit = config.unit(unit_id) if unit_id is not None else None
    return {
        "case_id": case_id,
        "received_at": format_timestamp(received_at),
        "routed_at": format_timestamp(routed_at),
        "channel_name": (
            channel.display_name
            if channel is not None
            else notifications.fallback_channel_name
        ),
        "procedure_name": notifications.procedure_names.get(procedure_id or "", ""),
        "unit_name": (
            unit.name if unit is not None else notifications.fallback_unit_name
        ),
    }


def render_text(text: str, context: dict[str, Any], *, label: str) -> str:
    """Render one template string and prove the result carries no placeholder.

    The refusal is deliberately a hard error rather than a redaction: a message
    that reached this point holding sealed content is a bug in the context
    builder, and quietly stripping it would hide the bug while still sending
    something to a citizen.
    """
    try:
        rendered = environment().from_string(text).render(**context)
    except jinja2.UndefinedError as error:
        raise NotificationRenderError(
            f"{label}: template references an unknown name ({error.message})"
        ) from error
    if contains_placeholder(rendered) or PLACEHOLDER_SHAPED_RE.search(rendered):
        raise NotificationRenderError(
            f"{label}: the rendered text carries a redaction placeholder. "
            "Nothing on the notification path may be re-hydrated (ADR-005, "
            "ADR-002); this is a bug in the render context, not a formatting "
            "problem, and the message is not sent."
        )
    return _tidy(rendered)


def render(
    template: NotificationTemplate, context: dict[str, Any]
) -> RenderedNotification:
    """Render one template's subject and body."""
    return RenderedNotification(
        template_id=template.template_id,
        subject=render_text(
            template.subject, context, label=f"{template.template_id}.subject"
        ),
        body=render_text(template.body, context, label=f"{template.template_id}.body"),
    )


def _tidy(text: str) -> str:
    """Collapse the blank-line debris a dropped ``{% if %}`` line leaves behind.

    Purely cosmetic and deliberately dumb: it never removes content, only runs
    of three or more newlines, so a template's own paragraph breaks survive.
    """
    lines = [line.rstrip() for line in text.strip().splitlines()]
    tidied: list[str] = []
    for line in lines:
        if not line and tidied and not tidied[-1]:
            continue
        tidied.append(line)
    return "\n".join(tidied)
