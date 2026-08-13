"""The deterministic text extractor, and the orchestration that merges readers.

The replay extractor exists so the verification machinery runs in full on every
gold item, on any machine, without a model. Two things are therefore worth
testing hard: that it is a pure function of (fixture, layer) - the same inputs
give byte-identical proposals every time - and that a fixture which disagrees
with the sealed letter produces NO proposal rather than a guessed one.

The orchestration tests below it pin the merge rules: a payload key beats a
sentence, a field the mapper could not fill and the text could stops counting
as a loss, and the EXTRACTED payload says how much was verified without saying
what was read.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pytest
from pydantic import ValidationError

from engine.config_loader import ConfigBundle
from engine.extract import (
    FIXTURE_KEY,
    ExtractionOutcome,
    FixtureEntry,
    Proposal,
    extract_all,
    field_descriptions,
    fixture_from_payload,
    replay_proposals,
)
from engine.journal.store import InMemoryJournalStore
from schemas.events import EventType
from schemas.extraction import MatchMode
from tests.factories import FIXED_NOW, TEST_VERSIONS, make_envelope, make_text_layer

TOKEN = "[[PII|VSNR|QRSTVWXZ2345]]"
LETTER = (
    "Sehr geehrte Damen und Herren, hiermit beantrage ich eine Rente. "
    "Rentenart: regelaltersrente. Rentenbeginn: 2026-11-01. "
    f"Versicherungsnummer: {TOKEN}. Mit freundlichen Gruessen"
)
LAYER = make_text_layer(("part-text-0", "born_digital", LETTER))

FIXTURE = (
    FixtureEntry(
        field="rentenart",
        part_id="part-text-0",
        anchor="Rentenart:",
        mode="literal",
        value="regelaltersrente",
        # The quote spans the LABEL and the value: a quote that WAS the value
        # would make the second lock true by construction.
        quote="Rentenart: regelaltersrente",
    ),
    FixtureEntry(
        field="versicherungsnummer",
        part_id="part-text-0",
        anchor="Versicherungsnummer:",
        mode="sealed",
    ),
)


# ------------------------------------------------------------- fixtures ---


def test_a_submission_without_a_fixture_has_no_fixture() -> None:
    assert fixture_from_payload({"submissionId": "x"}) == ()


def test_a_malformed_fixture_is_a_hard_error_not_a_silent_skip() -> None:
    """It can only come from the generator, and a broken sidecar has to be
    heard during the build rather than produce a corpus that extracts nothing."""
    with pytest.raises(ValueError, match=FIXTURE_KEY):
        fixture_from_payload({FIXTURE_KEY: "not a list"})
    with pytest.raises(ValidationError):
        fixture_from_payload({FIXTURE_KEY: [{"field": "x"}]})
    with pytest.raises(ValueError, match="literal"):
        fixture_from_payload(
            {FIXTURE_KEY: [{"field": "x", "part_id": "p", "anchor": "A:"}]}
        )


def test_a_well_formed_fixture_round_trips() -> None:
    entries = fixture_from_payload(
        {
            FIXTURE_KEY: [
                {
                    "field": "rentenart",
                    "part_id": "part-text-0",
                    "anchor": "Rentenart:",
                    "mode": "literal",
                    "value": "regelaltersrente",
                    "quote": "Rentenart: regelaltersrente",
                }
            ]
        }
    )
    assert entries == (FIXTURE[0],)


# --------------------------------------------------------------- replay ---


def test_replay_locates_a_literal_value_behind_its_anchor() -> None:
    proposals, stats = replay_proposals(FIXTURE[:1], LAYER, extractor_id="replay:v4")
    assert stats.to_dict() == {
        "entries": 1,
        "proposed": 1,
        "anchor_missing": 0,
        "placeholder_missing": 0,
    }
    claim = proposals[0]
    assert claim.field == "rentenart"
    assert LETTER[claim.offset : claim.end] == "Rentenart: regelaltersrente"
    assert claim.value == "regelaltersrente"


def test_replay_proposes_the_placeholder_a_sealed_value_left_behind() -> None:
    proposals, stats = replay_proposals(FIXTURE[1:], LAYER, extractor_id="replay:v4")
    assert stats.proposed == 1
    assert proposals[0].value == TOKEN
    assert proposals[0].quote == TOKEN
    assert LETTER[proposals[0].offset : proposals[0].end] == TOKEN


def test_an_anchor_the_letter_does_not_carry_is_simply_not_stated() -> None:
    """A missing label IS a missing_field scenario from the inside: no proposal
    and no discard, because there was nothing to verify."""
    entry = FIXTURE[0].model_copy(update={"anchor": "Auslandsbezug:"})
    proposals, stats = replay_proposals((entry,), LAYER, extractor_id="replay:v4")
    assert proposals == ()
    assert stats.anchor_missing == 1
    assert stats.proposed == 0


def test_a_fixture_pointing_at_a_part_that_is_not_there_proposes_nothing() -> None:
    entry = FIXTURE[0].model_copy(update={"part_id": "part-text-9"})
    proposals, stats = replay_proposals((entry,), LAYER, extractor_id="replay:v4")
    assert proposals == ()
    assert stats.anchor_missing == 1


def test_a_sealed_entry_without_a_placeholder_refuses_to_invent_one() -> None:
    """The corpus and the boundary disagree; inventing a value from the
    surrounding text would be the extractor deciding what the letter meant."""
    plain = make_text_layer(
        ("part-text-0", "born_digital", "Versicherungsnummer: 17170459B012.")
    )
    proposals, stats = replay_proposals(FIXTURE[1:], plain, extractor_id="replay:v4")
    assert proposals == ()
    assert stats.placeholder_missing == 1


def test_replay_without_a_layer_or_without_entries_does_nothing() -> None:
    assert replay_proposals(FIXTURE, None, extractor_id="replay:v4")[0] == ()
    assert replay_proposals((), LAYER, extractor_id="replay:v4")[1].entries == 0


def test_replay_is_a_pure_function_of_its_inputs() -> None:
    """Same fixture, same layer, same proposals - every run, every machine."""
    runs = [
        replay_proposals(FIXTURE, LAYER, extractor_id="replay:v4") for _ in range(5)
    ]
    assert all(run == runs[0] for run in runs)
    assert [claim.field for claim in runs[0][0]] == [
        "rentenart",
        "versicherungsnummer",
    ]


# -------------------------------------------------------- orchestration ---


class StubExtractor:
    """A 'live' reader with a scripted answer, used to prove it is not trusted."""

    extractor_id = "llm:stub"

    def __init__(self, *proposals: Proposal) -> None:
        self._proposals = proposals
        self.calls: list[str] = []

    def propose(
        self, *, part_id: str, text: str, fields: Mapping[str, str]
    ) -> tuple[Proposal, ...]:
        self.calls.append(part_id)
        return self._proposals


def make_text_envelope(payload: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
    """A structured envelope plus one free-text part carrying ``LETTER``."""
    envelope = make_envelope(payload or {})
    return envelope.model_copy(
        update={
            "parts": [
                *envelope.parts,
                envelope.parts[0].model_copy(
                    update={
                        "part_id": "part-text-0",
                        "media_type": "text/plain",
                        "redacted_text": LETTER,
                        "structured_payload": None,
                    }
                ),
            ]
        }
    )


def run(
    config: ConfigBundle,
    *,
    payload: dict[str, object] | None = None,
    fixture: tuple[FixtureEntry, ...] = FIXTURE,
    live: StubExtractor | None = None,
) -> tuple[ExtractionOutcome, InMemoryJournalStore]:
    journal = InMemoryJournalStore()
    outcome = extract_all(
        make_text_envelope(payload),
        LAYER,
        config.procedure("altersrente"),
        config=config.extraction,
        journal=journal,
        versions=TEST_VERSIONS,
        fixture=fixture,
        live=live,
        procedure_id="altersrente",
        now=FIXED_NOW,
    )
    return outcome, journal


def test_text_records_join_the_structured_ones_in_one_set(
    config: ConfigBundle,
) -> None:
    outcome, _ = run(config)
    by_field = {record.field: record for record in outcome.extractions.records}
    assert by_field["rentenart"].match_mode is MatchMode.EXACT
    assert by_field["rentenart"].extractor_id == "replay:v4"
    assert by_field["versicherungsnummer"].value == TOKEN
    assert outcome.verified_count == 2
    assert outcome.text_discarded_count == 0


def test_a_payload_key_beats_a_sentence_and_the_clash_is_recorded(
    config: ConfigBundle,
) -> None:
    outcome, _ = run(config, payload={"antrag": {"rentenart": "regelaltersrente"}})
    rentenart = [
        record for record in outcome.extractions.records if record.field == "rentenart"
    ]
    assert [record.match_mode for record in rentenart] == [MatchMode.STRUCTURED]
    assert outcome.failure_counts() == {"duplicate_field": 1}


def test_a_field_the_text_recovers_stops_counting_as_a_loss(
    config: ConfigBundle,
) -> None:
    """Without this the same field would push toward tier 3 while its value sat
    in the evidence record."""
    with_text, _ = run(config)
    without_text, _ = run(config, fixture=())
    assert (
        with_text.extractions.discarded_count < without_text.extractions.discarded_count
    )
    assert set(with_text.mapper_discarded) == set(without_text.mapper_discarded)


def test_the_extracted_event_carries_verification_statistics_and_no_content(
    config: ConfigBundle,
) -> None:
    outcome, journal = run(config)
    events = journal.read("case-test")
    assert [event.type for event in events] == [EventType.EXTRACTED]
    payload = cast(dict[str, Any], events[0].payload)
    verification = payload["verification"]
    assert verification["proposals"] == 2
    assert verification["verified"] == 2
    assert verification["discarded"] == 0
    assert verification["failures"] == {}
    assert verification["by_part"] == [
        {"part_id": "part-text-0", "proposals": 2, "verified": 2, "discarded": 0}
    ]
    assert payload["text_part_ids"] == ["part-text-0"]
    assert set(payload["extractor_ids"]) == {"mapper:v0", "replay:v4"}
    rendered = str(payload)
    assert "regelaltersrente" not in rendered
    assert TOKEN not in rendered
    assert outcome.stats()["replay"]["entries"] == 2


def test_a_live_reader_is_asked_per_part_and_still_has_to_prove_itself(
    config: ConfigBundle,
) -> None:
    liar = StubExtractor(
        Proposal(
            field="rentenbeginn",
            value="2026-11-01",
            quote="Rentenbeginn: 2026-11-01",
            part_id="part-text-0",
            # Confidently wrong about where it stands.
            offset=0,
            extractor_id="llm:stub",
        )
    )
    outcome, _ = run(config, fixture=(), live=liar)
    assert liar.calls == ["part-text-0"]
    assert outcome.extractions.records == []
    assert outcome.failure_counts() == {"quote_mismatch": 1}


def test_a_live_reader_that_is_right_is_accepted_on_the_same_terms(
    config: ConfigBundle,
) -> None:
    honest = StubExtractor(
        Proposal(
            field="rentenbeginn",
            value="2026-11-01",
            quote="Rentenbeginn: 2026-11-01",
            part_id="part-text-0",
            offset=LETTER.find("Rentenbeginn: 2026-11-01"),
            extractor_id="llm:stub",
        )
    )
    outcome, _ = run(config, fixture=(), live=honest)
    assert [record.field for record in outcome.extractions.records] == ["rentenbeginn"]
    assert outcome.extractions.records[0].extractor_id == "llm:stub"


def test_a_live_reader_is_not_called_when_there_is_nothing_for_it_to_read(
    config: ConfigBundle,
) -> None:
    stub = StubExtractor()
    journal = InMemoryJournalStore()
    extract_all(
        make_envelope({}),
        None,
        config.procedure("altersrente"),
        config=config.extraction,
        journal=journal,
        versions=TEST_VERSIONS,
        live=stub,
        now=FIXED_NOW,
    )
    assert stub.calls == []


def test_a_live_reader_is_not_called_without_a_procedure(
    config: ConfigBundle,
) -> None:
    """No procedure means no field descriptions, which means no prompt."""
    stub = StubExtractor()
    journal = InMemoryJournalStore()
    outcome = extract_all(
        make_text_envelope(),
        LAYER,
        None,
        config=config.extraction,
        journal=journal,
        versions=TEST_VERSIONS,
        fixture=FIXTURE,
        live=stub,
        now=FIXED_NOW,
    )
    assert stub.calls == []
    # ... and with no procedure no field is known, so the fixture's proposals
    # are discarded rather than turned into records nobody configured.
    assert outcome.extractions.records == []
    assert outcome.failure_counts() == {"unknown_field": 2}


def test_field_descriptions_come_from_the_requirement_wording(
    config: ConfigBundle,
) -> None:
    """One definition of what a field means per procedure - the same sentences
    the completeness checker and the Nachforderung wording use."""
    descriptions = field_descriptions(config.procedure("altersrente"))
    assert descriptions["rentenart"] == "Beantragte Rentenart."
    assert set(descriptions) <= {
        entry.field for entry in config.procedures["altersrente"].field_map
    }
    assert field_descriptions(None) == {}
