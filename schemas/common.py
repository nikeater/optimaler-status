"""Shared primitives used across all contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for all contracts: unknown fields are hard errors."""

    model_config = ConfigDict(extra="forbid", frozen=False)


class Channel(str, Enum):
    """Inbound channel the item arrived through."""

    FIT_CONNECT = "fit_connect"
    EMAIL = "email"
    SCAN = "scan"


class SourceType(str, Enum):
    """Origin quality of a text part; selects span-match mode."""

    BORN_DIGITAL = "born_digital"  # exact span matching
    OCR = "ocr"  # bounded-fuzzy span matching


class Tier(int, Enum):
    """Triage tier. Higher number = more human oversight.

    Monotonicity convention used everywhere: anomaly evidence may only
    INCREASE the tier number (add oversight), never decrease it.
    """

    CLEAR_AND_COMPLETE = 1
    INCOMPLETE_BUT_ROUTABLE = 2
    FULL_HUMAN_REVIEW = 3


class Span(StrictModel):
    """Half-open character range [start, end) in NORMALIZED text coordinates.

    Translate back to original-document coordinates via the part's OffsetMap.
    """

    part_id: str = Field(description="ContentPart this span belongs to")
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    def model_post_init(self, __context: object) -> None:
        if self.end < self.start:
            raise ValueError(f"span end {self.end} < start {self.start}")


class VersionStamp(StrictModel):
    """Versions of everything that produced an artifact; journal-stamped."""

    schema_version: str
    taxonomy_version: str | None = None
    rules_version: str | None = None
    decision_table_version: str | None = None
    thresholds_version: str | None = None
    prompt_version: str | None = None
    model_id: str | None = None
    feature_set_version: str | None = None


class Stamped(StrictModel):
    """Mixin fields for artifacts that carry provenance."""

    created_at: datetime
    versions: VersionStamp
