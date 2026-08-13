"""Envelope builder: one shape for every channel, sealed, and the RECEIVED event."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from engine.ingest import build_envelope, build_ingest, ingest_submission
from engine.ingest.envelope import STRUCTURED_PART_ID, case_id_for, structured_payload
from engine.journal.store import InMemoryJournalStore
from engine.redact import (
    InMemoryVaultStore,
    Kind,
    SeededTokenSource,
    parse_placeholder,
)
from schemas.common import Channel, SourceType, VersionStamp
from schemas.events import ActorKind, EventType
from tests.factories import FIXED_NOW, TEST_VERSIONS

SUBMISSION: dict[str, Any] = {
    "submissionId": "s1-test",
    "destinationId": "drv-bund-eingang-test",
    "procedureHint": "altersrente",
    "channel": "fit_connect",
    "submittedAt": "2026-08-03T09:12:00+00:00",
    "data": {"antrag": {"rentenart": "regelaltersrente"}},
    "attachments": [],
}

IDENTITY_SUBMISSION: dict[str, Any] = {
    **SUBMISSION,
    "data": {
        "antragsteller": {
            "versicherungsnummer": "17170459B012",
            "geburtsdatum": "1959-04-17",
            "anschrift": {
                "strasse": "Kirchgasse",
                "hausnummer": "2",
                "plz": "24103",
                "ort": "Beispielstadt",
            },
        },
        "antrag": {"rentenart": "regelaltersrente", "rentenbeginn": "2026-11-01"},
    },
}


def test_envelope_carries_one_structured_part() -> None:
    envelope = build_envelope(SUBMISSION, versions=TEST_VERSIONS)
    assert envelope.envelope_id == "env-s1-test"
    assert envelope.case_id == case_id_for("s1-test")
    assert envelope.channel is Channel.FIT_CONNECT
    assert envelope.procedure_hint == "altersrente"
    assert len(envelope.parts) == 1
    part = envelope.parts[0]
    assert part.part_id == STRUCTURED_PART_ID
    assert part.source_type is SourceType.BORN_DIGITAL
    assert part.redacted_text is None
    # Nothing identity-classed in this fixture, so the working copy is the data.
    assert part.structured_payload == SUBMISSION["data"]


# ----------------------------------------------- the two part-01 hard-codes ---


def test_the_vault_ref_is_minted_and_not_derivable_from_the_submission() -> None:
    """Part 01 shipped ``vault:{submission_id}``; part 04 mints a real handle."""
    first = build_ingest(SUBMISSION, versions=TEST_VERSIONS)
    second = build_ingest(SUBMISSION, versions=TEST_VERSIONS)
    assert first.vault_ref.startswith("vault-")
    assert "s1-test" not in first.vault_ref
    # Two ingests of the SAME submission get different handles: the reference
    # carries no information about the case it belongs to.
    assert first.vault_ref != second.vault_ref
    assert len(first.vault_ref) == len("vault-") + 26


def test_redaction_verified_is_computed_not_asserted() -> None:
    """The flag is whatever the post-redaction sweep found, and nothing else."""
    result = build_ingest(IDENTITY_SUBMISSION, versions=TEST_VERSIONS)
    assert result.redaction_verified is True
    assert result.envelope.redaction_verified is result.redaction_verified
    assert result.auto_sealed_paths == ()


def test_identity_paths_are_sealed_before_the_envelope_exists() -> None:
    result = build_ingest(
        IDENTITY_SUBMISSION, versions=TEST_VERSIONS, token_source=SeededTokenSource(7)
    )
    payload = structured_payload(result.envelope)
    applicant = payload["antragsteller"]
    assert parse_placeholder(applicant["versicherungsnummer"]).kind is Kind.VSNR  # type: ignore[union-attr]
    assert parse_placeholder(applicant["geburtsdatum"]).kind is Kind.GEBDAT  # type: ignore[union-attr]
    # The whole address subtree collapses into ONE placeholder string.
    assert parse_placeholder(applicant["anschrift"]).kind is Kind.ADDR  # type: ignore[union-attr]
    assert result.sealed_count == 3
    # Non-identity content is untouched.
    assert payload["antrag"]["rentenart"] == "regelaltersrente"
    serialized = json.dumps(payload, ensure_ascii=False)
    for secret in ("17170459B012", "1959-04-17", "Kirchgasse", "24103"):
        assert secret not in serialized


def test_the_witness_resolves_sealed_scalars_but_not_the_subtree() -> None:
    result = build_ingest(IDENTITY_SUBMISSION, versions=TEST_VERSIONS)
    payload = structured_payload(result.envelope)
    applicant = payload["antragsteller"]
    assert result.witness.resolve(applicant["versicherungsnummer"]) == "17170459B012"
    assert result.witness.resolve(applicant["geburtsdatum"]) == "1959-04-17"
    # ADDR declares `witness: no`: nothing downstream reads its subkeys.
    assert result.witness.resolve(applicant["anschrift"]) is None
    assert len(result.witness) == 2


def test_the_witness_never_prints_what_it_holds() -> None:
    result = build_ingest(IDENTITY_SUBMISSION, versions=TEST_VERSIONS)
    assert repr(result.witness) == "<Witness 2 entries>"
    assert "17170459B012" not in f"{result.witness!r} {result.witness}"


def test_the_sealed_record_lands_in_the_vault() -> None:
    vault = InMemoryVaultStore()
    result = build_ingest(IDENTITY_SUBMISSION, versions=TEST_VERSIONS, vault=vault)
    assert vault.exists(result.vault_ref)
    record = vault.fetch(result.vault_ref)
    assert record.case_id == case_id_for("s1-test")
    assert {entry.kind for entry in record.entries} == {
        Kind.VSNR,
        Kind.GEBDAT,
        Kind.ADDR,
    }
    address = next(entry for entry in record.entries if entry.kind is Kind.ADDR)
    assert address.value()["ort"] == "Beispielstadt"


def test_an_absent_identity_path_stays_absent() -> None:
    """Sealing must never turn "no answer" into a placeholder."""
    payload = {
        **SUBMISSION,
        "data": {"antragsteller": {"geburtsdatum": "  "}, "antrag": {}},
    }
    result = build_ingest(payload, versions=TEST_VERSIONS)
    working = structured_payload(result.envelope)
    assert working["antragsteller"] == {"geburtsdatum": "  "}
    assert result.sealed_count == 0


def test_sealing_is_idempotent_over_an_already_sealed_payload() -> None:
    once = build_ingest(IDENTITY_SUBMISSION, versions=TEST_VERSIONS)
    working = structured_payload(once.envelope)
    twice = build_ingest({**SUBMISSION, "data": working}, versions=TEST_VERSIONS)
    assert twice.sealed_count == 0
    assert structured_payload(twice.envelope) == working


# ------------------------------------------------------------- raw refs ---


def test_raw_ref_hashes_the_payload_as_received() -> None:
    """The digest identifies the ORIGINAL artifact, not the redacted copy."""
    envelope = build_envelope(IDENTITY_SUBMISSION, versions=TEST_VERSIONS)
    other = build_envelope(IDENTITY_SUBMISSION, versions=TEST_VERSIONS)
    assert envelope.raw_refs[0].ref_id == "s1-test"
    assert envelope.raw_refs[0].media_type == "application/json"
    assert len(envelope.raw_refs[0].sha256 or "") == 64
    # Two ingests draw different placeholders; the digest must not move with
    # them, or a RawRef would stop identifying the artifact it points at.
    assert envelope.raw_refs[0].sha256 == other.raw_refs[0].sha256


def test_attachments_become_raw_refs() -> None:
    payload = {
        **SUBMISSION,
        "attachments": [
            {"id": "att-1", "mediaType": "application/pdf", "filename": "bescheid.pdf"},
            {},
        ],
    }
    envelope = build_envelope(payload, versions=TEST_VERSIONS)
    assert [ref.ref_id for ref in envelope.raw_refs] == [
        "s1-test",
        "att-1",
        "s1-test-att-1",
    ]
    assert envelope.raw_refs[1].filename == "bescheid.pdf"
    assert envelope.raw_refs[2].media_type == "application/octet-stream"


def test_snake_case_keys_are_accepted_too() -> None:
    envelope = build_envelope(
        {"submission_id": "s1-alt", "procedure_hint": "reha", "data": {}},
        versions=TEST_VERSIONS,
        now=FIXED_NOW,
    )
    assert envelope.procedure_hint == "reha"
    assert envelope.created_at == FIXED_NOW


def test_unknown_metadata_is_ignored_not_rejected() -> None:
    envelope = build_envelope(
        {**SUBMISSION, "serviceType": {"name": "Antrag"}, "weirdField": 1},
        versions=TEST_VERSIONS,
    )
    assert envelope.envelope_id == "env-s1-test"


def test_missing_submission_id_is_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        build_envelope({"data": {}}, versions=TEST_VERSIONS)


def test_missing_channel_defaults_to_fit_connect() -> None:
    envelope = build_envelope(
        {"submissionId": "x", "data": {}}, versions=TEST_VERSIONS, now=FIXED_NOW
    )
    assert envelope.channel is Channel.FIT_CONNECT


# ------------------------------------------------------------- RECEIVED ---


def test_ingest_emits_received_with_a_full_version_stamp() -> None:
    journal = InMemoryJournalStore()
    versions = VersionStamp(schema_version="0.1.0", rules_version="routing_v0")
    result = ingest_submission(
        SUBMISSION, journal=journal, versions=versions, now=FIXED_NOW
    )
    events = journal.read(result.envelope.case_id)
    assert [event.type for event in events] == [EventType.RECEIVED]
    event = events[0]
    assert event.sequence == 0
    assert event.actor.kind is ActorKind.SYSTEM
    assert event.versions.rules_version == "routing_v0"
    assert event.payload["procedure_hint"] == "altersrente"
    assert event.payload["part_ids"] == [STRUCTURED_PART_ID]


def test_received_records_the_seal_without_recording_a_value() -> None:
    journal = InMemoryJournalStore()
    result = ingest_submission(
        IDENTITY_SUBMISSION, journal=journal, versions=TEST_VERSIONS, now=FIXED_NOW
    )
    payload = journal.read(result.envelope.case_id)[0].payload
    assert payload["vault_ref"] == result.vault_ref
    assert payload["redaction_verified"] is True
    assert payload["sealed_count"] == 3
    # VersionStamp has no redaction field and schemas are contracts, so the
    # policy id rides in the payload (ADR-017).
    assert payload["redaction_policy_id"] == "identity_fields_v1"
    assert "17170459B012" not in json.dumps(payload, ensure_ascii=False)


def test_structured_payload_helper_handles_text_only_parts() -> None:
    envelope = build_envelope(SUBMISSION, versions=TEST_VERSIONS)
    assert structured_payload(envelope) == SUBMISSION["data"]
    envelope.parts[0].structured_payload = None
    assert structured_payload(envelope) == {}


def test_gold_fixtures_are_ingestible(gold_dir: Path) -> None:
    for path in sorted(gold_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        envelope = build_envelope(payload, versions=TEST_VERSIONS)
        assert envelope.case_id.startswith("case-s1-")
        assert envelope.redaction_verified is True
