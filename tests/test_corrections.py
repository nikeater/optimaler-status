"""The correction pool: what it collects, and what it refuses to collect.

The pool is the one artifact in this project whose purpose is to become
training data, so the tests are about provenance as much as about content: the
header has to say what the file is, the file has to be reproducible from the
journal, gold sets have to stay out of it, and no case content and no person
may ride along.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.config_loader import ConfigBundle
from engine.draft import InMemoryDraftStore, draft_case
from engine.draft.projection import facts_from
from engine.journal import InMemoryJournalStore, JsonlJournalStore
from engine.journal.corrections import (
    POOL_NOTE,
    build_pool,
    collect,
    corrections_for,
    main,
    write_pool,
)
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore, text_seal_detector
from engine.review import escalate_case, override_case
from engine.review.state import OVERRIDE_UNIT

UNIT = "Referat_312_Renten"
OTHER_UNIT = "Referat_318_Auslandsrenten"
ITEM = "ar-0011-ohne-rentenbeginn"
INGESTED_AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
CORRECTED_AT = datetime(2026, 3, 4, 9, 0, tzinfo=UTC)


@pytest.fixture
def journal_with_corrections(
    config: ConfigBundle, gold_v4_dir: Path
) -> tuple[InMemoryJournalStore, str]:
    journal, vault, drafts = (
        InMemoryJournalStore(),
        InMemoryVaultStore(),
        InMemoryDraftStore(),
    )
    payload = json.loads((gold_v4_dir / f"{ITEM}.json").read_text(encoding="utf-8"))
    result = run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=vault,
        now=INGESTED_AT,
        text_detector=text_seal_detector(with_ner=False),
    )
    case_id = result.decision.case_id
    draft_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        vault=vault,
        drafts=drafts,
        facts=facts_from(result.extractions),
        now=INGESTED_AT,
    )
    override_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        field=OVERRIDE_UNIT,
        to_value=OTHER_UNIT,
        reason="Auslandsbezug ergibt sich aus der Beschaeftigungshistorie",
        now=CORRECTED_AT,
    )
    escalate_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=OTHER_UNIT,
        reason="Sachverhalt unklar, medizinische Ruecksprache noetig",
        now=CORRECTED_AT,
    )
    return journal, case_id


def test_the_pool_collects_both_corrections_with_their_reasons(
    journal_with_corrections: tuple[InMemoryJournalStore, str],
) -> None:
    journal, case_id = journal_with_corrections
    pool = collect(journal)
    assert [item.field for item in pool] == ["unit", "escalation"]
    reroute, escalation = pool
    assert reroute.from_value == UNIT
    assert reroute.to_value == OTHER_UNIT
    assert "Auslandsbezug" in reroute.reason
    assert reroute.unit_id == UNIT
    assert escalation.to_value == 3
    assert escalation.unit_id == OTHER_UNIT
    assert all(item.case_id == case_id for item in pool)
    # The machine's answer travels with every correction: without it the pool
    # is a list of decisions rather than a list of disagreements.
    assert reroute.machine_unit_id == UNIT
    assert reroute.machine_tier == 2
    assert reroute.procedure_id == "altersrente"


def test_the_pool_says_what_it_is_and_is_not(
    journal_with_corrections: tuple[InMemoryJournalStore, str],
) -> None:
    journal, _case_id = journal_with_corrections
    document = build_pool(journal, now=CORRECTED_AT)
    assert document["note"] == POOL_NOTE
    assert "KEIN Goldsatz" in POOL_NOTE
    assert "ADR-010" in POOL_NOTE
    assert "BPersVG" in POOL_NOTE
    assert document["count"] == 2
    assert document["by_field"] == {"escalation": 1, "unit": 1}
    assert document["by_unit"] == {OTHER_UNIT: 1, UNIT: 1}


def test_the_pool_carries_no_case_content(
    journal_with_corrections: tuple[InMemoryJournalStore, str],
) -> None:
    """Decision, correction, reason. Not a value, not a letter, not a span."""
    journal, _case_id = journal_with_corrections
    allowed = {
        "case_id",
        "field",
        "from",
        "to",
        "reason",
        "unit_id",
        "occurred_at",
        "machine_tier",
        "machine_unit_id",
        "procedure_id",
        "channel",
        "sampled",
    }
    for item in build_pool(journal, now=CORRECTED_AT)["corrections"]:
        assert set(item) == allowed


def test_regenerating_the_pool_reproduces_it_byte_for_byte(
    journal_with_corrections: tuple[InMemoryJournalStore, str], tmp_path: Path
) -> None:
    """The journal is the truth; the pool is a view that can be thrown away."""
    journal, _case_id = journal_with_corrections
    first = write_pool(build_pool(journal, now=CORRECTED_AT), tmp_path / "pool.json")
    content = first.read_bytes()
    second = write_pool(build_pool(journal, now=CORRECTED_AT), tmp_path / "pool2.json")
    assert second.read_bytes() == content


def test_a_case_without_corrections_contributes_nothing(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    journal = InMemoryJournalStore()
    payload = json.loads((gold_v4_dir / f"{ITEM}.json").read_text(encoding="utf-8"))
    result = run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),
        now=INGESTED_AT,
        text_detector=text_seal_detector(with_ner=False),
    )
    assert (
        corrections_for(result.decision.case_id, journal.read(result.decision.case_id))
        == []
    )
    assert build_pool(journal, now=CORRECTED_AT)["count"] == 0


def test_a_malformed_override_degrades_instead_of_raising(
    journal_with_corrections: tuple[InMemoryJournalStore, str],
) -> None:
    """The projection discipline: a rendering bug may not kill the export."""
    journal, case_id = journal_with_corrections
    events = journal.read(case_id)
    broken = [
        event.model_copy(update={"payload": {}})
        if event.type.value == "overridden"
        else event
        for event in events
    ]
    pool = corrections_for(case_id, broken)
    assert len(pool) == 2
    assert all(item.field == "" for item in pool)


def test_the_cli_writes_the_pool_from_a_journal_directory(
    journal_with_corrections: tuple[InMemoryJournalStore, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal, case_id = journal_with_corrections
    on_disk = JsonlJournalStore(tmp_path / "journal")
    for event in journal.read(case_id):
        on_disk.append(event)
    out = tmp_path / "pool.json"
    assert main(["--journal", str(tmp_path / "journal"), "--out", str(out)]) == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["count"] == 2
    printed = capsys.readouterr().out
    assert "2 Korrektur(en)" in printed
    assert "unit: 1" in printed


def test_the_cli_refuses_to_guess_a_journal_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-memory journal has nothing to export, and silence would hide that."""
    monkeypatch.delenv("EINGANGSLOTSE_JOURNAL_DIR", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        main(["--out", str(tmp_path / "pool.json")])
    assert excinfo.value.code == 2
