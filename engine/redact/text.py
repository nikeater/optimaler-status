"""Sealing identity out of free text, span by span.

The structured sealer (:mod:`engine.redact.seal`) works on payload PATHS: a
policy names ``antragsteller.versicherungsnummer`` and the whole leaf is
replaced. Prose has no paths. What decides here is the detector union
(:mod:`engine.redact.detector`) running the recall-first REDACT profile, and what
gets replaced is the exact character range it found - the rest of the sentence
stays, because a letter with its verbs removed is not a working copy.

Three things this module has to get right, and the reasons they are not
negotiable:

**Back to front.** Detections come back merged, disjoint and in document order,
so substituting from the last hit backwards keeps every earlier offset valid.
Nothing recomputes an offset after a substitution.

**The witness gets the raw span text.** A Versicherungsnummer sealed out of a
letter still has to be checkable against the birth date it encodes, exactly like
a sealed payload leaf (ADR-017). :func:`engine.redact.seal.scalar_text` is the
same renderer both paths use, so a value validates identically whether it
arrived in a JSON field or in a sentence.

**One placeholder per hit, minted from the case's registry.** Two mentions of the
same Versicherungsnummer in one letter get two different tokens. That is
deliberate: the tokens are drawn from a random source and carry no structure, so
equality of tokens must not become a channel that says "these two spans are the
same person" to anything downstream (part 06's scorer above all).

What this module does NOT do is decide whether the result is clean. That is the
boundary's second sweep (:mod:`engine.redact.verify`), and it runs a union that
is at least as wide as the one that sealed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from engine.redact.detector import Detector, redact_detector
from engine.redact.placeholders import PlaceholderRegistry
from engine.redact.seal import scalar_text
from engine.redact.vault import SealedEntry


@dataclass(frozen=True)
class SealedText:
    """One text part after sealing: the working copy and what left it."""

    text: str
    entries: tuple[SealedEntry, ...] = ()

    @property
    def sealed_count(self) -> int:
        """How many spans were replaced by a placeholder."""
        return len(self.entries)


def text_seal_detector(*, with_ner: bool = False) -> Detector:
    """The union that decides what to seal in prose.

    The REDACT profile, because sealing is the recall-first job: a
    Versicherungsnummer with a typo in it identifies a person just as well as a
    correct one, and refusing to seal it because the Pruefziffer is off would be
    exactly backwards.

    ``with_ner`` is False by DEFAULT and every gate path leaves it there. The
    optional model member adds bare person names, which no regular expression
    carries - but a gate whose value depends on whether an optional wheel is
    installed is not a gate. Production turns it on (``api/app.py``); the corpus
    generator asserts at build time that deterministic sealing alone leaves every
    gold letter verification-clean, so the gate is not the weaker path by
    construction (ADR-019).
    """
    return redact_detector(with_ner=with_ner)


def seal_text(
    text: str,
    *,
    label: str,
    registry: PlaceholderRegistry,
    detector: Detector,
    witness: dict[str, str] | None = None,
) -> SealedText:
    """Replace every detected identity span in ``text`` with a placeholder."""
    if not text:
        return SealedText(text=text)
    detections = detector.scan(text)
    if not detections:
        return SealedText(text=text)
    working = text
    entries: list[SealedEntry] = []
    for hit in reversed(detections):
        raw = text[hit.start : hit.end]
        placeholder = registry.mint(hit.kind)
        working = working[: hit.start] + str(placeholder) + working[hit.end :]
        entries.append(
            SealedEntry(
                kind=hit.kind,
                token=placeholder.token,
                value_json=json.dumps(raw, ensure_ascii=False),
                # ``path`` stays None: prose has no payload path, and inventing
                # one would tell part 08's re-hydrator to look somewhere that
                # does not exist. ``part_id``/``span`` are the fields the vault
                # record has carried for exactly this case since part 04.
                part_id=label,
                span=(hit.start, hit.end),
            )
        )
        rendered = scalar_text(raw)
        if witness is not None and rendered is not None:
            witness[str(placeholder)] = rendered
    return SealedText(text=working, entries=tuple(reversed(entries)))


def seal_texts(
    texts: Mapping[str, str],
    *,
    registry: PlaceholderRegistry,
    detector: Detector,
    witness: dict[str, str] | None = None,
) -> tuple[dict[str, str], tuple[SealedEntry, ...]]:
    """Seal every text in ``texts``; returns the working copies and the entries."""
    working: dict[str, str] = {}
    entries: list[SealedEntry] = []
    for label, text in texts.items():
        sealed = seal_text(
            text, label=label, registry=registry, detector=detector, witness=witness
        )
        working[label] = sealed.text
        entries.extend(sealed.entries)
    return working, tuple(entries)
