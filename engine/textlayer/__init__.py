"""The normalized text layer: one string per part, and a way back to the text.

Everything downstream of this package works in NORMALIZED coordinates, and the
text it normalizes is already redacted (part 04 seals before the layer is built,
ADR-019). The offset map is the only thing that knows how to get from a
normalized span back to the redacted original, and nothing at all knows how to
get back to the raw document from here.
"""

from engine.textlayer.layer import (
    build_text_layer,
    layer_part,
    layer_stats,
    merged_text,
    original_span,
    text_parts,
)
from engine.textlayer.normalize import (
    NormalizedText,
    normalize,
    normalize_text,
    translate_span,
)

__all__ = [
    "NormalizedText",
    "build_text_layer",
    "layer_part",
    "layer_stats",
    "merged_text",
    "normalize",
    "normalize_text",
    "original_span",
    "text_parts",
    "translate_span",
]
