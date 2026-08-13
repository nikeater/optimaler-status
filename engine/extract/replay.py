"""The deterministic text extractor: proposals from a corpus sidecar fixture.

Why this exists. The verification machinery of :mod:`engine.extract.verify` is
the safety-critical half of the text path, and a gate that only exercised it
through a language model would be a gate that depends on a wheel, a GPU and a
sampling temperature. So the corpus carries, next to every generated letter, the
fixture the generator already had in its hands: which field it wrote where, in
what wording. Replaying it produces proposals of exactly the same shape a model
produces, and the verifier - which cannot tell them apart - runs in full on
every gold item, every run, on any machine.

What it is NOT. It is not an extractor for real post, and it never becomes one:
it locates values by the labels the generator itself wrote. It measures the
verifier, the merge, the discard accounting and the tier pressure. What a real
extractor has to do - read a sentence nobody wrote for it - is what the live
client does, and that number is reported separately and never gated (ADR-020).

Two entry modes per fixture entry, and the generator decides which by actually
sealing the letter at build time:

``literal``  the fragment survived sealing untouched, so the fixture carries the
             clean value and the clean quote - and the quote is the LABEL plus
             the value, starting at the anchor. Wider than the value on purpose:
             a quote that WAS the value would satisfy the second lock by
             construction and leave every corpus item testing only one of the
             two. On an OCR item the text under the quote is corrupted and the
             fuzzy lock is genuinely exercised.
``sealed``   the detector replaced the fragment with a placeholder, so there is
             nothing stable to declare. The extractor reads the placeholder that
             stands after the anchor and proposes THAT - which is what the letter
             now says, and correct (ADR-019). The real value is validated
             through the witness, not here. This entry kind necessarily builds
             its quote from the text it is verified against, so it exercises the
             placeholder path rather than the double lock; the double lock is
             what every ``literal`` entry and every unit test exercises.

The fixture rides on the submission JSON and is read straight from it. It is
deliberately not on the ``Envelope``: it is corpus scaffolding, it never travels
into a journal payload or an API response, and a contract field for it would
have made test material part of the published shape of an inbound item.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.extract.proposal import Proposal
from engine.redact import PLACEHOLDER_RE
from schemas.textlayer import TextLayer, TextLayerPart

#: Key the fixture travels under on the submission JSON.
FIXTURE_KEY = "extractionFixture"

#: Characters the anchor may be followed by before the value starts. Exactly
#: what the letter renderer writes; nothing is skipped that a reader would
#: consider part of the value.
_ANCHOR_GAP = " \t"


class FixtureEntry(BaseModel):
    """One field the generator wrote into one letter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    part_id: str
    anchor: str = Field(
        min_length=1,
        description="Literal label the renderer wrote immediately before the "
        "value; the extractor locates the value by finding it",
    )
    mode: Literal["literal", "sealed"] = "literal"
    value: str | None = None
    quote: str | None = None

    def check(self) -> None:
        """Raise when a literal entry does not carry what literal means."""
        if self.mode == "literal" and not (self.value and self.quote):
            raise ValueError(
                f"fixture entry for {self.field!r} is 'literal' but carries no "
                f"value/quote; a literal entry IS its declared text"
            )


@dataclass(frozen=True)
class ReplayStats:
    """What the replay pass did, without saying what it read."""

    entries: int = 0
    proposed: int = 0
    anchor_missing: int = 0
    placeholder_missing: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "entries": self.entries,
            "proposed": self.proposed,
            "anchor_missing": self.anchor_missing,
            "placeholder_missing": self.placeholder_missing,
        }


def fixture_from_payload(payload: Mapping[str, Any]) -> tuple[FixtureEntry, ...]:
    """Read the extraction fixture off a submission; empty when there is none.

    A malformed fixture is a hard error, not a silent skip: it can only come
    from the corpus generator, and a generator that wrote a broken sidecar has
    to hear about it during the build rather than produce a corpus that quietly
    extracts nothing.
    """
    raw = payload.get(FIXTURE_KEY)
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValueError(f"{FIXTURE_KEY} must be a list of entries")
    entries = tuple(FixtureEntry.model_validate(item) for item in raw)
    for entry in entries:
        entry.check()
    return entries


def replay_proposals(
    entries: Sequence[FixtureEntry],
    layer: TextLayer | None,
    *,
    extractor_id: str,
) -> tuple[tuple[Proposal, ...], ReplayStats]:
    """Turn fixture entries into proposals against the normalized layer."""
    if layer is None or not entries:
        return (), ReplayStats(entries=len(entries))
    parts = {part.part_id: part for part in layer.parts}
    proposals: list[Proposal] = []
    anchor_missing = 0
    placeholder_missing = 0
    for entry in entries:
        part = parts.get(entry.part_id)
        if part is None:
            anchor_missing += 1
            continue
        located = _locate(part, entry.anchor)
        if located is None:
            # The label is not in the letter: this item simply does not state
            # the field, which is what a missing_field scenario looks like from
            # the inside. No proposal, no discard - there was nothing to verify.
            anchor_missing += 1
            continue
        built = _proposal(entry, part, located, extractor_id)
        if built is None:
            placeholder_missing += 1
            continue
        proposals.append(built)
    return tuple(proposals), ReplayStats(
        entries=len(entries),
        proposed=len(proposals),
        anchor_missing=anchor_missing,
        placeholder_missing=placeholder_missing,
    )


def _locate(part: TextLayerPart, anchor: str) -> tuple[int, int] | None:
    """``(anchor start, value start)``, or None when the label is absent."""
    position = part.normalized_text.find(anchor)
    if position < 0:
        return None
    offset = position + len(anchor)
    text = part.normalized_text
    while offset < len(text) and text[offset] in _ANCHOR_GAP:
        offset += 1
    return position, offset


def _proposal(
    entry: FixtureEntry,
    part: TextLayerPart,
    located: tuple[int, int],
    extractor_id: str,
) -> Proposal | None:
    anchor_start, value_start = located
    if entry.mode == "sealed":
        match = PLACEHOLDER_RE.match(part.normalized_text, value_start)
        if match is None:
            # The fixture says a placeholder stands here and none does. That is
            # a disagreement between the corpus and the boundary, and the honest
            # answer is no proposal: inventing one from the surrounding text
            # would be the extractor deciding what the letter meant.
            return None
        token = match.group(0)
        return Proposal(
            field=entry.field,
            value=token,
            quote=token,
            part_id=part.part_id,
            offset=value_start,
            extractor_id=extractor_id,
        )
    return Proposal(
        field=entry.field,
        value=entry.value or "",
        quote=entry.quote or "",
        part_id=part.part_id,
        # The label is INSIDE the quote, so the offset is where the label
        # starts. A caseworker highlighting this span sees "Rentenart:
        # regelaltersrente", which is the sentence the letter makes, not a bare
        # word out of it.
        offset=anchor_start,
        extractor_id=extractor_id,
    )
