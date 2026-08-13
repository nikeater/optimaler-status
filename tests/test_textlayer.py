"""The normalized text layer: the offset map is exact or nothing below it holds.

Every span in this system - an extraction record's provenance, a gap's evidence,
whatever part 08 highlights in a draft - is an index into the normalized text.
If the map from those indices back to the redacted original is off by one, every
one of them points at the wrong characters, and nothing downstream can notice.
So the map is pinned by properties rather than by examples: the examples say what
the normalizer does, the properties say what it can never do.

Non-ASCII characters are built with ``chr`` rather than written into the source,
because the two forms this module exists to reconcile - a precomposed umlaut and
a base letter plus a combining accent - look identical in an editor and a diff.
"""

from __future__ import annotations

import unicodedata
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.textlayer import (
    NormalizedText,
    build_text_layer,
    layer_part,
    layer_stats,
    merged_text,
    normalize,
    normalize_text,
    original_span,
    text_parts,
    translate_span,
)
from schemas.common import Span, VersionStamp
from schemas.envelope import ContentPart, Envelope, RawRef
from schemas.textlayer import OffsetSegment
from tests.factories import FIXED_NOW, TEST_VERSIONS, make_envelope

#: U+00E4, already composed, and U+0301, which composes with the letter before
#: it. NFC turns "a" + ACUTE into one character; the offset map is what keeps a
#: span pointing at the right two characters of the source afterwards.
UMLAUT_A = chr(0x00E4)
COMBINING_ACUTE = chr(0x0301)

#: Deliberately nasty: several whitespace kinds, hyphens, a precomposed umlaut
#: and a combining accent, so NFC has something to do and de-hyphenation has
#: something to trip over.
TEXT_ALPHABET = st.sampled_from(
    [*"abcdeMR ", "\n", "\r\n", "\t", "-", ".", "1", "9", UMLAUT_A, COMBINING_ACUTE]
)
TEXTS = st.lists(TEXT_ALPHABET, max_size=40).map("".join)

#: The same alphabet minus anything NFC would compose, so "the normalized text
#: invents no character" can be checked as a strict subsequence.
PLAIN_TEXTS = st.lists(
    st.sampled_from([*"abcMR 1-.", "\n", "\r\n", "\t"]), max_size=40
).map("".join)


def flatten(text: str) -> str:
    """Every whitespace character as a plain space, everything else as it is."""
    return "".join(" " if char.isspace() else char for char in text)


def is_subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(char in iterator for char in needle)


def make_text_envelope(*texts: tuple[str, str, str]) -> Envelope:
    """An envelope of ``(part_id, source_type, redacted_text)`` triples."""
    return Envelope(
        envelope_id="env-text",
        case_id="case-text",
        channel="email",
        procedure_hint=None,
        raw_refs=[RawRef(ref_id="raw-1", media_type="text/plain")],
        vault_ref="vault:test",
        parts=[
            ContentPart(
                part_id="part-structured-0",
                source_type="born_digital",
                media_type="application/json",
                redacted_text=None,
                structured_payload={},
            ),
            *(
                ContentPart(
                    part_id=part_id,
                    source_type=source_type,
                    media_type="text/plain",
                    redacted_text=text,
                    structured_payload=None,
                )
                for part_id, source_type, text in texts
            ),
        ],
        redaction_verified=True,
        created_at=FIXED_NOW,
        versions=TEST_VERSIONS,
    )


