"""Rendering a scenario's facts as a German administrative letter.

A letter item is the same declared facts as a form item, written into prose
instead of into a JSON payload. Its ``data`` object stays empty, so every
``payload.*`` derivation signal and every routing rule that reads a payload path
is silent and the item can only be understood through the text path: normalize,
derive from ``text.*``, extract, verify each span. That is the point - it is the
one shape in the corpus that cannot be answered by reading a key.

Three things this module produces from one render, and all three have to agree:

``text``      the letter, ASCII, deterministic for a given (spec, seed).
``fixture``   the sidecar the replay extractor reads: for each fact, the label
              the renderer wrote in front of it, and either the clean value and
              quote (``literal``) or a note that the value was sealed away
              (``sealed``).
``protected`` the character ranges OCR corruption may not touch.

**The literal/sealed decision is made by actually sealing.** After rendering,
the deterministic REDACT union runs over the letter, and a fact whose value falls
inside a detection becomes a ``sealed`` entry - the extractor will read the
placeholder that stands there, which is what the letter now says (ADR-019). A
hard-coded list of "identity fields" would be a second classification next to the
recognizers, and the two would drift on the first recognizer change.

**OCR corruption is seeded, bounded and blind to identity.** It never touches an
identity value (the build asserts that deterministic sealing leaves every gold
letter verification-clean, which a corrupted Versicherungsnummer would break),
never touches a label (the extractor locates values by them), and never touches a
short value (a one-character change in a four-character word falls below any
sane fuzzy threshold and would turn a corpus item into a discard). What is left -
the prose, and values long enough to survive - is exactly where a scanner's
mistakes are interesting: the fuzzy lock has to accept them, and the
de-hyphenation in the normalizer has to undo the line breaks.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from engine.redact.detector import redact_detector
from engine.redact.recognizers import Detection

#: Field id -> the label the renderer writes in front of the value. This is the
#: anchor the replay extractor locates the value by, so it is part of the
#: corpus contract: changing one invalidates the sidecars built with it.
#:
#: Two of them are load-bearing beyond that. "Eintritt der Erwerbsminderung" and
#: "Auftraggeber"/"Taetigkeit" are the literal strings the procedure derivation
#: signals look for (ADR-020), so the label a caseworker reads and the signal a
#: rule fires on are the same words.
FIELD_LABELS: dict[str, str] = {
    "geburtsdatum": "Geburtsdatum:",
    "versicherungsnummer": "Versicherungsnummer:",
    "rentenart": "Rentenart:",
    "rentenbeginn": "Rentenbeginn:",
    "auslandsbezug": "Auslandsbezug:",
    "eintritt_erwerbsminderung": "Eintritt der Erwerbsminderung:",
    "letzte_taetigkeit": "Letzte Taetigkeit:",
    "gutachten_status": "Gutachten:",
    "antragsart": "Antragsart:",
    "antragsteller_rolle": "Rolle im Auftragsverhaeltnis:",
    "taetigkeit_bezeichnung": "Taetigkeit:",
    "taetigkeit_beginn": "Taetigkeit seit:",
    "auftraggeber_name": "Auftraggeber:",
    "weisungsgebunden": "Weisungsgebunden:",
    "eingliederung_arbeitsorganisation": "Eingliederung:",
    "arbeitsort": "Arbeitsort:",
    "weitere_auftraggeber": "Weitere Auftraggeber:",
    "umsatzanteil_hauptauftraggeber": "Umsatzanteil Hauptauftraggeber:",
    "honorar_modell": "Honorarmodell:",
    "honorar_monatlich": "Honorar monatlich:",
    "rahmenvertrag": "Rahmenvertrag:",
    "dreiecksverhaeltnis": "Dreiecksverhaeltnis:",
    "auftraggeber_betriebsnummer": "Betriebsnummer:",
}

#: Below this length a value is never corrupted. A single substitution in a
#: four-character word drops the fuzzy ratio to 0.75, well under the configured
#: OCR threshold, so corrupting one would not measure the fuzzy lock - it would
#: manufacture a discard and mislabel the item.
MIN_CORRUPTIBLE = 12

#: At most this many corruptions per letter, and never two on one line.
MAX_CORRUPTIONS = 3

#: Every sender below is invented; same rule as the rest of the corpus. The
#: Anrede is not decoration: it is what makes the deterministic union able to
#: find the name at all, and a corpus whose names only a model can find would
#: make the gate depend on an optional wheel.
_SENDERS: tuple[tuple[str, str], ...] = (
    ("Frau", "Erika Beispielfrau"),
    ("Herr", "Jonas Musterling"),
    ("Frau", "Halina Probstfeld"),
    ("Herr", "Ottmar Beispielhuber"),
    ("Frau", "Nadja Musterkamp"),
)

_ADDRESSES: tuple[str, ...] = (
    "Musterweg 3, 10115 Musterstadt",
    "Lindenallee 17, 04109 Beispielau",
    "Am Hang 8, 99084 Musterhausen",
    "Kirchgasse 2, 24103 Beispielstadt",
    "Feldstrasse 45, 70173 Musterdorf",
)

#: The everyday reading mistakes of a scanner, as (pattern, replacement). Class
#: substitutions only: nothing here invents a digit or a letter that changes an
#: identifier's meaning, because nothing here is ever applied to one.
_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("rn", "m"),
    ("m", "rn"),
    ("ei", "ci"),
    ("ll", "II"),
    # A lowercase l read as a capital I is the single most common confusion in
    # a German administrative scan, and it is the one that reaches the values
    # this corpus cares about: "regelaltersrente" has no rn, no m and no ei.
    ("lt", "It"),
)

#: A word long enough to be split across a line the way a scanner reports it.
_HYPHENATABLE = re.compile(r"[a-z]{4}([a-z]{4,})")


@dataclass(frozen=True)
class FactLine:
    """One declared fact as it stands in the letter."""

    field: str
    label: str
    value: str
    label_start: int
    value_start: int

    @property
    def value_end(self) -> int:
        return self.value_start + len(self.value)

    @property
    def quote(self) -> str:
        """Label and value together: what the fixture declares as the quote.

        Wider than the value on purpose. A quote that WAS the value would make
        the second lock ("does the quote contain the value") true by
        construction and leave only one lock being tested on every corpus item.
        """
        return f"{self.label} {self.value}"


@dataclass(frozen=True)
class Letter:
    """A rendered letter, its sidecar and the ranges corruption may not touch."""

    text: str
    fixture: list[dict[str, Any]] = field(default_factory=list)
    sealed_fields: tuple[str, ...] = ()


def render_letter(
    *,
    subject: str,
    opening: str,
    closing: str,
    facts: dict[str, str],
    with_sender: bool,
    ocr_noise: bool,
    rng: random.Random,
) -> Letter:
    """Render one letter and the extraction sidecar that describes it."""
    text, lines = _compose(
        subject=subject,
        opening=opening,
        closing=closing,
        facts=facts,
        with_sender=with_sender,
        rng=rng,
    )
    detector = redact_detector(with_ner=False)
    if ocr_noise:
        text, lines = _corrupt(text, lines, detector.scan(text), rng)
    detections = detector.scan(text)
    return Letter(
        text=text,
        fixture=[_entry(line, detections) for line in lines],
        sealed_fields=tuple(
            line.field for line in lines if _is_sealed(line, detections)
        ),
    )


def _compose(
    *,
    subject: str,
    opening: str,
    closing: str,
    facts: dict[str, str],
    with_sender: bool,
    rng: random.Random,
) -> tuple[str, list[FactLine]]:
    """Build the letter text, recording where every fact landed as it is written.

    Positions come from the construction, never from searching the finished
    text: a label that also occurred in the Betreff would make a search find
    the wrong one, and a corpus whose sidecar points a few characters off is a
    corpus that silently measures nothing.
    """
    anrede, name = rng.choice(_SENDERS)
    address = rng.choice(_ADDRESSES)
    pieces: list[str] = []
    lines: list[FactLine] = []
    position = 0

    def write(block: str) -> None:
        nonlocal position
        pieces.append(block)
        position += len(block) + 1  # the newline join adds one

    write(f"Betreff: {subject}")
    write("")
    write("Sehr geehrte Damen und Herren,")
    write("")
    write(opening)
    write("")
    if with_sender:
        write(f"Absender: {anrede} {name}")
        write(f"Anschrift: {address}")
    for field_id, value in facts.items():
        label = FIELD_LABELS.get(field_id)
        if label is None:
            raise KeyError(
                f"fact {field_id!r} has no letter label; add it to FIELD_LABELS"
            )
        lines.append(
            FactLine(
                field=field_id,
                label=label,
                value=value,
                label_start=position,
                value_start=position + len(label) + 1,
            )
        )
        write(f"{label} {value}")
    write("")
    write(closing)
    write(f"{anrede} {name}")
    return "\n".join(pieces), lines


def _entry(line: FactLine, detections: tuple[Detection, ...]) -> dict[str, Any]:
    """The sidecar entry for one fact, in the mode the seal actually produced."""
    if _is_sealed(line, detections):
        return {
            "field": line.field,
            "part_id": "part-text-0",
            "anchor": line.label,
            "mode": "sealed",
        }
    return {
        "field": line.field,
        "part_id": "part-text-0",
        "anchor": line.label,
        "mode": "literal",
        "value": line.value,
        "quote": line.quote,
    }


def _is_sealed(line: FactLine, detections: tuple[Detection, ...]) -> bool:
    """Whether the boundary will replace this fact's value with a placeholder."""
    return any(
        hit.start < line.value_end and line.value_start < hit.end for hit in detections
    )


