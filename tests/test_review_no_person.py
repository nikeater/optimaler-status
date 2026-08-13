"""C-4, asserted rather than promised: no caseworker appears anywhere.

BPersVG par. 80 Abs. 1 Nr. 21 makes the introduction of technical systems
SUITED to monitoring performance or behaviour co-determined. This system is
built so that the question does not arise: the journal's ``Actor`` has ``kind``
and ``unit_id`` and no third field, so a per-person override rate is not
suppressed by policy - it is not expressible.

A structural guarantee nobody checks is a structural guarantee right up until
somebody adds a field, so this file checks it. The canaries here are named
individuals in the shape a real deployment would produce them: a login, a
display name, an e-mail, a personnel number. They are pushed in through every
door part 10 opens - the unit picker, the confirm form, the override reason,
the note field - and then every surface part 10 writes is swept:

* the CONFIRMED and OVERRIDDEN journal payloads and their Actor,
* the three rendered pages,
* the P-6 / P-10 metrics payload,
* the corrections export,
* the xdomea-shaped dispatch stub on disk.

The one thing that is NOT swept is the free-text reason a caseworker typed,
where the caseworker chose to type their own name. That is a data-entry fact no
type system can prevent, and the honest answer is the one the Dienstvereinbarung
has to give (C-4 remains open for exactly that reason). The reason field is
tested for the OTHER direction instead: what the system puts there by itself.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.review import (
    build_case_view,
    build_overview,
    build_queue_view,
    render_case,
    render_overview,
    render_queue,
)
from engine.config_loader import ConfigBundle
from engine.draft import InMemoryDraftStore, draft_case
from engine.draft.projection import facts_from
from engine.journal import InMemoryJournalStore
from engine.journal.corrections import build_pool
from engine.notify import InMemoryOutbox
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore, text_seal_detector
from engine.review import build_index, confirm_case, override_case, review_metrics
from engine.review.state import OVERRIDE_UNIT
from schemas.events import ActorKind

UNIT = "Referat_312_Renten"
OTHER_UNIT = "Referat_318_Auslandsrenten"
ITEM = "ar-0011-ohne-rentenbeginn"
INGESTED_AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 3, 4, 9, 0, tzinfo=UTC)

#: A caseworker as a real deployment would identify one. Distinctive enough
#: that a substring search cannot match by accident.
PERSON_CANARIES = (
    "sachbearbeiterin.quirin.federweiss",
    "Quirin Federweiss",
    "q.federweiss@drv-bund.example",
    "PN-0815-4711",
)


def assert_no_person(text: str, where: str) -> None:
    for canary in PERSON_CANARIES:
        assert canary not in text, f"a natural person reached {where}: {canary!r}"


@pytest.fixture
def stores() -> tuple[InMemoryJournalStore, InMemoryVaultStore, InMemoryDraftStore]:
    return InMemoryJournalStore(), InMemoryVaultStore(), InMemoryDraftStore()


@pytest.fixture
def client(
    config: ConfigBundle,
    stores: tuple[InMemoryJournalStore, InMemoryVaultStore, InMemoryDraftStore],
) -> Iterator[TestClient]:
    journal, vault, drafts = stores
    app = create_app(
        config=config,
        journal=journal,
        vault=vault,
        text_detector=text_seal_detector(with_ner=False),
        outbox=InMemoryOutbox(),
        drafts=drafts,
    )
    with TestClient(app) as test_client:
        yield test_client


def _ingest(
    config: ConfigBundle,
    stores: tuple[InMemoryJournalStore, InMemoryVaultStore, InMemoryDraftStore],
    gold_v4_dir: Path,
) -> str:
    journal, vault, drafts = stores
    payload = json.loads((gold_v4_dir / f"{ITEM}.json").read_text(encoding="utf-8"))
    result = run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=vault,
        now=INGESTED_AT,
        text_detector=text_seal_detector(with_ner=False),
    )
    draft_case(
        journal.read(result.decision.case_id),
        config=config,
        journal=journal,
        vault=vault,
        drafts=drafts,
        facts=facts_from(result.extractions),
        now=INGESTED_AT,
    )
    return result.decision.case_id


def test_the_actor_contract_has_no_field_for_a_person() -> None:
    """The structural half: there is nowhere to put one."""
    from schemas.events import Actor

    assert set(Actor.model_fields) == {"kind", "unit_id"}
    assert set(ActorKind) == {ActorKind.SYSTEM, ActorKind.CASEWORKER}


def test_a_person_pushed_in_through_the_forms_reaches_no_surface(
    client: TestClient,
    config: ConfigBundle,
    stores: tuple[InMemoryJournalStore, InMemoryVaultStore, InMemoryDraftStore],
    gold_v4_dir: Path,
    tmp_path: Path,
) -> None:
    journal, _vault, drafts = stores
    case_id = _ingest(config, stores, gold_v4_dir)

    # Every door part 10 opens, with a person's identifier pushed through it.
    client.post(
        f"/review/case/{case_id}/override",
        data={
            "unit": PERSON_CANARIES[0],
            "field": OVERRIDE_UNIT,
            "to": OTHER_UNIT,
            "reason": "Auslandssachverhalt",
        },
        follow_redirects=False,
    )
    client.post(
        f"/review/case/{case_id}/override",
        data={
            "unit": OTHER_UNIT,
            "field": OVERRIDE_UNIT,
            "to": UNIT,
            "reason": "doch inlaendisch",
        },
        follow_redirects=False,
    )
    client.post(
        f"/review/case/{case_id}/confirm",
        data={
            "unit": UNIT,
            "note": "geprueft",
            "dispatch": "1",
        },
        follow_redirects=False,
    )

    events = journal.read(case_id)
    for event in events:
        assert event.actor.kind in (ActorKind.SYSTEM, ActorKind.CASEWORKER)
        assert_no_person(
            json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
            f"the {event.type.value} event",
        )
    # The unit that was not a taxonomy unit did NOT become an actor: it was
    # refused as a role, and the override it tried to make never happened.
    caseworker_units = {
        event.actor.unit_id
        for event in events
        if event.actor.kind is ActorKind.CASEWORKER
    }
    assert PERSON_CANARIES[0] not in caseworker_units

    index = build_index(journal)
    metrics = review_metrics(index, now=REVIEWED_AT, config=config.queues)
    assert_no_person(
        json.dumps(metrics.as_payload(), ensure_ascii=False), "the metrics payload"
    )
    for unit in metrics.units:
        assert unit.unit_id in {node.unit_id for node in config.taxonomy.nodes}

    pool = build_pool(journal, now=REVIEWED_AT)
    assert_no_person(json.dumps(pool, ensure_ascii=False), "the corrections export")

    for page in (
        render_overview(
            build_overview(journal, config=config, unit_id=UNIT, now=REVIEWED_AT)
        ),
        render_queue(
            build_queue_view(
                journal, config=config, queue_id=UNIT, unit_id=UNIT, now=REVIEWED_AT
            )
        ),
        render_case(
            _case_view(config, journal, drafts, case_id),
        ),
    ):
        assert_no_person(page, "a rendered review page")

    del tmp_path


def test_the_dispatch_stub_names_a_unit_and_never_a_person(
    config: ConfigBundle,
    stores: tuple[InMemoryJournalStore, InMemoryVaultStore, InMemoryDraftStore],
    gold_v4_dir: Path,
    tmp_path: Path,
) -> None:
    journal, vault, drafts = stores
    case_id = _ingest(config, stores, gold_v4_dir)
    outcome = confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
        dispatch_root=tmp_path,
    )
    assert outcome.stub is not None
    xml = outcome.stub.path.read_text(encoding="utf-8")
    assert_no_person(xml, "the dispatch stub")
    assert f"<Organisationseinheit>{UNIT}</Organisationseinheit>" in xml
    assert config.dispatch is not None
    assert config.dispatch.export.omit_caseworker_identity is True


def test_the_metrics_carry_counts_per_unit_and_nothing_finer(
    config: ConfigBundle,
    stores: tuple[InMemoryJournalStore, InMemoryVaultStore, InMemoryDraftStore],
    gold_v4_dir: Path,
) -> None:
    """Aggregate at UNIT level or coarser: the payload has no other key."""
    journal = stores[0]
    case_id = _ingest(config, stores, gold_v4_dir)
    override_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        field=OVERRIDE_UNIT,
        to_value=OTHER_UNIT,
        reason="Auslandssachverhalt",
        now=REVIEWED_AT,
    )
    payload = review_metrics(
        build_index(journal), now=REVIEWED_AT, config=config.queues
    ).as_payload()
    allowed = {
        "unit_id",
        "confirmed",
        "confirmed_without_edit",
        "confirm_without_edit_rate",
        "overridden",
        "override_rate",
        "escalated",
        "rerouted",
        "sampled_confirmed",
        "median_seconds_to_confirm",
        "reportable",
        "suppressed_reason",
    }
    for unit in payload["units"]:
        assert set(unit) == allowed
        for key, value in unit.items():
            if key == "unit_id":
                continue
            assert value is None or isinstance(value, bool | int | float | str)


def _case_view(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    drafts: InMemoryDraftStore,
    case_id: str,
) -> Any:
    view = build_case_view(
        journal,
        config=config,
        case_id=case_id,
        unit_id=UNIT,
        drafts=drafts,
        now=REVIEWED_AT,
    )
    assert view is not None
    return view
