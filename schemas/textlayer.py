"""Normalized text layer with an offset map back to the original document.

Normalization: unicode NFC, whitespace collapsing, line-break de-hyphenation.
All spans in the system live in normalized coordinates; the offset map makes
them translatable back to the original for display and audit.
"""

from __future__ import annotations

from pydantic import Field

from .common import SourceType, Stamped, StrictModel


class OffsetSegment(StrictModel):
    """Maps one contiguous normalized range onto its original range.

    Half-open ranges. Segments are ordered and non-overlapping in
    normalized coordinates.
    """

    norm_start: int = Field(ge=0)
    norm_end: int = Field(ge=0)
    orig_start: int = Field(ge=0)
    orig_end: int = Field(ge=0)


class TextLayerPart(StrictModel):
    """Normalized text of one content part plus its offset map."""

    part_id: str
    source_type: SourceType
    normalized_text: str
    offset_map: list[OffsetSegment] = Field(
        description="Ordered, non-overlapping segments covering the normalized text"
    )


class TextLayer(Stamped):
    """All normalized parts of one envelope."""

    envelope_id: str
    case_id: str
    parts: list[TextLayerPart] = Field(min_length=1)
