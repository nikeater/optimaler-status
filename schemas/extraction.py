"""Span-verified extraction records.

Every value an extractor produces must carry a source span that a validator
mechanically checked against the normalized text layer. Unverifiable values
never become ExtractionRecords; they are discarded and push toward tier 3.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .common import Span, Stamped, StrictModel


class MatchMode(str, Enum):
    EXACT = "exact"  # born-digital text
    FUZZY = "fuzzy"  # OCR text, bounded tolerance
    STRUCTURED = "structured"  # deterministic schema mapper, no text span


class ExtractionRecord(StrictModel):
    """One extracted field value with its verification provenance."""

    field: str = Field(description="Procedure-schema field id, e.g. 'geburtsdatum'")
    value: str
    span: Span | None = Field(
        default=None,
        description="Source span in normalized coordinates; None only for "
        "MatchMode.STRUCTURED (mapper fields have payload provenance instead)",
    )
    match_mode: MatchMode
    match_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Fuzzy match score; required when match_mode is FUZZY",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    extractor_id: str = Field(description="e.g. 'mapper:v0', 'llm:mistral-...'")

    @model_validator(mode="after")
    def _check_mode_invariants(self) -> "ExtractionRecord":
        if self.match_mode is MatchMode.STRUCTURED:
            if self.span is not None:
                raise ValueError("structured extractions carry no text span")
        else:
            if self.span is None:
                raise ValueError(f"{self.match_mode} extraction requires a span")
        if self.match_mode is MatchMode.FUZZY and self.match_score is None:
            raise ValueError("fuzzy match requires a match_score")
        return self


class ExtractionSet(Stamped):
    """All verified extractions for one envelope."""

    envelope_id: str
    case_id: str
    procedure_id: str | None = None
    records: list[ExtractionRecord] = Field(default_factory=list)
    discarded_count: int = Field(
        ge=0,
        default=0,
        description="Number of extractor outputs discarded by span "
        "verification; feeds the tier decision toward tier 3",
    )
