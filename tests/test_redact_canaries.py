"""The canary gate: seeded fake identities never appear past redaction.

Non-negotiable and permanent (ADR-002, ADR-017). Every canary here is
a distinctive value that cannot occur by chance, and every surface a case
touches is swept for all of them: the envelope, every PipelineResult artifact,
both journal backends including the JSONL files on disk, every API response
including a 422, the eval report and the rendered metrics page, captured
logging output, and the string form of the exception raised on the refusal path.

**One place is deliberately exempt: the vault.** Holding the sealed values is
what it is for. The test asserts that too, because "nothing anywhere" would
also be satisfied by a vault that lost the data, and then part 08 could never
re-hydrate a Nachforderung.

**Part 05 extends the sweep to the text path.** The canary item now carries a
whole letter and an OCR attachment as well as its structured payload, so every
canary above travels through span sealing, the normalized layer and span
verification before the same surfaces are swept - plus the two new ones, the
layer itself and the per-proposal verification records. Two limits are asserted
rather than assumed: the letter comes out clean under the DETERMINISTIC union
alone (the gate never depends on the optional model), and an OCR-mangled
identifier does evade the union entirely, which is a documented limit
(docs/KNOWN-ERRORS.md) and not something a threshold can fix.

**Part 07 extends it to the one surface a CITIZEN reads**: the applicant outbox
(both backends, including the JSONL files on disk), the two NOTIFIED payloads,
``GET /inbox`` and ``GET /inbox/{case_id}``. The notification path is PII-free by
construction - it renders from config and never reads the submission - and this
is where "by construction" is checked rather than believed.

**Part 09 extends it to the artifact that is designed to quote its input**:
the shadow scorer's reasons. Every other surface in this system tries to say as
little as possible about an item; a reason has to say what was observed and
what was expected, which makes it the most likely place for a sealed value to
reappear. The anomaly evidence, the rendered German sentences and the feature
displays behind them are all swept - for the canary values AND for the
placeholder tokens that replaced them, because a reason quoting a random token
would be just as useless to a caseworker as one quoting a person.

**Part 11 extends it to the two surfaces a PUBLIC visitor sees first**: the
demo landing page and the synthetic-data banner that rides every page. Both
render from constants and from the deployment posture rather than from a case,
which is the design - and the design is checked here rather than believed,
including the 403 body a stranger who POSTed real data would read back.

**Part 08 adds the SECOND member of the exception list: the draft store.** A
Nachforderung is a letter to a named person, so the canaries have to be in it -
if they were not, re-hydration would not be working and the vault would be a
write-only hole. The sweep therefore asserts both directions here: the drafts
and ``GET /drafts/{case_id}`` DO carry the seeded identities, and the DRAFTED
journal payloads, the case view, the inbox, the eval report and the logs still
carry none. Two places may hold them, and both of them say so in their own
docstring.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from api.app import create_app
from api.metrics import REPORT_ENV, current_view, render_page, set_demo_posture
from engine.config_loader import ConfigBundle
from engine.demo import (
    DEMO_MODE_ENV,
    INGEST_HEADER,
    INGEST_TOKEN_ENV,
    DemoPosture,
    demo_posture,
)
from engine.dispatch import DISPATCH_DIR_ENV
from engine.draft import InMemoryDraftStore, JsonlDraftStore
from engine.draft.projection import draft_case, facts_from
from engine.ingest import build_ingest
from engine.journal import InMemoryJournalStore, JsonlJournalStore
from engine.journal.corrections import build_pool
from engine.notify import InMemoryOutbox, JsonlOutbox, notify_case
from engine.pipeline import run_pipeline
from engine.redact import (
    InMemoryVaultStore,
    JsonlVaultStore,
    Kind,
    RedactionRefusedError,
    SeededTokenSource,
    contains_placeholder,
    find_placeholders,
    mask_placeholders,
    parse_placeholder,
    redact_payload,
    sweep_texts,
    text_seal_detector,
    vsnr_checksum_ok,
)
from engine.redact.placeholders import PLACEHOLDER_SHAPED_RE
from engine.redact.recognizers import Detection, iban_checksum_ok, steuer_id_checksum_ok
from engine.review import build_index, review_metrics
from engine.score import render_reason
from eval.harness import GoldItem, GoldLabels, evaluate_corpus

# --------------------------------------------------------------- canaries ---

#: Checksum-VALID on purpose: the VERIFY profile is checksum-gated, so a canary
#: that failed its Pruefziffer would never exercise the sweep it is here to test.
CANARY_VSNR = "65170839J003"
CANARY_STEUER_ID = "86095742719"
CANARY_IBAN = "DE02120300000000202051"
CANARY_NAME = "Zenobia Kanarienvogel"
CANARY_STRASSE = "Kanarienweg 77"
CANARY_ORT = "99999 Kanarienstadt"
CANARY_ORG = "Kanarienvogel Zwitscher GmbH"
CANARY_BNR = "77777771"
CANARY_TEL = "030 1234567"
CANARY_EMAIL = "zenobia@kanarien.example"
CANARY_FREITEXT = f"zuletzt gefuehrt unter {CANARY_VSNR} bei der Firma"

#: The unit the part-10 review pages act as. Any taxonomy unit id would do;
#: the point of naming one is that the draft surfaces now require one.
REVIEW_UNIT = "Referat_312_Renten"

#: The text path (part 05). Every canary above appears again, this time in PROSE
#: rather than in a JSON leaf, because a letter has no paths for the policy to
#: name and the detector union is the only control there. The same sweep runs
#: over the same surfaces, plus the two the text path adds: the normalized layer
#: and the extraction verifications.
CANARY_LETTER = (
    "Sehr geehrte Damen und Herren,\n\n"
    f"mein Name ist Frau {CANARY_NAME}, ich wohne {CANARY_STRASSE}, "
    f"{CANARY_ORT}.\n"
    f"Meine Versicherungsnummer lautet {CANARY_VSNR}, meine Steuer-ID ist "
    f"{CANARY_STEUER_ID} und mein Konto {CANARY_IBAN}.\n"
    f"Ich war zuletzt bei der {CANARY_ORG} taetig "
    f"(Betriebsnummer {CANARY_BNR}).\n"
    f"Telefon {CANARY_TEL}, E-Mail {CANARY_EMAIL}\n\n"
    "Antragsart: feststellung_nach_aufnahme\n"
    "Taetigkeit: IT-Beratung\n\n"
    "Mit freundlichen Gruessen"
)

#: Every value that must never be seen again. Kept as one list so a new canary
#: is swept everywhere by construction rather than by remembering.
CANARIES = (
    CANARY_VSNR,
    CANARY_STEUER_ID,
    CANARY_IBAN,
    CANARY_NAME,
    CANARY_STRASSE,
    CANARY_ORT,
    CANARY_ORG,
    CANARY_BNR,
    CANARY_TEL,
    CANARY_EMAIL,
)


def canary_submission(submission_id: str = "canary-0001") -> dict[str, Any]:
    """Canaries in identity fields, in a free-text field AND in a whole letter."""
    return {
        **_structured_canary_submission(submission_id),
        "channel": "email",
        "bodyText": CANARY_LETTER,
        # A second free-text part, arriving as an attachment's extracted text,
        # so the sweep covers more than "the body was handled".
        "attachments": [
            {
                "ref": "canary-anlage",
                "text": f"Anlage: Kontoverbindung {CANARY_IBAN}",
                "sourceType": "ocr",
            }
        ],
    }


def _structured_canary_submission(submission_id: str = "canary-0001") -> dict[str, Any]:
    """The part-04 submission: canaries in identity fields and in a free-text one."""
    return {
        "submissionId": submission_id,
        "destinationId": "drv-bund-eingang-test",
        "procedureHint": "statusfeststellung",
        "channel": "fit_connect",
        "submittedAt": "2026-08-11T09:00:00+00:00",
        "data": {
            "antragsteller": {
                "versicherungsnummer": CANARY_VSNR,
                "geburtsdatum": "1939-08-17",
                "name": CANARY_NAME,
                "anschrift": {
                    "strasse": CANARY_STRASSE.rsplit(" ", 1)[0],
                    "hausnummer": "77",
                    "plz": "99999",
                    "ort": "Kanarienstadt",
                },
            },
            "auftraggeber": {
                "firmenname": CANARY_ORG,
                "betriebsnummer": CANARY_BNR,
                "anschrift": {
                    "strasse": "Kanarienweg",
                    "hausnummer": "78",
                    "plz": "99999",
                    "ort": "Kanarienstadt",
                },
            },
            "antrag": {
                "antragsart": "feststellung_nach_aufnahme",
                "antragsteller_rolle": "auftragnehmer",
                "taetigkeit_bezeichnung": "IT-Beratung",
                "taetigkeit_beginn": "2026-01-15",
                # Planted at a path the policy does NOT cover: the sweep has to
                # find it, auto-seal the leaf, and re-verify.
                "letzte_taetigkeit": CANARY_FREITEXT,
                "hinweistext": f"Konto {CANARY_IBAN}, Steuer-ID {CANARY_STEUER_ID}",
            },
        },
        "attachments": [],
    }


def canary_altersrente_submission(
    submission_id: str = "canary-0002",
) -> dict[str, Any]:
    """A canary case that produces a DRAFT: tier 2, one missing field.

    The statusfeststellung canary above lands at tier 3 and therefore owes no
    draft at all (ruling 7), so it could never show that re-hydration works.
    This one is an Altersrente with the Rentenbeginn missing: it routes, it is
    incomplete, and it owes a Nachforderung addressed to the canary applicant.
    """
    return {
        "submissionId": submission_id,
        "destinationId": "drv-bund-eingang-test",
        "procedureHint": "altersrente",
        "channel": "fit_connect",
        "submittedAt": "2026-08-11T09:00:00+00:00",
        "data": {
            "antragsteller": {
                # The birth date the canary Versicherungsnummer encodes at
                # positions 3 to 8, so the structural cross-check passes and the
                # item is incomplete for exactly one reason.
                "geburtsdatum": "1939-08-17",
                "versicherungsnummer": CANARY_VSNR,
                "name": CANARY_NAME,
                "anschrift": {
                    "strasse": CANARY_STRASSE.rsplit(" ", 1)[0],
                    "hausnummer": "77",
                    "plz": "99999",
                    "ort": "Kanarienstadt",
                },
            },
            "antrag": {"rentenart": "regelaltersrente", "auslandsbezug": "nein"},
        },
        "attachments": [],
    }


#: Anything that looks like a tag. Used to read a served page the way a reader
#: reads it, which is a different string from the one the server sent.
TAG_RE = re.compile(r"<[^>]*>")


def assert_no_canary(blob: str, where: str) -> None:
    """The one assertion this whole module exists for.

    IT READS THE BLOB TWICE SINCE PART 23, and the second read is the one that
    matters now. That part paints the working copy like code, which means a run
    of machine text arrives wrapped in elements rather than as one string - and
    a substring sweep over markup CANNOT see a value that a tag happens to fall
    inside of. A sweep that goes green by no longer being able to look is the
    worst failure mode a leak check has, so the blob is also read with every tag
    removed, which is what ends up in front of a reader.

    Stripping tags out of a JSON body or a log line is a no-op on anything this
    project emits, and where it is not, it can only ever make the check find
    MORE. The cost of the extra pass is nothing and the property it protects is
    the whole module.
    """
    stripped = TAG_RE.sub("", blob)
    for canary in CANARIES:
        assert canary not in blob, f"canary {canary!r} leaked into {where}"
        assert canary not in stripped, (
            f"canary {canary!r} leaked into {where}, split across tags"
        )


#: What a re-hydrated Altersrente draft has to contain. Not the whole canary
#: list: the draft addresses the applicant, so it carries the identity fields of
#: the letter head and nothing else - the Steuer-ID and the IBAN are not part of
#: a Nachforderung and their absence is correct rather than a leak.
DRAFT_CANARIES = (CANARY_VSNR, CANARY_STRASSE, CANARY_ORT)


def assert_canaries_present(blob: str, where: str) -> None:
    """The exception, asserted rather than assumed (part 08).

    "Nothing anywhere" would also be satisfied by a vault that lost the data,
    and then no Nachforderung could ever be addressed to anybody.
    """
    for canary in DRAFT_CANARIES:
        assert canary in blob, f"canary {canary!r} is MISSING from {where}"


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def journal_dir(tmp_path: Path) -> Path:
    return tmp_path / "journal"


@pytest.fixture
def outbox_dir(tmp_path: Path) -> Path:
    return tmp_path / "outbox"


@pytest.fixture
def drafts_dir(tmp_path: Path) -> Path:
    return tmp_path / "drafts"


@pytest.fixture
def client(
    config: ConfigBundle,
    journal_dir: Path,
    vault_dir: Path,
    outbox_dir: Path,
    drafts_dir: Path,
) -> Iterator[TestClient]:
    """The REAL app, on file-backed stores, so the sweep can read the disk."""
    app = create_app(
        config=config,
        journal=JsonlJournalStore(journal_dir),
        vault=JsonlVaultStore(vault_dir),
        outbox=JsonlOutbox(outbox_dir),
        drafts=JsonlDraftStore(drafts_dir),
    )
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------- the sweep ---


def test_no_canary_survives_the_pipeline(
    config: ConfigBundle, caplog: pytest.LogCaptureFixture
) -> None:
    """Envelope, extractions, evidence, decision, gaps, journal, logs."""
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    with caplog.at_level(logging.DEBUG):
        result = run_pipeline(
            canary_submission(), config=config, journal=journal, vault=vault
        )
    assert_no_canary(result.envelope.model_dump_json(), "the serialized envelope")
    assert_no_canary(result.extractions.model_dump_json(), "the extraction set")
    assert_no_canary(result.evidence.model_dump_json(), "the evidence record")
    assert_no_canary(result.decision.model_dump_json(), "the decision record")
    assert_no_canary(
        json.dumps([rendering.__dict__ for rendering in result.gap_renderings]),
        "the Nachforderung renderings",
    )
    assert_no_canary(repr(result.redaction), "the redaction summary")
    # The two surfaces the text path adds (part 05): the normalized layer every
    # span points into, and everything the verifier said about the proposals.
    assert result.text_layer is not None, "the canary item must produce a layer"
    assert_no_canary(result.text_layer.model_dump_json(), "the normalized text layer")
    assert result.extraction is not None
    assert_no_canary(
        json.dumps(result.extraction.stats(), ensure_ascii=False),
        "the verification statistics",
    )
    assert_no_canary(
        json.dumps(
            [outcome.describe() for outcome in result.extraction.verifications],
            ensure_ascii=False,
        ),
        "the per-proposal verification records",
    )
    # The shadow scorer (part 09). Its output is the one artifact in this system
    # that is DESIGNED to quote what it saw - a reason names a feature, an
    # observation and a reference range - so it is exactly where a sealed value
    # would be quoted next. The anomaly evidence, every rendered reason and the
    # feature displays behind them are all swept, and the placeholder TOKENS are
    # checked separately below: a reason must carry neither the value nor the
    # random token that replaced it.
    assert result.scoring is not None, "the canary item must reach the scorer"
    assert result.anomaly is not None
    assert_no_canary(result.anomaly.model_dump_json(), "the anomaly evidence")
    for reason in result.anomaly.reasons:
        assert_no_canary(render_reason(reason), f"the rendered reason {reason.feature}")
    assert result.scoring.vector is not None
    for feature in result.scoring.vector.features:
        assert_no_canary(feature.display, f"the feature display {feature.feature_id}")
        assert not contains_placeholder(feature.display)
        assert not PLACEHOLDER_SHAPED_RE.search(feature.display)
    # The applicant-facing surface (part 07): the rendered notifications and the
    # NOTIFIED payloads. Run here rather than in its own test so that a new
    # canary is swept over the citizen's copy by construction.
    outbox = InMemoryOutbox()
    notify_case(
        journal.read(result.envelope.case_id),
        config=config,
        journal=journal,
        outbox=outbox,
    )
    entries = outbox.entries(result.envelope.case_id)
    assert entries, "the canary item must have produced notifications"
    for entry in entries:
        assert_no_canary(
            entry.model_dump_json(), f"the outbox entry {entry.template_id}"
        )
    assert_no_canary(
        json.dumps(
            [
                event.model_dump(mode="json")
                for event in journal.read(result.envelope.case_id)
            ],
            ensure_ascii=False,
        ),
        "the in-memory journal (including the NOTIFIED payloads)",
    )
    assert_no_canary(caplog.text, "captured logging output")


def test_no_canary_reaches_any_api_response_or_the_files_on_disk(
    client: TestClient,
    journal_dir: Path,
    vault_dir: Path,
    outbox_dir: Path,
    drafts_dir: Path,
) -> None:
    created = client.post("/ingest", json=canary_submission())
    assert created.status_code == 201
    assert_no_canary(created.text, "the /ingest response")

    case_id = created.json()["case_id"]
    case = client.get(f"/cases/{case_id}")
    assert case.status_code == 200
    assert_no_canary(case.text, "the /cases response")

    assert_no_canary(client.get("/health").text, "the /health response")

    # The applicant's own view (part 07), page and JSON.
    inbox_page = client.get("/inbox")
    assert inbox_page.status_code == 200
    assert_no_canary(inbox_page.text, "the rendered /inbox page")
    inbox_json = client.get(f"/inbox/{case_id}")
    assert inbox_json.status_code == 200
    assert len(inbox_json.json()["notifications"]) == 2
    assert_no_canary(inbox_json.text, "the /inbox/{case_id} response")

    for path in sorted(journal_dir.glob("*.jsonl")):
        assert_no_canary(
            path.read_text(encoding="utf-8"), f"the journal file {path.name}"
        )

    outbox_files = sorted(outbox_dir.glob("*.jsonl"))
    assert outbox_files, "the outbox must have been written to disk"
    for path in outbox_files:
        assert_no_canary(
            path.read_text(encoding="utf-8"), f"the outbox file {path.name}"
        )

    # ... and the one place the values are SUPPOSED to be.
    vault_files = sorted(vault_dir.glob("*.json"))
    assert len(vault_files) == 1
    sealed = vault_files[0].read_text(encoding="utf-8")
    assert CANARY_VSNR in sealed, "the vault must actually hold the sealed value"

    # This item is tier 3 and owes no draft at all, so the draft surfaces are
    # empty rather than clean - which is a different fact and worth pinning.
    assert client.get(f"/drafts/{case_id}?unit={REVIEW_UNIT}").status_code == 404
    assert not list(drafts_dir.glob("*.jsonl"))


def test_the_drafts_route_is_the_one_api_surface_that_returns_identity(
    client: TestClient, drafts_dir: Path, journal_dir: Path, outbox_dir: Path
) -> None:
    """Part 08's exception, end to end through the real app.

    ``GET /drafts/{case_id}`` returns a letter with the applicant in it, on
    purpose, and every neighbouring route still returns none of it.
    """
    created = client.post("/ingest", json=canary_altersrente_submission())
    assert created.status_code == 201
    body = created.json()
    assert body["tier"] == 2
    assert [draft["kind"] for draft in body["drafts"]] == ["nachforderung"]
    # The ingest response says WHICH drafts exist and not what they say.
    assert_no_canary(created.text, "the /ingest response")

    case_id = body["case_id"]
    # Part 10 put this route behind the demo role model: without a unit the
    # letter is not served at all, and that refusal is part of the exception's
    # definition now.
    assert client.get(f"/drafts/{case_id}").status_code == 403
    drafts = client.get(f"/drafts/{case_id}?unit={REVIEW_UNIT}")
    assert drafts.status_code == 200
    assert_canaries_present(drafts.text, "GET /drafts/{case_id}")
    assert "nichts hier ist ein Verwaltungsakt" in drafts.text.replace(
        "nothing here is a Verwaltungsakt", "nichts hier ist ein Verwaltungsakt"
    )

    files = sorted(drafts_dir.glob("*.jsonl"))
    assert files, "the draft store must have been written to disk"
    assert_canaries_present(files[0].read_text(encoding="utf-8"), "the draft file")

    # Everything next door is unchanged: no canary in the case view, the
    # inbox, the journal on disk or the outbox on disk.
    assert_no_canary(client.get(f"/cases/{case_id}").text, "the /cases response")
    assert_no_canary(client.get("/inbox").text, "the rendered /inbox page")
    assert_no_canary(client.get(f"/inbox/{case_id}").text, "the /inbox response")
    for path in sorted(journal_dir.glob("*.jsonl")):
        assert_no_canary(path.read_text(encoding="utf-8"), f"journal file {path.name}")
    for path in sorted(outbox_dir.glob("*.jsonl")):
        assert_no_canary(path.read_text(encoding="utf-8"), f"outbox file {path.name}")


def test_the_draft_store_is_the_second_exception_and_the_journal_is_not(
    config: ConfigBundle, caplog: pytest.LogCaptureFixture
) -> None:
    """Part 08: the canaries MUST be in the letter and nowhere else.

    Both halves matter. A draft without them would mean re-hydration is not
    working and a Nachforderung could not be addressed to anybody; a DRAFTED
    payload with them would mean the audit trail had quietly become a second
    copy of the letter.
    """
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    drafts = InMemoryDraftStore()
    with caplog.at_level(logging.DEBUG):
        result = run_pipeline(
            canary_altersrente_submission(),
            config=config,
            journal=journal,
            vault=vault,
        )
        outcome = draft_case(
            journal.read(result.envelope.case_id),
            config=config,
            journal=journal,
            vault=vault,
            drafts=drafts,
            facts=facts_from(result.extractions),
        )
    assert int(result.decision.tier) == 2
    assert outcome.count == 1, outcome.blocked
    draft = outcome.drafts[0]

    assert_canaries_present(draft.body, "the prepared Nachforderung")
    assert_canaries_present(
        json.dumps(draft.model_dump(mode="json"), ensure_ascii=False),
        "the stored draft record",
    )
    # ... and nowhere else, including in the event the same function wrote.
    assert_no_canary(
        json.dumps(outcome.events[0].payload, ensure_ascii=False), "the DRAFTED payload"
    )
    assert_no_canary(
        json.dumps(draft.summary(), ensure_ascii=False), "the draft summary"
    )
    assert_no_canary(
        json.dumps(
            [event.model_dump(mode="json") for event in journal.read(draft.case_id)],
            ensure_ascii=False,
        ),
        "the journal of a case that owns a draft",
    )
    assert_no_canary(caplog.text, "captured logging output while drafting")


def test_no_canary_reaches_a_422(client: TestClient) -> None:
    """A malformed submission whose payload carries canaries."""
    malformed = canary_submission()
    malformed.pop("submissionId")
    response = client.post("/ingest", json=malformed)
    assert response.status_code == 422
    assert_no_canary(response.text, "the 422 body")

    # FastAPI's own request validation, which echoes the body by default.
    raw = client.post("/ingest", json=[CANARY_VSNR, CANARY_NAME])
    assert raw.status_code == 422
    assert_no_canary(raw.text, "FastAPI's own 422 body")


def test_no_canary_reaches_the_eval_report_or_the_metrics_page(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = GoldLabels(
        item_id="canary-eval",
        expected_unit_id="Referat_340_Clearingstelle",
        expected_tier=3,
        procedure_id="statusfeststellung",
        derived_procedure_id="statusfeststellung",
        derivation_source="hint",
    )
    item = GoldItem(
        item_id="canary-eval",
        payload=canary_submission("canary-eval"),
        labels=labels,
        path=tmp_path / "canary-eval.json",
    )
    report = evaluate_corpus([item], config=config, gold_dir=tmp_path)
    written = report.write(tmp_path / "report.json")
    assert_no_canary(written.read_text(encoding="utf-8"), "the eval report")
    assert_no_canary(report.summary(), "the eval summary")

    monkeypatch.setenv(REPORT_ENV, str(written))
    assert_no_canary(render_page(current_view()), "the rendered metrics page")


def test_no_canary_reaches_the_refusal_exception(config: ConfigBundle) -> None:
    """The refusal path is the likeliest place for a value to end up in a log."""

    class NeverClean:
        def scan(self, text: str) -> tuple[Detection, ...]:
            return (Detection(start=0, end=4, kind=Kind.VSNR, recognizer_id="stub"),)

    with pytest.raises(RedactionRefusedError) as raised:
        redact_payload(
            canary_submission()["data"],
            policy=config.redaction,
            case_id="case-canary",
            created_at=None,  # type: ignore[arg-type]
            token_source=SeededTokenSource(3),
            detector=NeverClean(),  # type: ignore[arg-type]
        )
    error = raised.value
    assert_no_canary(str(error), "str(RedactionRefusedError)")
    assert_no_canary(repr(error), "repr(RedactionRefusedError)")
    assert_no_canary(json.dumps(error.as_payload()), "the refusal API payload")


# --------------------------------------------- placeholder round-trip ---


def test_every_placeholder_parses_is_unique_and_resolves_in_the_vault(
    config: ConfigBundle,
) -> None:
    vault = InMemoryVaultStore()
    result = build_ingest(
        canary_submission(),
        versions=config.version_stamp(),
        vault=vault,
        policy=config.redaction,
        token_source=SeededTokenSource(42),
    )
    payload = result.envelope.parts[0].structured_payload or {}
    serialized = json.dumps(payload, ensure_ascii=False)
    placeholders = find_placeholders(serialized)
    tokens = [placeholder.token for placeholder in placeholders]

    assert tokens, "the canary item must have produced placeholders"
    assert len(tokens) == len(set(tokens)), "placeholder tokens must be unique"

    record = vault.fetch(result.vault_ref)
    assert set(tokens) <= record.tokens, "every placeholder must resolve in the vault"
    # The witness is a SUBSET: the address subtree is sealed without one.
    assert result.witness.tokens < record.tokens
    assert result.witness.tokens <= record.tokens


def test_the_planted_canary_is_auto_sealed_and_the_envelope_still_verifies(
    config: ConfigBundle,
) -> None:
    result = build_ingest(
        canary_submission(),
        versions=config.version_stamp(),
        vault=InMemoryVaultStore(),
        policy=config.redaction,
    )
    assert "antrag.letzte_taetigkeit" in result.auto_sealed_paths
    assert "antrag.hinweistext" in result.auto_sealed_paths
    assert result.redaction_verified is True
    assert result.envelope.redaction_verified is True
    payload = result.envelope.parts[0].structured_payload or {}
    antrag = payload["antrag"]
    assert isinstance(antrag, dict)
    placeholder = parse_placeholder(str(antrag["letzte_taetigkeit"]))
    assert placeholder is not None
    assert placeholder.kind is Kind.TEXT


def test_the_canary_letter_is_sealed_by_the_deterministic_union_alone(
    config: ConfigBundle,
) -> None:
    """The GATE path: no optional wheel, and the letter still comes out clean.

    This is the load-bearing half of ruling 2. Production may add the model
    member, but the number this project quotes has to hold without it, so the
    canary letter is deliberately written out of identifiers the deterministic
    recognizers carry - including one name, which only reaches them because it
    stands behind an Anrede.
    """
    result = build_ingest(
        canary_submission("canary-deterministic"),
        versions=config.version_stamp(),
        vault=InMemoryVaultStore(),
        policy=config.redaction,
        text_detector=text_seal_detector(with_ner=False),
    )
    assert result.redaction_verified is True
    assert result.text_sealed_count >= 10
    for part in result.envelope.parts:
        if part.redacted_text is not None:
            assert_no_canary(part.redacted_text, f"the redacted text of {part.part_id}")


def test_masking_placeholders_does_not_blind_the_sweep(config: ConfigBundle) -> None:
    """The sweep ignores hits ON a placeholder and nothing else.

    Masking exists because a model tagging a random token as a PERSON would
    make the boundary refuse its own output. It must not become a way to hide
    residue that stands NEXT to a placeholder.
    """
    sealed = f"Vorgang [[PII|VSNR|QRSTVWXZ2345]] fuer {CANARY_NAME}, {CANARY_VSNR}"
    assert mask_placeholders(sealed).count(" " * 25) == 1
    report = sweep_texts({"part-text-0": sealed})
    assert not report.clean
    assert Kind.VSNR in {finding.kind for finding in report.findings}
    # ... and a text that is nothing BUT placeholders is clean.
    assert sweep_texts({"part-text-0": "[[PII|VSNR|QRSTVWXZ2345]]"}).clean


def test_ocr_mangled_identity_can_evade_the_detector(config: ConfigBundle) -> None:
    """A documented limit, asserted rather than hidden (docs/KNOWN-ERRORS.md).

    A scan that read ``0`` as ``O`` produces a string that is still a
    Versicherungsnummer to a human and no longer one to a regular expression.
    The union does not find it, the sweep therefore does not either, and the
    mangled text reaches the working copy. The system is not silently wrong
    about the value - the CORRECT identifier never appears anywhere, because it
    never arrived - but the residue is real and no threshold fixes it. What
    fixes it is OCR confidence at the scanner, which is part 07 territory.
    """
    mangled = CANARY_VSNR.replace("0", "O").replace("1", "l")
    assert mangled != CANARY_VSNR
    letter = f"Meine Versicherungsnummer lautet {mangled}."
    detector = text_seal_detector(with_ner=False)
    assert detector.scan(letter) == (), "if this fires, the limit has been closed"
    assert sweep_texts({"part-text-0": letter}, detector=detector).clean

    result = build_ingest(
        {
            "submissionId": "canary-ocr-limit",
            "destinationId": "drv-bund-eingang-test",
            "channel": "scan",
            "submittedAt": "2026-08-11T09:00:00+00:00",
            "data": {},
            "bodyText": letter,
        },
        versions=config.version_stamp(),
        vault=InMemoryVaultStore(),
        policy=config.redaction,
        text_detector=detector,
    )
    surviving = result.envelope.parts[-1].redacted_text or ""
    assert mangled in surviving, "the limit is real: the mangled run survives"
    assert_no_canary(surviving, "the OCR-limit working copy")


def test_the_canaries_are_actually_canary_shaped() -> None:
    """A canary with a broken checksum would silently stop testing the sweep."""
    assert vsnr_checksum_ok(CANARY_VSNR)
    assert steuer_id_checksum_ok(CANARY_STEUER_ID)
    assert iban_checksum_ok(CANARY_IBAN)


def test_a_gold_item_without_canaries_is_not_falsely_cleared(
    config: ConfigBundle, gold_v3_dir: Path
) -> None:
    """The sweep has to be able to fail: assert it finds nothing where nothing is,
    and then that a planted value IS found. A canary suite that can only pass is
    a canary suite that proves nothing.
    """
    payload = json.loads(
        (gold_v3_dir / "sf-0001-it-beratung-vollstaendig.json").read_text(
            encoding="utf-8"
        )
    )
    journal = InMemoryJournalStore()
    result = run_pipeline(
        payload, config=config, journal=journal, vault=InMemoryVaultStore()
    )
    assert_no_canary(result.envelope.model_dump_json(), "a clean gold item")

    planted: dict[str, Any] = json.loads(json.dumps(payload))
    planted["data"]["antrag"]["taetigkeit_bezeichnung"] = CANARY_FREITEXT
    planted_result = build_ingest(
        planted,
        versions=config.version_stamp(),
        vault=InMemoryVaultStore(),
        policy=config.redaction,
    )
    assert planted_result.auto_sealed_paths == ("antrag.taetigkeit_bezeichnung",)
    assert_no_canary(
        planted_result.envelope.model_dump_json(), "an item with a planted canary"
    )


def test_the_yaml_config_never_names_a_canary(config: ConfigBundle) -> None:
    """Guards against a fixture value drifting into shipped config."""
    for path in sorted(config.config_dir.rglob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert_no_canary(json.dumps(document, default=str), f"config file {path.name}")


# ------------------------------------------------ part 10: the review UI ---
#
# The sweep's last extension, and the one with the most surfaces at once. The
# review UI renders more of a case than anything before it - the working copy's
# sealed kinds, the extraction spans, the gap sentences, the routing evidence,
# the anomaly reasons, the whole journal - so it is where a value that survived
# anywhere upstream would finally become visible to a human.
#
# The exception list stays at TWO members and part 10 does not extend it. The
# case view's draft SECTION shows the letter, which is the draft store's
# exception being rendered rather than a third holder of personal data; every
# other page, the metrics, the corrections export and the xdomea-shaped
# dispatch stub are swept clean.


def test_no_canary_reaches_the_review_pages_or_the_dispatch_export(
    client: TestClient,
    config: ConfigBundle,
    journal_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queues, case view, metrics, corrections export, handover stub."""
    monkeypatch.setenv(DISPATCH_DIR_ENV, str(tmp_path / "dispatch"))
    created = client.post("/ingest", json=canary_altersrente_submission())
    assert created.status_code == 201
    case_id = created.json()["case_id"]

    # Every page WITHOUT a unit: nothing is re-hydrated anywhere.
    assert_no_canary(client.get("/review").text, "the review overview")
    assert_no_canary(
        client.get(f"/review/queue/{REVIEW_UNIT}").text, "a unit queue page"
    )
    assert_no_canary(
        client.get("/review/queue/__clearing__").text, "the clearing queue page"
    )
    assert_no_canary(
        client.get(f"/review/case/{case_id}").text, "the case view without a unit"
    )

    # A correction, then a confirmation with dispatch. Both journal, one writes
    # a file into an operator-visible out-directory.
    assert (
        client.post(
            f"/review/case/{case_id}/override",
            data={
                "unit": REVIEW_UNIT,
                "field": "unit",
                "to": "Referat_318_Auslandsrenten",
                "reason": "Auslandsbezug",
            },
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(
            f"/review/case/{case_id}/confirm",
            data={"unit": "Referat_318_Auslandsrenten", "dispatch": "1"},
            follow_redirects=False,
        ).status_code
        == 303
    )

    stubs = sorted((tmp_path / "dispatch").glob("*.xml"))
    assert stubs, "the confirm step must have written a handover stub"
    for path in stubs:
        assert_no_canary(path.read_text(encoding="utf-8"), f"the stub {path.name}")

    store = JsonlJournalStore(journal_dir)
    pool = build_pool(store)
    assert pool["count"] == 1
    assert_no_canary(
        json.dumps(pool, ensure_ascii=False, default=str), "the corrections export"
    )

    metrics = review_metrics(
        build_index(store), now=datetime(2026, 3, 4, tzinfo=UTC), config=config.queues
    )
    assert_no_canary(
        json.dumps(metrics.as_payload(), ensure_ascii=False), "the review metrics"
    )

    # And the pages again, AFTER the actions, so the CONFIRMED and OVERRIDDEN
    # payloads are on screen when they are swept.
    assert_no_canary(client.get("/review").text, "the overview after the actions")
    assert_no_canary(
        client.get(f"/review/case/{case_id}").text, "the case view after the actions"
    )


def test_the_case_view_shows_the_letter_only_behind_the_unit_picker(
    client: TestClient,
) -> None:
    """Part 08's exception, rendered - and the gate part 10 put in front of it."""
    created = client.post("/ingest", json=canary_altersrente_submission())
    case_id = created.json()["case_id"]
    gated = client.get(f"/review/case/{case_id}")
    assert_no_canary(gated.text, "the case view without a unit")
    assert "erst angezeigt, wenn oben eine" in gated.text

    opened = client.get(f"/review/case/{case_id}?unit={REVIEW_UNIT}")
    assert_canaries_present(opened.text, "the case view WITH a unit")
    # An id that is not a taxonomy unit is not a role.
    assert_no_canary(
        client.get(f"/review/case/{case_id}?unit=Referat_999_Erfunden").text,
        "the case view with an unknown unit",
    )


def test_no_canary_reaches_the_demo_landing_page_or_the_banner(
    config: ConfigBundle,
    journal_dir: Path,
    vault_dir: Path,
    outbox_dir: Path,
    drafts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Part 11's two new surfaces, swept like every other one.

    The landing page and the banner are the two things a PUBLIC visitor sees
    first and the two things written last, which is exactly the combination
    that produces a leak nobody reviewed. Neither reads a case - both render
    from constants and from the posture - and that is what is checked here
    rather than assumed: the sweep runs after a canary case exists in the
    stores the app is serving, with the banner on every page of it.
    """
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.setenv(INGEST_TOKEN_ENV, "canary-token")
    app = create_app(
        config=config,
        journal=JsonlJournalStore(journal_dir),
        vault=JsonlVaultStore(vault_dir),
        outbox=JsonlOutbox(outbox_dir),
        drafts=JsonlDraftStore(drafts_dir),
    )
    try:
        with TestClient(app) as demo_client:
            created = demo_client.post(
                "/ingest",
                json=canary_altersrente_submission(),
                headers={INGEST_HEADER: "canary-token"},
            )
            assert created.status_code == 201
            case_id = created.json()["case_id"]
            # The ribbon renders on the two pages that are about the demo since
            # part 18; here it is only evidence that the posture really is on
            # for this client, which is what makes the sweep below a sweep of
            # the DEMO surface. The sweep itself is over every page either way.
            for path in (
                "/",
                "/hinweise",
                "/review",
                f"/review/queue/{REVIEW_UNIT}",
                f"/review/case/{case_id}",
                "/metrics",
                "/inbox",
                # Part 15's tour and part 19's counterparty surface. Both are
                # public pages that render from constants and from the demo
                # store, and the second one renders a LETTER built out of
                # strings a visitor typed - which is exactly the kind of page
                # this sweep exists for. Reached here without a reference, so
                # what is swept is the state a stranger following the menu
                # lands on.
                "/demo/rundgang",
                "/demo/gegenpartei",
                "/demo/gegenpartei?zeichen=not-a-real-reference",
            ):
                page = demo_client.get(path)
                assert page.status_code == 200, path
                assert ('id="demo-ribbon"' in page.text) is (
                    path in ("/", "/hinweise")
                ), path
                assert_no_canary(page.text, f"the demo page {path}")
            # And the refusal body, which is the one response a stranger who
            # POSTed real data would read back.
            refused = demo_client.post("/ingest", json=canary_submission())
            assert refused.status_code == 403
            assert_no_canary(refused.text, "the demo ingest refusal")
    finally:
        demo_posture.cache_clear()
        set_demo_posture(DemoPosture())