def protected_ranges(
    lines: list[FactLine], detections: tuple[Detection, ...]
) -> list[tuple[int, int]]:
    """Everything OCR corruption must leave alone, as half-open ranges.

    Three classes, and each of them would break something specific:
    an identity value (the seal would miss it and the working copy would carry
    it), a label (the extractor locates values by them, and a mangled label is a
    fact the letter no longer states), and a short value (see MIN_CORRUPTIBLE).
    """
    ranges = [(hit.start, hit.end) for hit in detections]
    for line in lines:
        ranges.append((line.label_start, line.value_start))
        if len(line.value) < MIN_CORRUPTIBLE:
            ranges.append((line.value_start, line.value_end))
    return sorted(ranges)


def _corrupt(
    text: str,
    lines: list[FactLine],
    detections: tuple[Detection, ...],
    rng: random.Random,
) -> tuple[str, list[FactLine]]:
    """Apply up to MAX_CORRUPTIONS seeded scanner mistakes outside the protected
    ranges, and shift the fact positions by exactly what the edits moved."""
    forbidden = protected_ranges(lines, detections)
    corruptible = [
        (line.value_start, line.value_end)
        for line in lines
        if len(line.value) >= MIN_CORRUPTIBLE
    ]
    edits = sorted(_pick_edits(text, forbidden, corruptible, rng))
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text, [_shifted(line, edits) for line in lines]


