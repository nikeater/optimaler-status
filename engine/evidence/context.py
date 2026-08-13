"""The flat evaluation context routing rules, derivation signals and
clear-cut criteria read.

Five namespaces, documented in ``config/rules/routing_v3.yaml``:

    procedure_hint          the procedure id the channel claimed, may be None
    channel                 the inbound channel
    payload.<dotted.path>   scalar leaf of the structured payload, as received
    text.normalized         every free-text part of the item, normalized and
                            already redacted, joined into one string
    text.source_types       the source types present, comma-joined and sorted
    extraction.<field_id>   value of an extracted field, absent when not
                            extracted
    procedure_id            the procedure the evidence plane derived
    procedure_source        how it was derived: hint | content | none

The ``payload.*`` namespace exists because of a chicken-and-egg problem part 02
documented: the schema mapper only extracts fields a *known* procedure declares
in its ``field_map``, so with an unknown procedure nothing is extracted and
every rule over ``extraction.*`` is dead. Procedure derivation has to read
content before a procedure exists, and content-based routing rules should keep
working when derivation finds nothing, so both read the payload directly.

``extraction.*`` stays the namespace for values that went through a procedure's
field map and through span verification; ``payload.*`` is the raw reading with no
procedure behind it. ``text.*`` is the third reading: what the item SAYS, before
anybody has decided what any of it means. It exists for the same reason
``payload.*`` does - derivation has to be able to identify a procedure from a
free-text Anschreiben that has no structured payload at all, and a rule over
``extraction.*`` is dead until a procedure is known (ADR-020).

``text.normalized`` is the MERGED view: every part of the item, joined by a
space. A Rentenart named in the mail body and one named in the scanned annex are
the same fact about the same case, and a per-part namespace would force every
config rule to enumerate parts whose number it cannot know. Spans stay strictly
per part, because a span that did not name its part could not be translated back.

Absent and None are the same thing to the predicate evaluator: the condition
fails. Presence is therefore tested as ``{field: extraction.x, op: ne,
value: null}``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engine.namespaces import (
    EXTRACTION_PREFIX,
    PAYLOAD_PREFIX,
    TEXT_FIELDS,
    TEXT_PREFIX,
)
from engine.textlayer import merged_text
from schemas.envelope import Envelope
from schemas.extraction import ExtractionSet
from schemas.textlayer import TextLayer

__all__ = [
    "EXTRACTION_PREFIX",
    "PAYLOAD_PREFIX",
    "TEXT_FIELDS",
    "TEXT_PREFIX",
    "build_context",
    "build_payload_context",
]

#: How deep the payload flattener descends. A submission that nests deeper than
#: this is not silently half-read: the deeper keys simply do not exist in the
#: context, and a rule over them fails like any unknown field.
MAX_PAYLOAD_DEPTH = 6


def build_payload_context(
    envelope: Envelope, layer: TextLayer | None = None
) -> dict[str, object]:
    """Context available before anything is extracted: hint, channel, payload, text.

    This is what procedure derivation sees. It is deliberately a strict subset
    of :func:`build_context`, so a derivation signal can never read a value that
    only exists because a procedure was already known.

    The text is already redacted (part 04 seals before the layer is built), so
    what a rule can read here is prose with placeholders where identity used to
    be. That is not an inconvenience to work around: it is what makes a config
    lint able to promise that no rule quotes a person's data.
    """
    context: dict[str, object] = {
        "procedure_hint": envelope.procedure_hint,
        "channel": envelope.channel.value,
    }
    for part in envelope.parts:
        if part.structured_payload is None:
            continue
        _flatten(part.structured_payload, PAYLOAD_PREFIX, context, depth=0)
    text = merged_text(layer)
    if text:
        context["text.normalized"] = text
        context["text.source_types"] = ",".join(
            sorted({part.source_type.value for part in (layer.parts if layer else [])})
        )
    return context


def build_context(
    envelope: Envelope,
    extractions: ExtractionSet,
    *,
    procedure_id: str | None = None,
    procedure_source: str | None = None,
    layer: TextLayer | None = None,
) -> dict[str, object]:
    """Build the full evaluation context for one item."""
    context = build_payload_context(envelope, layer)
    context["procedure_id"] = procedure_id
    context["procedure_source"] = procedure_source
    for record in extractions.records:
        context[f"{EXTRACTION_PREFIX}{record.field}"] = record.value
    return context


def _flatten(
    payload: Mapping[str, Any], prefix: str, target: dict[str, object], *, depth: int
) -> None:
    """Write every scalar leaf of ``payload`` into ``target`` as prefix+path.

    Lists are skipped rather than indexed: a rule that depends on the position
    of an item in a list would be unreadable config, and part 04's attachment
    handling gives repeated structures their own evidence shape.
    """
    if depth >= MAX_PAYLOAD_DEPTH:
        return
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            _flatten(value, f"{path}.", target, depth=depth + 1)
            continue
        scalar = _as_scalar(value)
        if scalar is not None:
            target[path] = scalar


def _as_scalar(value: object) -> str | None:
    """Render a payload leaf as a context value; None means 'not usable'.

    Mirrors ``engine.extract.mapper._as_scalar`` on purpose: a rule over
    ``payload.x`` and a rule over ``extraction.x`` must see the same text for
    the same submission, or the two namespaces would disagree about reality.
    """
    if value is None or isinstance(value, Mapping | list | tuple | set):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None