# --------------------------------------------------------- what it does ---


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("Sehr geehrte  Damen\nund Herren", "Sehr geehrte Damen und Herren"),
        ("  fuehrender und nachlaufender Raum  ", "fuehrender und nachlaufender Raum"),
        ("Versiche-\nrungsnummer", "Versicherungsnummer"),
        ("Muster-\n  Weg", "MusterWeg"),
        ("Anlage - Kopie", "Anlage - Kopie"),
        ("Muster- weg", "Muster- weg"),
        ("Rentenbeginn 2026-11-01", "Rentenbeginn 2026-11-01"),
        ("Zeile\r\nZeile", "Zeile Zeile"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_the_three_transformations(original: str, expected: str) -> None:
    assert normalize_text(original) == expected


def test_nfc_composes_a_combining_sequence() -> None:
    """One normalized character, two original ones, and the map says so."""
    normalized = normalize("Ma" + COMBINING_ACUTE + "rz")
    assert unicodedata.is_normalized("NFC", normalized.text)
    assert normalized.length == 4
    # The composed character is its own segment: two originals, one normalized.
    composed = [
        segment
        for segment in normalized.segments
        if segment.norm_end - segment.norm_start
        != segment.orig_end - segment.orig_start
    ]
    assert [(segment.orig_start, segment.orig_end) for segment in composed] == [(1, 3)]


def test_a_hyphen_only_joins_across_a_line_break_between_letters() -> None:
    assert normalize_text("Erwerbsminde-\nrung") == "Erwerbsminderung"
    # ... a digit is not a letter, so a rendered date survives a line wrap.
    assert normalize_text("2026-\n11") == "2026- 11"
    # ... and a hyphen at the very end of the text has nothing to join.
    assert normalize_text("Muster-") == "Muster-"
    assert normalize_text("Muster-\n") == "Muster-"


def test_translation_maps_a_normalized_span_onto_the_original() -> None:
    original = "Sehr geehrte  Damen,\n  die Rentenart lautet regelaltersrente."
    normalized = normalize(original)
    start = normalized.text.index("regelaltersrente")
    orig_start, orig_end = normalized.translate(start, start + len("regelaltersrente"))
    assert original[orig_start:orig_end] == "regelaltersrente"


def test_translation_widens_onto_a_collapsed_run() -> None:
    """Inside a collapsed segment the mapping is not injective, so it widens."""
    normalized = normalize("a  \n b")
    space = normalized.text.index(" ")
    assert normalized.translate(space, space + 1) == (1, 5)


def test_translation_of_degenerate_inputs_never_raises() -> None:
    normalized = normalize("abc")
    assert normalized.translate(-5, -1) == (0, 0)
    assert normalized.translate(0, 99) == (0, 3)
    assert normalized.translate(99, 99) == (3, 3)
    assert translate_span((), 0, 1) == (0, 0)


# ----------------------------------------------------- what it cannot do ---


@given(TEXTS)
def test_normalization_is_idempotent(original: str) -> None:
    once = normalize_text(original)
    assert normalize_text(once) == once


@given(TEXTS)
def test_the_offset_map_covers_the_normalized_text(original: str) -> None:
    normalized = normalize(original)
    segments = normalized.segments
    if not normalized.text:
        assert segments == ()
        return
    assert segments[0].norm_start == 0
    assert segments[-1].norm_end == len(normalized.text)
    for left, right in pairwise(segments):
        assert left.norm_end == right.norm_start, "normalized coverage has a hole"
        assert left.orig_end <= right.orig_start, "original ranges overlap"
    for segment in segments:
        assert segment.norm_start < segment.norm_end
        assert segment.orig_start < segment.orig_end
        assert segment.orig_end <= len(original)


@given(TEXTS)
def test_every_segment_says_the_truth_about_its_two_slices(original: str) -> None:
    """A segment is either a positional 1:1 rewrite or a collapsed run.

    "1:1" is about POSITIONS, not about characters: a single line break becomes
    a single space, so the two slices can differ in content while every index
    still maps exactly. That is why translation inside such a segment is allowed
    to be index-for-index, and why a collapsed run - many characters onto one
    space - has to stay a segment of its own.
    """
    normalized = normalize(original)
    for segment in normalized.segments:
        piece = normalized.text[segment.norm_start : segment.norm_end]
        source = flatten(
            unicodedata.normalize(
                "NFC", original[segment.orig_start : segment.orig_end]
            )
        )
        if source == piece:
            continue
        assert piece == " " and not source.strip(), (
            f"segment {segment} maps {source!r} onto {piece!r}"
        )


@given(TEXTS)
def test_translation_is_monotone_and_ordered(original: str) -> None:
    normalized = normalize(original)
    if not normalized.text:
        return
    previous_start = -1
    previous_end = -1
    for index in range(len(normalized.text) + 1):
        start, end = normalized.translate(index, index)
        assert start >= previous_start, "start translation went backwards"
        assert end >= previous_end, "end translation went backwards"
        previous_start, previous_end = start, end
    for index in range(len(normalized.text)):
        start, end = normalized.translate(index, index + 1)
        assert start <= end, "a non-empty span translated to an inverted range"


@given(PLAIN_TEXTS)
def test_normalization_never_invents_a_character(original: str) -> None:
    """Deletions and whitespace collapse only; nothing is ever added."""
    normalized = normalize_text(original)
    assert is_subsequence(normalized, flatten(original))
    assert "\n" not in normalized and "\t" not in normalized
    assert normalized == normalized.strip()
    assert "  " not in normalized


@given(TEXTS)
def test_de_hyphenation_only_ever_removes_a_hyphen(original: str) -> None:
    """No hyphen is ever added; the ones a line break licensed are removed."""
    assert normalize_text(original).count("-") <= original.count("-")


@given(st.text(max_size=30))
def test_arbitrary_unicode_still_produces_a_valid_layer(original: str) -> None:
    """Hypothesis's full text strategy: the invariants are not alphabet-bound."""
    normalized = normalize(original)
    rebuilt = "".join(
        normalized.text[segment.norm_start : segment.norm_end]
        for segment in normalized.segments
    )
    assert rebuilt == normalized.text


# ------------------------------------------------------------- the layer ---


def test_the_layer_has_one_part_per_free_text_part_in_envelope_order() -> None:
    envelope = make_text_envelope(
        ("part-text-0", "born_digital", "Guten  Tag"),
        ("part-text-1", "ocr", "Anlage\nzum Antrag"),
    )
    assert [part.part_id for part in text_parts(envelope)] == [
        "part-text-0",
        "part-text-1",
    ]
    layer = build_text_layer(envelope, versions=TEST_VERSIONS, now=FIXED_NOW)
    assert layer is not None
    assert [part.part_id for part in layer.parts] == ["part-text-0", "part-text-1"]
    assert [part.normalized_text for part in layer.parts] == [
        "Guten Tag",
        "Anlage zum Antrag",
    ]
    assert [part.source_type.value for part in layer.parts] == ["born_digital", "ocr"]
    assert layer.envelope_id == "env-text"
    assert layer.created_at == FIXED_NOW


def test_an_envelope_without_prose_produces_no_layer_at_all() -> None:
    """None, not an empty layer: the contract requires at least one part, and an
    empty one would be a lie about what arrived."""
    assert build_text_layer(make_envelope({}), versions=TEST_VERSIONS) is None


def test_layer_lookup_helpers_answer_none_for_what_is_not_there() -> None:
    layer = build_text_layer(
        make_text_envelope(("part-text-0", "born_digital", "Antrag")),
        versions=TEST_VERSIONS,
    )
    assert layer is not None
    assert layer_part(layer, "part-text-0") is not None
    assert layer_part(layer, "part-text-9") is None
    assert layer_part(None, "part-text-0") is None
    assert original_span(layer, Span(part_id="part-text-9", start=0, end=1)) is None
    assert original_span(None, Span(part_id="part-text-0", start=0, end=1)) is None


def test_the_merged_view_joins_every_part_with_one_space() -> None:
    layer = build_text_layer(
        make_text_envelope(
            ("part-text-0", "born_digital", "Rentenart:  regelaltersrente"),
            ("part-text-1", "ocr", "Anlage"),
        ),
        versions=TEST_VERSIONS,
    )
    assert merged_text(layer) == "Rentenart: regelaltersrente Anlage"
    assert merged_text(None) == ""


def test_original_span_translates_through_the_named_part() -> None:
    text = "Zeile eins\n\nRentenart: regelaltersrente"
    layer = build_text_layer(
        make_text_envelope(("part-text-0", "born_digital", text)),
        versions=TEST_VERSIONS,
    )
    assert layer is not None
    normalized = layer.parts[0].normalized_text
    start = normalized.index("regelaltersrente")
    translated = original_span(
        layer, Span(part_id="part-text-0", start=start, end=start + 16)
    )
    assert translated is not None
    assert text[translated[0] : translated[1]] == "regelaltersrente"


def test_layer_stats_describe_the_layer_without_quoting_it() -> None:
    layer = build_text_layer(
        make_text_envelope(("part-text-0", "ocr", "Sehr geehrte  Damen")),
        versions=TEST_VERSIONS,
    )
    stats = layer_stats(layer)
    assert stats == {
        "part_count": 1,
        "parts": [
            {
                "part_id": "part-text-0",
                "source_type": "ocr",
                "normalized_chars": len("Sehr geehrte Damen"),
                # Three: the run up to the double space, the collapsed run
                # itself (two characters onto one), and the rest.
                "offset_segments": 3,
            }
        ],
    }
    assert layer_stats(None) == {"part_count": 0, "parts": []}
    assert "Damen" not in str(stats)


def test_a_normalized_text_can_be_rebuilt_from_a_contract_offset_map() -> None:
    """The dataclass and the contract model carry the same information."""
    normalized = normalize("Sehr  geehrte\nDamen")
    rebuilt = NormalizedText(
        text=normalized.text,
        segments=tuple(
            OffsetSegment(**segment.model_dump()) for segment in normalized.segments
        ),
    )
    assert rebuilt.translate(0, rebuilt.length) == normalized.translate(
        0, normalized.length
    )


def test_the_layer_carries_the_version_stamp_it_was_built_with() -> None:
    versions = VersionStamp(schema_version="0.1.0", prompt_version="prompt_v1")
    layer = build_text_layer(
        make_text_envelope(("part-text-0", "born_digital", "Antrag")),
        versions=versions,
        now=FIXED_NOW,
    )
    assert layer is not None
    assert layer.versions.prompt_version == "prompt_v1"