def _shifted(line: FactLine, edits: list[tuple[int, int, str]]) -> FactLine:
    """The same fact after the edits before it moved everything along.

    Computed rather than searched for: labels and short values are protected, so
    an edit is never INSIDE one, and the offset of a label is its old offset plus
    the length every earlier edit added or removed.
    """
    delta = sum(
        len(replacement) - (end - start)
        for start, end, replacement in edits
        if end <= line.label_start
    )
    return FactLine(
        field=line.field,
        label=line.label,
        value=line.value,
        label_start=line.label_start + delta,
        value_start=line.value_start + delta,
    )


def _pick_edits(
    text: str,
    forbidden: list[tuple[int, int]],
    corruptible: list[tuple[int, int]],
    rng: random.Random,
) -> list[tuple[int, int, str]]:
    """Choose the edits: at most one per line, at most MAX_CORRUPTIONS in all.

    Ordered rather than uniformly random, because two of the three edits have a
    JOB. One substitution inside a declared value is what pushes the fuzzy match
    score below 1.0, so the OCR threshold is actually being tested rather than
    configured; one hyphenation inside a declared value is what the normalizer's
    de-hyphenation has to undo before the span can match at all. Everything
    after those two is ordinary noise in the prose, which is where a scanner's
    mistakes mostly land and where they mostly do not matter.
    """
    substitutions, hyphens = _candidates(text, forbidden)
    inside = _inside(corruptible)
    ordered = [
        *_shuffled([edit for edit in substitutions if inside(edit)], rng),
        *_shuffled([edit for edit in hyphens if inside(edit)], rng)[:1],
        *_shuffled([edit for edit in substitutions if not inside(edit)], rng),
        *_shuffled([edit for edit in hyphens if not inside(edit)], rng),
    ]
    chosen: list[tuple[int, int, str]] = []
    used_lines: set[int] = set()
    for start, end, replacement in ordered:
        line_number = text.count("\n", 0, start)
        if line_number in used_lines:
            continue
        used_lines.add(line_number)
        chosen.append((start, end, replacement))
        if len(chosen) == MAX_CORRUPTIONS:
            break
    return chosen


def _candidates(
    text: str, forbidden: list[tuple[int, int]]
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, str]]]:
    """Every legal substitution and every legal hyphenation, kept apart."""
    substitutions = [
        (match.start(), match.end(), replacement)
        for pattern, replacement in _SUBSTITUTIONS
        for match in re.finditer(pattern, text)
        if not _overlaps(match.start(), match.end(), forbidden)
    ]
    hyphens = [
        (match.start(1), match.start(1), "-\n")
        for match in _HYPHENATABLE.finditer(text)
        if not _overlaps(match.start(1) - 1, match.start(1) + 1, forbidden)
    ]
    return substitutions, hyphens


def _inside(
    ranges: list[tuple[int, int]],
) -> Callable[[tuple[int, int, str]], bool]:
    def predicate(edit: tuple[int, int, str]) -> bool:
        start, end, _ = edit
        return any(
            begin <= start and max(end, start + 1) <= finish for begin, finish in ranges
        )

    return predicate


def _shuffled(
    edits: list[tuple[int, int, str]], rng: random.Random
) -> list[tuple[int, int, str]]:
    shuffled = list(edits)
    rng.shuffle(shuffled)
    return shuffled


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(begin < end and start < finish for begin, finish in ranges)
