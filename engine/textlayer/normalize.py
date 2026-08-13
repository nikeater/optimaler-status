"""Normalization with an exact offset map back to the text it came from.

**What "original" means here.** The text this module normalizes is the part's
REDACTED text as stored on the envelope: identity was sealed into placeholders
by the part-04 boundary BEFORE the layer was built (ADR-019). So every offset in
:class:`schemas.textlayer.OffsetSegment`, every :class:`schemas.common.Span` and
every quote in an extraction proposal lives in redacted coordinates. The raw,
un-redacted letter exists only behind ``raw_refs`` and in the vault, and it never
re-enters the model path - not even to translate a span for display. A caseworker
who needs the original opens the original.

Three transformations, in this order, all of them deletions or 1:1 rewrites:

1. **Unicode NFC.** Combining sequences are composed, so ``e`` + U+0301 and
   U+00E9 compare equal. This is the only step that can change the number of
   characters in a run, which is precisely why the offset map exists.
2. **Line-break de-hyphenation.** ``Versiche-\\nrungsnummer`` becomes
   ``Versicherungsnummer``: the hyphen and the line break are dropped and the
   two halves join. Only a hyphen BETWEEN two letters with a newline in the
   whitespace after it qualifies, so ``Muster-\\n  Weg`` joins while a dash used
   as punctuation ("Anlage - Kopie") does not.
3. **Whitespace collapsing.** Every run of whitespace becomes one space; leading
   and trailing whitespace is dropped entirely. Line structure is deliberately
   not preserved: a quote that a model or a fixture reports has to compare equal
   whether the sender's mail client wrapped the line or not.

Nothing is ever INSERTED. Map every whitespace character of the NFC form of the
input to a plain space and the normalized text is a SUBSEQUENCE of the result -
that is the property the test suite pins, and it is the sharp statement of "the
normalizer can delete a hyphen, collapse a run and compose a cluster, and it can
do nothing else". A character in a quote that is not in the letter is therefore
impossible before verification even begins.

The offset map is built in the same single pass that builds the text, so it is
exact by construction rather than reconstructed afterwards. Segments are ordered,
non-overlapping, and cover the whole normalized string; runs where one normalized
character came from one original character are merged into a single segment, and
every non-1:1 emission (a collapsed whitespace run, a composed combining
sequence) stays its own segment so translation never has to interpolate inside
one.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from schemas.textlayer import OffsetSegment

#: One emitted piece of normalized text: what it is, and the half-open original
#: range it came from.
type Emission = tuple[str, int, int]

#: Characters that count as a LINE break for de-hyphenation. A plain space does
#: not: "Muster- weg" inside a line is a typo or a compound, and joining it would
#: silently rewrite the sender's words.
LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\u2028\u2029")


@dataclass(frozen=True)
class NormalizedText:
    """Normalized text plus the map back to the redacted original."""

    text: str
    segments: tuple[OffsetSegment, ...]

    @property
    def length(self) -> int:
        """Length of the normalized text."""
        return len(self.text)

    def translate(self, start: int, end: int) -> tuple[int, int]:
        """Translate a normalized half-open range into original coordinates."""
        return translate_span(self.segments, start, end)


def normalize(original: str) -> NormalizedText:
    """Normalize one text and return it with its offset map."""
    emissions = _emit(original)
    return NormalizedText(
        text="".join(chunk for chunk, _, _ in emissions),
        segments=_segments(emissions),
    )


def normalize_text(original: str) -> str:
    """The normalized text alone, for callers that do not need the map."""
    return normalize(original).text


def translate_span(
    segments: tuple[OffsetSegment, ...] | list[OffsetSegment], start: int, end: int
) -> tuple[int, int]:
    """Translate ``[start, end)`` in normalized coordinates to the original.

    A range inside a 1:1 segment translates character for character. A range
    that falls inside a collapsed segment (one space standing for a run of
    whitespace, one composed character standing for two) translates to the WHOLE
    original range of that segment: the mapping is genuinely not injective
    there, and widening is the honest answer for a display highlight. Widening
    also keeps the result monotone, which is the property a caller relies on
    when it slices the original document.
    """
    if not segments:
        return (0, 0)
    ordered = list(segments)
    return (
        _translate_start(ordered, max(start, 0)),
        _translate_end(ordered, max(end, start, 0)),
    )


def _emit(original: str) -> list[Emission]:
    """The single pass: normalized pieces with their original ranges."""
    emissions: list[Emission] = []
    index = 0
    length = len(original)
    while index < length:
        char = original[index]
        if char.isspace():
            index = _emit_whitespace(original, index, emissions)
            continue
        if char == "-" and _is_hyphenation(original, index, emissions):
            index = _end_of_whitespace(original, index + 1)
            continue
        end = index + 1
        while end < length and unicodedata.combining(original[end]):
            end += 1
        # NFC of a non-empty run is non-empty, so this always emits: the pass
        # deletes only whitespace and hyphenation, and both are handled above.
        emissions.append(
            (unicodedata.normalize("NFC", original[index:end]), index, end)
        )
        index = end
    return emissions


def _emit_whitespace(original: str, index: int, emissions: list[Emission]) -> int:
    """Collapse one whitespace run; returns the index after it.

    Leading whitespace (nothing emitted yet) and trailing whitespace (the run
    reaches the end of the text) produce no emission at all, so the normalized
    text is stripped without a second pass that would have to redo the map.
    """
    end = _end_of_whitespace(original, index)
    if emissions and end < len(original):
        emissions.append((" ", index, end))
    return end


def _end_of_whitespace(original: str, index: int) -> int:
    while index < len(original) and original[index].isspace():
        index += 1
    return index


def _is_hyphenation(original: str, index: int, emissions: list[Emission]) -> bool:
    """Whether the hyphen at ``index`` is a line-break hyphenation.

    Requires a letter immediately before it (in what was already emitted), a
    whitespace run containing a line break after it, and a letter after that.
    Every other hyphen - a compound word, a dash between clauses, the hyphen in
    a rendered date - is ordinary text and survives untouched.
    """
    if not emissions:
        return False
    previous = emissions[-1][0]
    if not previous or not previous[-1].isalpha():
        return False
    cursor = index + 1
    saw_break = False
    while cursor < len(original) and original[cursor].isspace():
        if original[cursor] in LINE_BREAKS:
            saw_break = True
        cursor += 1
    return saw_break and cursor < len(original) and original[cursor].isalpha()


def _segments(emissions: list[Emission]) -> tuple[OffsetSegment, ...]:
    """Merge consecutive 1:1 emissions; keep every other one on its own."""
    segments: list[list[int]] = []
    norm_position = 0
    for chunk, orig_start, orig_end in emissions:
        norm_start = norm_position
        norm_position += len(chunk)
        one_to_one = len(chunk) == orig_end - orig_start
        if (
            segments
            and one_to_one
            and _is_one_to_one(segments[-1])
            and segments[-1][1] == norm_start
            and segments[-1][3] == orig_start
        ):
            segments[-1][1] = norm_position
            segments[-1][3] = orig_end
            continue
        segments.append([norm_start, norm_position, orig_start, orig_end])
    return tuple(
        OffsetSegment(
            norm_start=segment[0],
            norm_end=segment[1],
            orig_start=segment[2],
            orig_end=segment[3],
        )
        for segment in segments
    )


def _is_one_to_one(segment: list[int]) -> bool:
    return segment[1] - segment[0] == segment[3] - segment[2]


def _translate_start(segments: list[OffsetSegment], start: int) -> int:
    for segment in segments:
        if segment.norm_start <= start < segment.norm_end:
            if _same_length(segment):
                return segment.orig_start + (start - segment.norm_start)
            return segment.orig_start
    return segments[-1].orig_end


def _translate_end(segments: list[OffsetSegment], end: int) -> int:
    if end <= 0:
        return segments[0].orig_start
    for segment in segments:
        if segment.norm_start < end <= segment.norm_end:
            if _same_length(segment):
                return segment.orig_start + (end - segment.norm_start)
            return segment.orig_end
    return segments[-1].orig_end


def _same_length(segment: OffsetSegment) -> bool:
    return (
        segment.norm_end - segment.norm_start == segment.orig_end - segment.orig_start
    )
