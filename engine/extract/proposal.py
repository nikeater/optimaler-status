"""What an extractor is allowed to say, before anything believes it.

A proposal is a CLAIM, not evidence: "field ``rentenart`` has value
``regelaltersrente``, because the text of part ``part-text-0`` says
``Rentenart: regelaltersrente`` starting at character 412". It becomes an
:class:`schemas.extraction.ExtractionRecord` only after
:mod:`engine.extract.verify` has checked both halves of that sentence against
the normalized layer, independently.

The shape is identical for every extractor - the deterministic replay one, the
live LLM one, anything a later part adds - and the verifier deliberately cannot
tell which produced a given proposal. If it could, the temptation to trust one
source more than another would arrive with it, and "we trusted it because we
wrote it" is not a verification.

Offsets are in NORMALIZED coordinates of the part's redacted text
(:mod:`engine.textlayer`), which is the only coordinate system anything
downstream of ingest knows about.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Proposal:
    """One claim about one field, with the two things that make it checkable."""

    field: str
    value: str
    quote: str
    part_id: str
    offset: int
    extractor_id: str

    @property
    def end(self) -> int:
        """Where the quote ends if the offset is right.

        Derived rather than carried, and that IS the first lock: an extractor
        that reports a quote and an offset has committed to a range, and the
        verifier compares the text in that range with the quote. A proposal that
        carried an independent ``end`` could be self-consistently wrong.
        """
        return self.offset + len(self.quote)

    def describe(self) -> dict[str, object]:
        """Value-free description for a journal payload or a report.

        Neither the value nor the quote appears: a proposal that was rejected is
        exactly the case where the text is least trustworthy, and a rejection
        record that quoted it would put unverified content into the audit trail.
        """
        return {
            "field": self.field,
            "part_id": self.part_id,
            "offset": self.offset,
            "quote_length": len(self.quote),
            "extractor_id": self.extractor_id,
        }
