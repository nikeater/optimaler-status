"""The demo-only, in-memory, TTL-bounded store behind the glass-pipeline view.

**This is not the answer to ADR-026's open question, and it must not be read as
one.** Part 10 could not show the working-copy TEXT on the review UI because the
journal deliberately carries no case content and no other store holds the
redacted text; rendering it needs a decision about where a redacted working copy
lives and under whose retention period, and that decision is still a pre-pilot
item. What this module does is narrower and lives entirely inside the demo: it
holds, for the handful of submissions a VISITOR made in the last half hour, the
two strings the guided tour needs to put side by side. A production deployment
gets no store from this file; the open item stays open.

## The two compartments, and where each one may come from

``working_copy``
    What the pipeline saw. Built by :meth:`DemoSubmission.from_envelope` from
    an :class:`~schemas.envelope.Envelope` and from nothing else, which is the
    structural half of "placeholders in, sealed values never": the envelope's
    documented invariant is that it carries only redacted content, so a value
    the boundary sealed cannot arrive here without first defeating the
    boundary. There is no vault parameter on any function in this module, and
    there is no import of the vault anywhere in it.

``echo``
    What the VISITOR typed, taken off their own HTTP request and from nowhere
    else. It exists for one moment of the tour - "your name became this token" -
    and that moment is only honest with the value the visitor themselves just
    entered. It is never obtained by unsealing anything: not from the vault
    (ADR-002 keeps that shut until outbound rendering), not from the transient
    witness (ADR-017 keeps that inside one pipeline call), and not from a
    journal payload (which never carries content).

## What bounds it

* **RAM only.** Nothing here is ever written to a file. The five file-backed
  stores of ADR-027 survive a process; this one does not, which makes a restart
  a complete wipe by construction rather than by maintenance - the same
  argument ADR-027 makes for the reset, one step stronger.
* **A TTL**, :data:`DEFAULT_TTL`, swept on every read and every write.
* **A capacity**, :data:`DEFAULT_CAPACITY`, oldest evicted first, so a demo
  nobody stops cannot grow.
* **A per-entry size cap**, :data:`MAX_CHARS` per string, so one visitor with a
  large paste cannot become the memory profile of the process.
* **Demo mode.** The store is constructed only when the posture is on
  (``api/app.py``); with the flag off there is no store object at all, which is
  asserted in ``tests/test_demo_journey.py`` rather than assumed.

## What it deliberately does NOT hold

The span coordinates, the sealed-kind counts, the extraction verdicts, the
routing evidence and the decision are all in the JOURNAL already, and the
pipeline view reads them from there through the existing projections. Holding a
second copy here would be a second answer to "what happened to this case", which
is the one thing ADR-008 and ADR-026 both refuse.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from schemas.envelope import Envelope

#: How long a demo submission stays visible. Long enough to walk the three
#: phases without hurrying, short enough that a page left open in a browser
#: overnight shows the tour's expiry sentence rather than yesterday's data.
DEFAULT_TTL = timedelta(minutes=30)

#: How many submissions the store keeps at once. A visitor makes a handful; a
#: crawler making thousands evicts its own earlier ones and nothing else.
DEFAULT_CAPACITY = 64

#: Per-string cap. The intake form enforces the same bound before submitting,
#: so this is the second of two locks rather than the only one.
MAX_CHARS = 8000


@dataclass(frozen=True)
class TypedValue:
    """One value the visitor typed, with the placeholder kind it became.

    ``kind`` is the persona field's declared kind (``engine/demo/personas.py``)
    and is used only to pair this value with a placeholder on the page. It
    never decides what is sealed: that is the redaction policy's job and a
    second opinion here would be a second redaction policy.
    """

    label: str
    value: str
    kind: str


@dataclass(frozen=True)
class WorkingCopyPart:
    """One part of the working copy, as the envelope carries it."""

    part_id: str
    #: "text" for a free-text part, "structured" for a payload part.
    shape: str
    text: str


@dataclass(frozen=True)
class DemoSubmission:
    """One visitor submission, in the two shapes the tour needs."""

    case_id: str
    persona_id: str
    persona_label: str
    channel: str
    created_at: datetime
    working_copy: tuple[WorkingCopyPart, ...] = ()
    echo: tuple[TypedValue, ...] = ()
    #: The prose the visitor wrote, for the e-mail tab's before/after. Empty
    #: for the form tab, where the fields ARE the submission.
    echo_body: str = ""

    @classmethod
    def from_envelope(
        cls,
        envelope: Envelope,
        *,
        persona_id: str,
        persona_label: str,
        channel: str,
        created_at: datetime,
        echo: Iterable[TypedValue] = (),
        echo_body: str = "",
    ) -> DemoSubmission:
        """Build an entry whose working copy comes off the envelope alone.

        The one constructor, on purpose. Every string in ``working_copy`` is
        read from ``Envelope.parts``, which by contract carries only redacted
        content - so "the store never holds a sealed value" is a property of
        where the data comes from rather than a check somebody has to remember
        to run.
        """
        return cls(
            case_id=envelope.case_id,
            persona_id=persona_id,
            persona_label=persona_label,
            channel=channel,
            created_at=created_at,
            working_copy=tuple(_parts(envelope)),
            echo=tuple(
                TypedValue(label=item.label, value=_clip(item.value), kind=item.kind)
                for item in echo
            ),
            echo_body=_clip(echo_body),
        )

    def expired(self, now: datetime, ttl: timedelta) -> bool:
        return now - self.created_at >= ttl


class DemoStore:
    """A tiny in-memory map of case id to :class:`DemoSubmission`.

    Not a protocol implementation and deliberately not one: the journal, the
    vault, the outbox and the draft store all have a Protocol and a second
    file-backed implementation because they are part of the system. This has
    neither, because a second implementation of it would be the beginning of
    the production answer that ADR-026's open item has not decided yet.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = DEFAULT_TTL,
        capacity: int = DEFAULT_CAPACITY,
    ) -> None:
        self._ttl = ttl
        self._capacity = max(1, capacity)
        self._entries: OrderedDict[str, DemoSubmission] = OrderedDict()

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    @property
    def capacity(self) -> int:
        return self._capacity

    def put(self, submission: DemoSubmission, *, now: datetime | None = None) -> None:
        """Store one submission, sweeping the expired and the surplus."""
        self._sweep(now or datetime.now(UTC))
        self._entries[submission.case_id] = submission
        self._entries.move_to_end(submission.case_id)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def get(
        self, case_id: str, *, now: datetime | None = None
    ) -> DemoSubmission | None:
        """The submission, or None when it never existed or has expired.

        Expiry is indistinguishable from absence on purpose: the pipeline view
        says "this submission is no longer held" for both, because the
        difference is not a fact about the visitor's case and inventing one
        would mean keeping a record of what was dropped.
        """
        self._sweep(now or datetime.now(UTC))
        return self._entries.get(case_id)

    def reset(self) -> None:
        """Drop everything. A restart does the same thing by construction."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def case_ids(self) -> tuple[str, ...]:
        """Oldest first, which is the order the capacity evicts in."""
        return tuple(self._entries)

    def _sweep(self, now: datetime) -> None:
        for case_id in [
            case_id
            for case_id, entry in self._entries.items()
            if entry.expired(now, self._ttl)
        ]:
            del self._entries[case_id]


def _parts(envelope: Envelope) -> Iterable[WorkingCopyPart]:
    for part in envelope.parts:
        if part.redacted_text is not None:
            yield WorkingCopyPart(
                part_id=part.part_id, shape="text", text=_clip(part.redacted_text)
            )
            continue
        if part.structured_payload is None:  # pragma: no cover - defensive
            # A ContentPart carries free text or a structured payload; a part
            # with neither cannot come out of ingest. Skipped rather than
            # rendered as an empty box, because an empty box on this page would
            # read as "the machine saw nothing here" and it did not.
            continue
        yield WorkingCopyPart(
            part_id=part.part_id,
            shape="structured",
            text=_clip(_render_payload(part.structured_payload)),
        )


def _render_payload(payload: Mapping[str, object]) -> str:
    """The redacted structured payload as dotted path lines.

    Not JSON: the page shows it to a citizen next to what they typed, and
    ``antragsteller.versicherungsnummer = [[PII|VSNR|...]]`` is readable in a
    way that a pretty-printed object with six levels of braces is not.
    """
    return "\n".join(f"{path} = {value}" for path, value in sorted(_walk(payload, "")))


def _walk(node: object, prefix: str) -> Iterable[tuple[str, str]]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _walk(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{prefix}[{index}]")
    else:
        yield prefix, "" if node is None else str(node)


def _clip(text: str) -> str:
    return text[:MAX_CHARS]
