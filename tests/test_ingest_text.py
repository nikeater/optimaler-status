"""Free text through the privacy boundary, and out the other side as a layer.

Part 04 sealed payload PATHS. A letter has no paths, so the boundary now also
seals SPANS: the detector union finds an identity range in the prose and the
range - not the sentence around it - is replaced by a placeholder. Everything
below depends on that happening BEFORE the text layer is built, because the
normalized text and every offset in it live in redacted coordinates (ADR-019).

The tests are grouped by the three things that can go wrong: the span sealer
itself (offsets, tokens, witness), the whole-envelope verification that has to
cover prose as well as leaves, and the ingest adapter that decides which part is
born-digital and which is OCR - a decision that later selects EXACT or
bounded-fuzzy span matching.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from engine.config_loader import ConfigBundle
from engine.ingest import (
    TEXT_PART_PREFIX,
    FitConnectSubmission,
    build_ingest,
    text_parts_of,
)
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import (
    InMemoryVaultStore,
    Kind,
    PlaceholderRegistry,
    RedactionRefusedError,
    SeededTokenSource,
    find_placeholders,
    merge_reports,
    redact_payload,
    seal_text,
    seal_texts,
    sweep_texts,
    text_seal_detector,
    verify_texts,
)
from engine.redact.verify import VerificationReport
from engine.textlayer import build_text_layer
from schemas.common import SourceType
from schemas.events import EventType

VSNR = "65170839J003"
LETTER = (
    "Sehr geehrte Damen und Herren,\n\n"
    f"meine Versicherungsnummer lautet {VSNR}. Ich beantrage eine "
    "Regelaltersrente.\nRentenart: regelaltersrente\n"
    f"Zweite Nennung derselben Nummer: {VSNR}.\n\n"
    "Mit freundlichen Gruessen"
)
DETECTOR = text_seal_detector(with_ner=False)


def submission(
    *,
    body: str | None = LETTER,
    attachments: list[dict[str, Any]] | None = None,
    channel: str = "email",
    body_source_type: str | None = None,
    submission_id: str = "text-0001",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "submissionId": submission_id,
        "destinationId": "drv-bund-eingang-test",
        "channel": channel,
        "submittedAt": "2026-08-11T09:00:00+00:00",
        "procedureHint": "altersrente",
        "data": {"antrag": {"rentenart": "regelaltersrente"}},
        "attachments": attachments if attachments is not None else [],
    }
    if body is not None:
        payload["bodyText"] = body
    if body_source_type is not None:
        payload["bodySourceType"] = body_source_type
    return payload


def ingest(config: ConfigBundle, payload: dict[str, Any]) -> Any:
    return build_ingest(
        payload,
        versions=config.version_stamp(),
        policy=config.redaction,
        token_source=SeededTokenSource(7),
        text_detector=DETECTOR,
    )


# ------------------------------------------------------- the span sealer ---


def test_a_span_is_replaced_and_the_sentence_around_it_survives() -> None:
    registry = PlaceholderRegistry(SeededTokenSource(1))
    sealed = seal_text(
        LETTER, label="part-text-0", registry=registry, detector=DETECTOR
    )
    assert VSNR not in sealed.text
    assert "Ich beantrage eine Regelaltersrente." in sealed.text
    assert "Rentenart: regelaltersrente" in sealed.text
    assert sealed.sealed_count >= 2


def test_two_mentions_of_the_same_value_get_two_different_tokens() -> None:
    """Token equality must not become a channel that says 'same person'."""
    registry = PlaceholderRegistry(SeededTokenSource(2))
    sealed = seal_text(
        LETTER, label="part-text-0", registry=registry, detector=DETECTOR
    )
    vsnr_tokens = [
        placeholder.token
        for placeholder in find_placeholders(sealed.text)
        if placeholder.kind is Kind.VSNR
    ]
    assert len(vsnr_tokens) == 2
    assert len(set(vsnr_tokens)) == 2


def test_the_witness_receives_the_raw_span_text() -> None:
    """A Versicherungsnummer sealed out of a letter still has to be checkable
    against the birth date it encodes (ADR-017), exactly like a sealed leaf."""
    witness: dict[str, str] = {}
    sealed = seal_text(
        LETTER,
        label="part-text-0",
        registry=PlaceholderRegistry(SeededTokenSource(3)),
        detector=DETECTOR,
        witness=witness,
    )
    assert VSNR in witness.values()
    for entry in sealed.entries:
        assert entry.part_id == "part-text-0"
        assert entry.path is None, "prose has no payload path to invent"
        assert entry.span is not None
        start, end = entry.span
        assert LETTER[start:end] == json.loads(entry.value_json)


def test_nothing_to_seal_leaves_the_text_exactly_as_it_was() -> None:
    registry = PlaceholderRegistry(SeededTokenSource(4))
    for text in ("", "Ich beantrage eine Regelaltersrente."):
        sealed = seal_text(
            text, label="part-text-0", registry=registry, detector=DETECTOR
        )
        assert sealed.text == text
        assert sealed.entries == ()


def test_seal_texts_works_over_a_mapping_of_parts() -> None:
    working, entries = seal_texts(
        {"part-text-0": LETTER, "part-text-1": "Ohne Angaben."},
        registry=PlaceholderRegistry(SeededTokenSource(5)),
        detector=DETECTOR,
    )
    assert VSNR not in working["part-text-0"]
    assert working["part-text-1"] == "Ohne Angaben."
    assert {entry.part_id for entry in entries} == {"part-text-0"}


def test_the_sealing_union_is_deterministic_by_default() -> None:
    """The gate never depends on an optional wheel."""
    assert text_seal_detector().uses_ner is False


# ---------------------------------------------------------- the sweep ---


def test_the_prose_sweep_asks_both_questions() -> None:
    """The recall-first union finds identity; the precision-first one finds
    anything imitating the reserved placeholder syntax."""
    forged = "Vorgang [[PII|VSNR|NOTAREALTOKEN]] mit Adresse Musterweg 3, 10115 Berlin"
    report = sweep_texts({"part-text-0": forged}, detector=DETECTOR)
    recognizers = {finding.recognizer_id for finding in report.findings}
    assert "placeholder_collision" in recognizers, "a forged token has to be residue"
    kinds = {finding.kind for finding in report.findings}
    assert Kind.ADDR in kinds, "the narrow profile alone would miss the address"
    assert report.scanned_leaves == 1
    # The narrow profile on its own does NOT see the address - which is exactly
    # why sweep_texts runs two unions instead of one.
    assert Kind.ADDR not in {
        finding.kind for finding in verify_texts({"part-text-0": forged}).findings
    }


def test_the_prose_sweep_defaults_to_the_recall_first_union() -> None:
    address = {"part-text-0": "Anschrift: Musterweg 3, 10115 Musterstadt"}
    assert not sweep_texts(address).clean


def test_merging_reports_deduplicates_and_orders() -> None:
    one = sweep_texts({"a": "Anschrift: Musterweg 3"}, detector=DETECTOR)
    merged = merge_reports(one, one, VerificationReport())
    assert merged.findings == tuple(sorted(one.findings))
    assert len(merged.findings) == len(one.findings)


# -------------------------------------------------------- the adapter ---


def test_the_body_and_every_attachment_with_text_become_parts() -> None:
    parts = text_parts_of(
        FitConnectSubmission.model_validate(
            submission(
                attachments=[
                    {"ref": "a1", "text": "Anlage eins"},
                    {"ref": "a2"},
                    {"ref": "a3", "text": "   "},
                    {"ref": "a4", "text": "Anlage zwei", "sourceType": "ocr"},
                ]
            )
        )
    )
    assert [part.part_id for part in parts] == [
        f"{TEXT_PART_PREFIX}{index}" for index in range(3)
    ]
    assert [part.source_type for part in parts] == [
        SourceType.BORN_DIGITAL,
        SourceType.BORN_DIGITAL,
        SourceType.OCR,
    ]


@pytest.mark.parametrize(
    ("channel", "declared", "expected"),
    [
        ("email", None, SourceType.BORN_DIGITAL),
        ("scan", None, SourceType.OCR),
        ("fit_connect", None, SourceType.BORN_DIGITAL),
        ("scan", "born_digital", SourceType.BORN_DIGITAL),
        ("email", "ocr", SourceType.OCR),
    ],
)
def test_the_source_type_follows_the_declaration_then_the_channel(
    channel: str, declared: str | None, expected: SourceType
) -> None:
    """Load-bearing: the source type selects EXACT or bounded-FUZZY matching."""
    parts = text_parts_of(
        FitConnectSubmission.model_validate(
            submission(channel=channel, body_source_type=declared)
        )
    )
    assert parts[0].source_type is expected


def test_an_unknown_attachment_source_type_falls_back_to_the_channel() -> None:
    """No reason to drop the text, and no reason to claim 'born digital'."""
    parts = text_parts_of(
        FitConnectSubmission.model_validate(
            submission(
                body=None,
                channel="scan",
                attachments=[{"text": "Gescannt", "sourceType": "faxpapier"}],
            )
        )
    )
    assert parts[0].source_type is SourceType.OCR


def test_a_submission_without_prose_produces_no_text_part() -> None:
    assert (
        text_parts_of(FitConnectSubmission.model_validate(submission(body="  "))) == ()
    )


# ------------------------------------------------------------- ingest ---


def test_the_envelope_carries_redacted_text_and_only_that(
    config: ConfigBundle,
) -> None:
    result = ingest(config, submission())
    parts = {part.part_id: part for part in result.envelope.parts}
    letter = parts["part-text-0"]
    assert letter.redacted_text is not None
    assert VSNR not in letter.redacted_text
    assert letter.media_type == "text/plain"
    assert letter.structured_payload is None
    assert parts["part-structured-0"].redacted_text is None
    assert result.envelope.redaction_verified is True
    assert result.text_sealed_counts["part-text-0"] >= 2
    assert result.text_sealed_count == sum(result.text_sealed_counts.values())


def test_the_sealed_spans_are_in_the_vault_with_their_part_and_offsets(
    config: ConfigBundle,
) -> None:
    """Part 08 re-hydrates from these; a span without a part could not be found
    again, and a path invented for prose would point at nothing."""
    vault = InMemoryVaultStore()
    result = build_ingest(
        submission(),
        versions=config.version_stamp(),
        vault=vault,
        policy=config.redaction,
        token_source=SeededTokenSource(7),
        text_detector=DETECTOR,
    )
    entries = [
        entry
        for entry in vault.fetch(result.vault_ref).entries
        if entry.part_id == "part-text-0"
    ]
    assert entries
    for entry in entries:
        assert entry.path is None
        assert entry.span is not None


def test_the_received_event_says_what_was_sealed_and_never_what_it_was(
    config: ConfigBundle,
) -> None:
    journal = InMemoryJournalStore()
    result = run_pipeline(
        submission(),
        config=config,
        journal=journal,
        text_detector=DETECTOR,
    )
    events = {event.type: event for event in journal.read(result.envelope.case_id)}
    received = cast(dict[str, Any], events[EventType.RECEIVED].payload)
    assert received["text_sealed_counts"]["part-text-0"] >= 2
    assert {entry["part_id"] for entry in received["part_source_types"]} == {
        "part-structured-0",
        "part-text-0",
    }
    assert VSNR not in json.dumps(received, default=str)


def test_the_redacted_event_describes_the_layer_that_was_built(
    config: ConfigBundle,
) -> None:
    """One event for both halves: the text that was sealed IS the text that was
    normalized, and splitting them would invite a reader to doubt it."""
    journal = InMemoryJournalStore()
    result = run_pipeline(
        submission(), config=config, journal=journal, text_detector=DETECTOR
    )
    events = {event.type: event for event in journal.read(result.envelope.case_id)}
    payload = cast(dict[str, Any], events[EventType.REDACTED].payload)
    stats = payload["text_layer"]
    assert stats["part_count"] == 1
    assert stats["parts"][0]["part_id"] == "part-text-0"
    assert stats["parts"][0]["source_type"] == "born_digital"
    assert stats["parts"][0]["normalized_chars"] > 0
    assert VSNR not in json.dumps(payload, default=str)


def test_a_structured_only_item_still_records_that_it_had_no_prose(
    config: ConfigBundle, gold_v3_dir: Any
) -> None:
    payload = json.loads(
        (gold_v3_dir / "ar-0001-regelaltersrente-vollstaendig.json").read_text(
            encoding="utf-8"
        )
    )
    journal = InMemoryJournalStore()
    result = run_pipeline(payload, config=config, journal=journal)
    events = {event.type: event for event in journal.read(result.envelope.case_id)}
    assert events[EventType.REDACTED].payload["text_layer"] == {
        "part_count": 0,
        "parts": [],
    }
    assert result.text_layer is None


def test_the_layer_is_built_over_the_redacted_text(config: ConfigBundle) -> None:
    result = ingest(config, submission())
    layer = build_text_layer(result.envelope, versions=config.version_stamp())
    assert layer is not None
    assert VSNR not in layer.parts[0].normalized_text
    assert "[[PII|VSNR|" in layer.parts[0].normalized_text
    # Normalization joined the letter's line breaks; the placeholders survived
    # it unharmed, which is what makes an offset into this text meaningful.
    assert "\n" not in layer.parts[0].normalized_text


def test_prose_the_sweep_cannot_clean_refuses_the_submission(
    config: ConfigBundle,
) -> None:
    """A forged placeholder cannot be sealed away - the REDACT union does not
    look for the reserved syntax - so the second sweep still finds it and the
    item is refused before a single journal event exists."""
    forged = "Vorgang [[PII|VSNR|NOTAREALTOKEN]] bitte pruefen."
    with pytest.raises(RedactionRefusedError) as raised:
        redact_payload(
            {"antrag": {"rentenart": "regelaltersrente"}},
            policy=config.redaction,
            case_id="case-forged",
            created_at=None,  # type: ignore[arg-type]
            token_source=SeededTokenSource(9),
            texts={"part-text-0": forged},
            text_detector=DETECTOR,
        )
    payload = raised.value.as_payload()
    paths = [finding["path"] for finding in payload["findings"]]
    assert any(str(path).startswith("text:") for path in paths)
    assert "NOTAREALTOKEN" not in json.dumps(payload)


def test_residue_that_appears_only_after_substitution_gets_a_second_pass(
    config: ConfigBundle,
) -> None:
    """The re-seal round exists for the narrow case where sealing changed the
    text enough for a recognizer to see something new at a seam. It is one
    round, never a loop."""
    calls: list[str] = []
    real = DETECTOR

    class SeesMoreTheSecondTime:
        profile = real.profile

        def scan(self, text: str) -> Any:
            calls.append(text)
            return real.scan(text)

    outcome = redact_payload(
        {"antrag": {"rentenart": "regelaltersrente"}},
        policy=config.redaction,
        case_id="case-second",
        created_at=None,  # type: ignore[arg-type]
        token_source=SeededTokenSource(11),
        texts={"part-text-0": LETTER},
        text_detector=SeesMoreTheSecondTime(),  # type: ignore[arg-type]
    )
    assert outcome.verified is True
    assert outcome.text_sealed_count >= 2
    assert VSNR not in outcome.texts["part-text-0"]
    assert calls, "the injected union really was the one that sealed"
    assert VSNR not in json.dumps(outcome.summary(), default=str)
