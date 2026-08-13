"""Schema mapper: paths, missing fields, and the discarded counter."""

from __future__ import annotations

from typing import Any, cast

from engine.config_loader import ConfigBundle, FieldMapEntry
from engine.extract import extract_all, map_payload, resolve_path
from engine.journal.store import InMemoryJournalStore
from schemas.common import VersionStamp
from schemas.events import EventType
from schemas.extraction import MatchMode
from tests.factories import FIXED_NOW, make_envelope

PAYLOAD = {
    "antragsteller": {
        "geburtsdatum": "1959-04-17",
        "versicherungsnummer": "17045917B012",
    },
    "antrag": {"rentenart": "regelaltersrente", "kinder": 2, "eilbeduerftig": False},
}
FIELD_MAP = [
    FieldMapEntry(path="antragsteller.geburtsdatum", field="geburtsdatum"),
    FieldMapEntry(path="antrag.rentenart", field="rentenart"),
]


def test_resolve_path_walks_nested_mappings() -> None:
    assert resolve_path(PAYLOAD, "antragsteller.geburtsdatum") == "1959-04-17"


def test_resolve_path_returns_none_for_unknown_paths() -> None:
    assert resolve_path(PAYLOAD, "antragsteller.gibtsnicht") is None
    assert resolve_path(PAYLOAD, "antrag.rentenart.zu.tief") is None
    assert resolve_path(PAYLOAD, "") is None


def test_map_payload_produces_structured_records() -> None:
    records, discarded = map_payload(PAYLOAD, FIELD_MAP)
    assert discarded == []
    assert [record.field for record in records] == ["geburtsdatum", "rentenart"]
    for record in records:
        assert record.match_mode is MatchMode.STRUCTURED
        assert record.span is None  # structured extractions carry no text span
        assert record.confidence == 1.0
        assert record.extractor_id == "mapper:v0"


def test_missing_paths_are_skipped_and_counted() -> None:
    records, discarded = map_payload(
        PAYLOAD,
        [
            *FIELD_MAP,
            FieldMapEntry(path="antragsteller.iban", field="iban"),
            FieldMapEntry(path="antrag.gibtsnicht", field="phantom"),
        ],
    )
    assert [record.field for record in records] == ["geburtsdatum", "rentenart"]
    assert discarded == ["iban", "phantom"]


def test_non_scalar_and_empty_values_are_discarded() -> None:
    records, discarded = map_payload(
        {"antrag": {"anlagen": ["a", "b"], "notiz": "   ", "block": {"x": 1}}},
        [
            FieldMapEntry(path="antrag.anlagen", field="anlagen"),
            FieldMapEntry(path="antrag.notiz", field="notiz"),
            FieldMapEntry(path="antrag.block", field="block"),
        ],
    )
    assert records == []
    assert discarded == ["anlagen", "notiz", "block"]


def test_numbers_and_booleans_are_rendered_as_text() -> None:
    records, _ = map_payload(
        PAYLOAD,
        [
            FieldMapEntry(path="antrag.kinder", field="kinder"),
            FieldMapEntry(path="antrag.eilbeduerftig", field="eilbeduerftig"),
        ],
    )
    assert [record.value for record in records] == ["2", "false"]


def test_extract_all_emits_an_extracted_event(config: ConfigBundle) -> None:
    journal = InMemoryJournalStore()
    envelope = make_envelope(PAYLOAD)
    versions = VersionStamp(schema_version="0.1.0")
    procedure = config.procedure("altersrente")
    assert procedure is not None
    outcome = extract_all(
        envelope,
        None,
        procedure,
        config=config.extraction,
        journal=journal,
        versions=versions,
        procedure_id="altersrente",
        now=FIXED_NOW,
    )
    extractions = outcome.extractions
    # The shipped altersrente field map has five entries; this payload carries
    # geburtsdatum, versicherungsnummer and rentenart, so two are discarded.
    assert extractions.discarded_count == len(procedure.field_map) - len(
        extractions.records
    )
    assert extractions.procedure_id == "altersrente"
    events = journal.read(envelope.case_id)
    assert [event.type for event in events] == [EventType.EXTRACTED]
    payload = cast(dict[str, Any], events[0].payload)
    assert set(payload["discarded_fields"]) == {"rentenbeginn", "auslandsbezug"}
    assert payload["record_count"] == 3
    assert payload["extractor_ids"] == ["mapper:v0"]
    assert payload["verification"] == {
        "proposals": 0,
        "verified": 0,
        "discarded": 0,
        "failures": {},
        "replay": {
            "entries": 0,
            "proposed": 0,
            "anchor_missing": 0,
            "placeholder_missing": 0,
        },
        "by_part": [],
    }


def test_unknown_procedure_maps_nothing(config: ConfigBundle) -> None:
    """No field map means no records and nothing to discard."""
    records, discarded = map_payload(PAYLOAD, [])
    assert (records, discarded) == ([], [])
    assert config.procedure("gibtsnicht") is None
