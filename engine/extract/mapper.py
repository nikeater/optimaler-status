"""Deterministic schema mapper: structured payload paths to field values.

No model, no guessing: a declarative field map (``config/procedures/*.yaml``)
says which payload path carries which procedure-schema field, and anything that
does not resolve is dropped. Dropped entries are counted in ``discarded_count``,
which pushes the item toward tier 3 - the same lever span verification pulls for
the text path (:mod:`engine.extract.verify`), which is why the decision table
needed no new field when prose arrived.

Mapper output is ``MatchMode.STRUCTURED``: no text span exists (the value came
from a payload path, not from prose), confidence is 1.0 because there is nothing
probabilistic about reading a JSON key.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from engine.config_loader import FieldMapEntry
from engine.redact import scalar_text
from schemas.extraction import ExtractionRecord, MatchMode

EXTRACTOR_ID = "mapper:v0"


def resolve_path(payload: Mapping[str, Any], path: str) -> object | None:
    """Resolve a dotted path in a nested mapping; None when it does not exist."""
    current: object = payload
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def map_payload(
    payload: Mapping[str, Any],
    field_map: Sequence[FieldMapEntry],
) -> tuple[list[ExtractionRecord], list[str]]:
    """Apply a field map to a payload.

    Returns:
        The extraction records and the list of field ids that were discarded
        because their path was missing or carried a non-scalar/empty value.
    """
    records: list[ExtractionRecord] = []
    discarded: list[str] = []
    for entry in field_map:
        value = _as_scalar(resolve_path(payload, entry.path))
        if value is None:
            discarded.append(entry.field)
            continue
        records.append(
            ExtractionRecord(
                field=entry.field,
                value=value,
                span=None,  # structured extractions carry payload provenance
                match_mode=MatchMode.STRUCTURED,
                match_score=None,
                confidence=1.0,
                extractor_id=EXTRACTOR_ID,
            )
        )
    return records, discarded


#: Rendering a payload leaf as a field value is defined once, in
#: ``engine.redact.seal``: the witness has to hand a validator exactly the string
#: the mapper would have produced from the same raw value, or a sealed field
#: would validate differently from an open one (a Versicherungsnummer submitted
#: with stray whitespace would suddenly fail its format pattern). Two copies of
#: that rule would drift the day one of them learns about a new scalar type.
_as_scalar = scalar_text
