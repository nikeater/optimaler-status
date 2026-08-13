"""The recognizer union, merged longest-span-wins.

This is the NERwhal-style multi-recognizer union the prior-art pass named as the
answer to weak single-model German NER (P-7): deterministic recognizers carry
the identifiers that have a spec and a checksum, and the optional NER member
(:mod:`engine.redact.ner`) carries the ones that only a model can find - bare
person names above all. The union is the unit that gets measured, because a per-
recognizer number says nothing about what actually escapes.

Merging answers two questions at once, and it has to answer them separately:

**How much text is covered?** The union of every overlapping hit. Coverage never
shrinks in a merge - if one member saw more characters than another, those
characters stay covered. Dropping the loser's span outright would let a
deterministic recognizer that matched a narrow core UNDER-redact what a broader
hit had found.

**What is it?** The strongest evidence in the group wins (see
:class:`engine.redact.recognizers.Evidence`): a checksum-validated identifier
beats a pattern, a pattern beats a model. This is not cosmetic - the kind
decides which vault entry the value gets and how part 08 re-hydrates it. The
canary run with the extra installed found the concrete case: spaCy tagged
``DE53375756206830111642.`` as an ORGANIZATION, one character longer than the
mod-97-verified IBAN underneath it, and a plain longest-span-wins rule handed a
bank account to a model's guess.

The merged output is a set of disjoint spans in document order, so a caller can
substitute back to front without recomputing offsets.

Free text does not flow through the pipeline yet (part 05). Today the detector
is exercised by the seeded PII golden set (``corpus/pii_golden``) and by the
post-redaction sweep over string leaves of the structured working copy.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from engine.redact.ner import NerMember, load_ner
from engine.redact.recognizers import (
    RECOGNIZERS,
    Detection,
    Profile,
    Recognizer,
    recognizers_for,
)


class Detector:
    """A profile, a set of recognizers and (optionally) the NER member."""

    def __init__(
        self,
        profile: Profile,
        *,
        recognizers: Sequence[Recognizer] | None = None,
        ner: NerMember | None = None,
    ) -> None:
        self.profile = profile
        self.recognizers = tuple(
            recognizers if recognizers is not None else recognizers_for(profile)
        )
        self.ner = ner

    @property
    def uses_ner(self) -> bool:
        """Whether the optional model-backed member is part of this union."""
        return self.ner is not None

    def scan(self, text: str) -> tuple[Detection, ...]:
        """Every merged hit in ``text``, in order of appearance."""
        if not text:
            return ()
        found: list[Detection] = []
        for recognizer in self.recognizers:
            found.extend(recognizer.scan(text, self.profile))
        if self.ner is not None:
            found.extend(self.ner.scan(text))
        return merge(found)

    def inventory(self) -> dict[str, object]:
        """What this union consists of, for the eval report."""
        return {
            "profile": self.profile.value,
            "recognizers": sorted(
                recognizer.recognizer_id for recognizer in self.recognizers
            ),
            "ner": None if self.ner is None else self.ner.describe(),
        }


def merge(detections: Iterable[Detection]) -> tuple[Detection, ...]:
    """Reduce overlapping hits to a disjoint set.

    Every group of transitively overlapping hits becomes ONE detection spanning
    the union of the group and carrying the classification of its strongest
    member. Strength is (evidence class, span length, earliest start, kind,
    recognizer id), which is a total order over any set of hits, so the result
    is a pure function of the input SET and never of iteration order.
    """
    groups = _components(sorted(detections, key=lambda hit: (hit.start, hit.end)))
    merged: list[Detection] = []
    for group in groups:
        winner = _strongest(group)
        merged.append(
            Detection(
                start=min(hit.start for hit in group),
                end=max(hit.end for hit in group),
                kind=winner.kind,
                recognizer_id=winner.recognizer_id,
                validated=winner.validated,
                evidence=winner.evidence,
            )
        )
    return tuple(sorted(merged, key=lambda hit: (hit.start, hit.end)))


def _components(ordered: list[Detection]) -> list[list[Detection]]:
    """Group hits into runs of transitively overlapping spans."""
    groups: list[list[Detection]] = []
    reach = 0
    for hit in ordered:
        if groups and hit.start < reach:
            groups[-1].append(hit)
            reach = max(reach, hit.end)
            continue
        groups.append([hit])
        reach = hit.end
    return groups


def _strongest(group: list[Detection]) -> Detection:
    """The hit whose classification the merged span carries."""
    return max(
        group,
        key=lambda hit: (
            int(hit.evidence),
            hit.end - hit.start,
            -hit.start,
            hit.kind.value,
            hit.recognizer_id,
        ),
    )


def redact_detector(*, with_ner: bool = True) -> Detector:
    """The recall-first union used to decide what to seal."""
    return Detector(Profile.REDACT, ner=load_ner() if with_ner else None)


def verify_detector() -> Detector:
    """The precision-first union used by the post-redaction sweep.

    No NER member: the sweep runs over the structured working copy, where the
    remaining question is "did an identifier with a checksum survive", not "is
    this prose about a person". Free-text verification from part 05 on uses the
    full union including NER and the address grammar.
    """
    return Detector(Profile.VERIFY)


def redact_recognizers() -> tuple[Recognizer, ...]:
    """The deterministic recall-first recognizer set (no NER)."""
    return tuple(
        recognizer
        for recognizer in RECOGNIZERS
        if Profile.REDACT in recognizer.profiles
    )
