"""Building the :class:`schemas.textlayer.TextLayer` for one envelope.

One :class:`schemas.textlayer.TextLayerPart` per free-text content part, in
envelope order. Structured parts have no text and produce nothing; an envelope
with no free-text part produces no layer at all (``None``), because the contract
requires at least one part and an empty layer would be a lie about what arrived.

The layer is derived, never stored: it is a pure function of the envelope's
redacted text, so it can be rebuilt at any time and cannot drift from the text a
span points into. Nothing here reaches for the raw document.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from engine.textlayer.normalize import NormalizedText, normalize
from schemas.common import Span, VersionStamp
from schemas.envelope import ContentPart, Envelope
from schemas.textlayer import TextLayer, TextLayerPart


def text_parts(envelope: Envelope) -> Iterator[ContentPart]:
    """Every content part that carries free text, in envelope order."""
    for part in envelope.parts:
        if part.redacted_text is not None:
            yield part


def build_text_layer(
    envelope: Envelope,
    *,
    versions: VersionStamp,
    now: datetime | None = None,
) -> TextLayer | None:
    """Normalize every free-text part of ``envelope``; None when there is none."""
    parts = [
        _layer_part(part, normalize(part.redacted_text or ""))
        for part in text_parts(envelope)
    ]
    if not parts:
        return None
    return TextLayer(
        envelope_id=envelope.envelope_id,
        case_id=envelope.case_id,
        parts=parts,
        created_at=now or datetime.now(UTC),
        versions=versions,
    )


def _layer_part(part: ContentPart, normalized: NormalizedText) -> TextLayerPart:
    return TextLayerPart(
        part_id=part.part_id,
        source_type=part.source_type,
        normalized_text=normalized.text,
        offset_map=list(normalized.segments),
    )


def layer_part(layer: TextLayer | None, part_id: str) -> TextLayerPart | None:
    """The layer part with ``part_id``, or None."""
    if layer is None:
        return None
    for part in layer.parts:
        if part.part_id == part_id:
            return part
    return None


def merged_text(layer: TextLayer | None) -> str:
    """Every part's normalized text, joined by a single space.

    This is what the ``text.*`` evaluation namespace exposes to derivation
    signals and routing rules (ADR-020). A rule asks whether THIS ITEM says
    something, not whether attachment two says it: a Rentenart named in the mail
    body and one named in the scanned annex are the same fact about the case, and
    a per-part namespace would force every config rule to enumerate parts it
    cannot know about. Spans stay per part, because a span that did not name its
    part could not be translated back.
    """
    if layer is None:
        return ""
    return " ".join(part.normalized_text for part in layer.parts)


def original_span(layer: TextLayer | None, span: Span) -> tuple[int, int] | None:
    """Translate a normalized span back into its part's redacted coordinates.

    Returns None when the span names a part this layer does not have, which is
    the same answer the verifier gives a proposal pointing at a part that does
    not exist: no guessing, no nearest match.
    """
    part = layer_part(layer, span.part_id)
    if part is None:
        return None
    return NormalizedText(
        text=part.normalized_text, segments=tuple(part.offset_map)
    ).translate(span.start, span.end)


def layer_stats(layer: TextLayer | None) -> dict[str, object]:
    """Value-free description of the layer, for the REDACTED journal payload."""
    if layer is None:
        return {"part_count": 0, "parts": []}
    return {
        "part_count": len(layer.parts),
        "parts": [
            {
                "part_id": part.part_id,
                "source_type": part.source_type.value,
                "normalized_chars": len(part.normalized_text),
                "offset_segments": len(part.offset_map),
            }
            for part in layer.parts
        ],
    }
