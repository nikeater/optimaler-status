"""The two letters: what they say, when they exist, and who may see them.

Every text here is produced from a FROZEN corpus item with a FIXED clock and a
seeded token source, so the two golden files are byte-stable. Nothing in this
module reads a wall clock and nothing depends on which wheels are installed.

The golden files are unusual and deliberate, for the reason part 07 gave about
notification wording and one more besides: a draft is the artifact of this
system with procedural consequence, and a change to its wording should have to
be looked at by a human rather than noticed in production.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from engine.config_loader import ConfigBundle, DraftTemplate
from engine.draft import (
    KIND_NACHFORDERUNG,
    KIND_PREPARED_DECISION,
    DraftingError,
    DraftOutcome,
    DraftRecord,
    DraftRequest,
    InMemoryDraftStore,
    build_letter,
    draft_kind_for,
    owed_drafts,
    requirement_label,
)
from engine.draft.letters import wrap
from engine.draft.projection import draft_case, facts_from
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import PipelineResult, run_pipeline
from engine.redact import InMemoryVaultStore, SeededTokenSource, VaultRecord
from schemas.events import EventType

GOLDEN_DIR = Path(__file__).parent / "golden"

#: The injected clock. Every timestamp in this module derives from it.
FIXED = datetime(2026, 8, 6, 7, 21, tzinfo=UTC)

#: One tier-1 item (clear and complete Regelaltersrente) and one tier-2 item
#: whose gaps are a softened requirement (C-7: the Versicherungsnummer the DRV
#: can look up itself) and an ordinary one. Both frozen in gold v4.
TIER1_ITEM = "ar-0001-regelaltersrente-vollstaendig"
TIER2_ITEM = "ar-0014-ohne-vsnr-und-rentenbeginn"
TIER3_ITEM = "sf-0001-it-beratung-vollstaendig"


@pytest.fixture
def gold(gold_v4_dir: Path) -> Path:
    return gold_v4_dir


def run_case(
    item_id: str,
    config: ConfigBundle,
    *,
    gold: Path,
    rechtsfolgenhinweis: bool = False,
) -> tuple[PipelineResult, DraftOutcome, InMemoryDraftStore]:
    """One frozen item through the pipeline and then through drafting."""
    payload = json.loads((gold / f"{item_id}.json").read_text(encoding="utf-8"))
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    drafts = InMemoryDraftStore()
    result = run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=vault,
        now=FIXED,
        token_source=SeededTokenSource(42),
    )
    outcome = draft_case(
        journal.read(result.envelope.case_id),
        config=config,
        journal=journal,
        vault=vault,
        drafts=drafts,
        facts=facts_from(result.extractions),
        rechtsfolgenhinweis=rechtsfolgenhinweis,
        now=FIXED,
    )
    return result, outcome, drafts


def only_draft(outcome: DraftOutcome) -> DraftRecord:
    assert len(outcome.drafts) == 1, outcome.blocked
    return outcome.drafts[0]


# --------------------------------------------------------- golden letters ---


@pytest.mark.parametrize(
    ("item_id", "golden", "rechtsfolgenhinweis"),
    [
        (TIER2_ITEM, "draft_nachforderung_v1.txt", False),
        (TIER1_ITEM, "draft_bewilligungsentwurf_v1.txt", False),
    ],
)
def test_the_shipped_german_wording_is_byte_stable(
    config: ConfigBundle,
    gold: Path,
    item_id: str,
    golden: str,
    rechtsfolgenhinweis: bool,
) -> None:
    _, outcome, _ = run_case(
        item_id, config, gold=gold, rechtsfolgenhinweis=rechtsfolgenhinweis
    )
    expected = (GOLDEN_DIR / golden).read_text(encoding="utf-8").rstrip("\n")
    assert only_draft(outcome).body == expected


# ------------------------------------------------- C-6: the Nachforderung ---


def test_the_nachforderung_anchors_par_60_and_states_a_relative_window(
    config: ConfigBundle, gold: Path
) -> None:
    """C-6: the anchor, the window and the reply channel, all in the letter.

    The window is RELATIVE on purpose. The absolute date depends on the dispatch
    date, which does not exist while a draft waits for a caseworker, so a letter
    that named one would be naming a date nobody could compute yet.
    """
    _, outcome, _ = run_case(TIER2_ITEM, config, gold=gold)
    draft = only_draft(outcome)
    assert "par. 60" in draft.body
    assert "innerhalb von 30 Tagen nach Bekanntgabe" in draft.body
    assert "Antragsportal" in draft.body  # the reply channel of fit_connect
    assert draft.response_window_days == 30
    # No absolute deadline anywhere: no date-shaped literal except the one
    # journal timestamp the letter prints as the arrival date.
    dates = [word for word in draft.body.split() if word.count(".") == 2]
    assert dates == ["06.08.2026"]


def test_the_nachforderung_assembles_the_existing_gap_sentences(
    config: ConfigBundle, gold: Path
) -> None:
    """The wording is the procedure config's, rendered by the evidence plane.

    Part 08 never re-words a gap. The test compares the letter against the
    sentences ``engine/evidence/nachforderung.py`` produced for the same run.
    """
    result, outcome, _ = run_case(TIER2_ITEM, config, gold=gold)
    draft = only_draft(outcome)
    assert result.gap_renderings
    for rendering in result.gap_renderings:
        # The letter wraps at 76 columns, so compare on collapsed whitespace.
        assert " ".join(rendering.sentence.split()) in " ".join(draft.body.split())
    assert draft.requirement_ids == ["versicherungsnummer", "rentenbeginn"]


def test_the_letter_carries_the_dispatch_journaling_note_for_part_10(
    config: ConfigBundle, gold: Path
) -> None:
    _, outcome, _ = run_case(TIER2_ITEM, config, gold=gold)
    body = only_draft(outcome).body
    assert "Versandzeitpunkt wird beim Versand journalisiert" in " ".join(body.split())
    assert "par. 37 Abs. 2 SGB X" in " ".join(body.split())


def test_nothing_is_dispatched_and_the_journal_says_so(
    config: ConfigBundle, gold: Path
) -> None:
    _, outcome, _ = run_case(TIER2_ITEM, config, gold=gold)
    payload = outcome.events[0].payload
    assert payload["dispatched"] is False
    # C-8: the shape a par. 66-bearing letter would need, recorded per case.
    assert payload["dispatch_shape"] == "qualified_electronic"


# ------------------------------------------ C-7: the Amtsermittlung guard ---


def test_the_vsnr_request_softens_and_leaves_the_par_66_scope(
    config: ConfigBundle, gold: Path
) -> None:
    """C-7: what the DRV can determine itself is asked for gently, and never
    threatened with par. 66 Abs. 3."""
    _, outcome, _ = run_case(TIER2_ITEM, config, gold=gold, rechtsfolgenhinweis=True)
    draft = only_draft(outcome)
    flat = " ".join(draft.body.split())
    assert "koennen wir auch selbst ermitteln" in flat or "recherchierbar" in flat
    assert draft.amtsermittlung_ids == ["versicherungsnummer"]
    assert draft.rechtsfolgenhinweis is True
    # The block names the OTHER requirement and not the softened one.
    block = flat.split("Folgen fehlender Mitwirkung")[1]
    assert "Gewuenschter Rentenbeginn" in block
    assert "Versicherungsnummer" not in block


def test_the_par_66_block_is_off_unless_a_caseworker_asks(
    config: ConfigBundle, gold: Path
) -> None:
    """The parameter defaults off and the config carries no switch at all."""
    _, outcome, _ = run_case(TIER2_ITEM, config, gold=gold)
    draft = only_draft(outcome)
    assert draft.rechtsfolgenhinweis is False
    assert "par. 66" not in draft.body
    assert "versagt" not in draft.body


def test_a_letter_whose_only_gap_is_amtsermittelbar_renders_no_par_66_block(
    config: ConfigBundle, gold: Path
) -> None:
    """An empty scope means NO block, not an empty one: a Rechtsfolgenhinweis
    that names nothing is boilerplate, and par. 66 Abs. 3 SGB I does not permit
    boilerplate."""
    _, outcome, _ = run_case(
        "ar-0010-ohne-versicherungsnummer", config, gold=gold, rechtsfolgenhinweis=True
    )
    draft = only_draft(outcome)
    assert draft.amtsermittlung_ids == ["versicherungsnummer"]
    assert draft.requirement_ids == ["versicherungsnummer"]
    assert draft.rechtsfolgenhinweis is False
    assert "par. 66" not in draft.body


# ------------------------------------------------ tier 1 and the ENTWURF ---


def test_the_prepared_decision_is_unmissably_a_draft(
    config: ConfigBundle, gold: Path
) -> None:
    """Ruling 6: prepared for human confirmation, not a Verwaltungsakt."""
    _, outcome, _ = run_case(TIER1_ITEM, config, gold=gold)
    draft = only_draft(outcome)
    assert draft.kind == KIND_PREPARED_DECISION
    body = draft.body
    assert body.startswith("ENTWURF")
    flat = " ".join(body.split())
    assert "KEIN VERWALTUNGSAKT" in flat
    assert "erst dann zu einer Entscheidung" in flat
    assert "Vollautomatische Bekanntgabe findet nicht statt" in flat


def test_the_prepared_decision_states_the_re_hydrated_facts(
    config: ConfigBundle, gold: Path
) -> None:
    result, outcome, _ = run_case(TIER1_ITEM, config, gold=gold)
    draft = only_draft(outcome)
    flat = " ".join(draft.body.split())
    assert "Beantragte Rentenart: regelaltersrente" in flat
    # The two sealed fields are placeholders in the extraction set and real
    # values in the letter. That IS re-hydration, measured rather than claimed.
    facts = facts_from(result.extractions)
    assert facts["versicherungsnummer"].startswith("[[PII|VSNR|")
    assert "Versicherungsnummer: 17170459B012" in flat
    assert draft.resolved_tokens == 5
    assert draft.token_kinds == {"ADDR": 1, "GEBDAT": 2, "VSNR": 2}


def test_fully_automated_stays_false_for_every_procedure(
    config: ConfigBundle,
) -> None:
    """Part 08 touches no flag; asserted here because this is the part where a
    prepared decision first exists."""
    assert all(
        procedure.flags.fully_automated is False
        for procedure in config.procedures.values()
    )


# --------------------------------------------------------- draft policy ---


def test_tier_three_gets_no_draft(config: ConfigBundle, gold: Path) -> None:
    """Ruling 7: drafting for tier 3 would presume the outcome."""
    result, outcome, drafts = run_case(TIER3_ITEM, config, gold=gold)
    assert int(result.decision.tier) == 3
    assert outcome.count == 0
    assert outcome.blocked == ()
    assert drafts.count() == 0
    assert not any(
        event.type is EventType.DRAFTED
        for event in [*outcome.events]  # nothing was journaled either
    )


def test_the_policy_in_one_function() -> None:
    from engine.draft import GapSentence

    gap = GapSentence(requirement_id="x", sentence="Bitte.")
    assert draft_kind_for(1, []) == KIND_PREPARED_DECISION
    assert draft_kind_for(2, [gap]) == KIND_NACHFORDERUNG
    assert draft_kind_for(2, []) is None
    assert draft_kind_for(3, [gap]) is None


def test_a_requirement_label_is_a_prefix_of_the_agency_wording(
    config: ConfigBundle,
) -> None:
    """No second wording is invented for a letter: a label is always a prefix."""
    procedure = config.procedures["altersrente"]
    for requirement in procedure.requirements.requirements:
        label = requirement_label(requirement, requirement.requirement_id)
        assert " ".join(requirement.description.split()).startswith(label)
    assert requirement_label(None, "auslandsbezug") == "Auslandsbezug"


# ------------------------------------------------------------ the store ---


def test_the_drafted_event_never_carries_the_letter(
    config: ConfigBundle, gold: Path
) -> None:
    """Ruling 3: template id, kind, requirement ids, token counts - no text."""
    _, outcome, _ = run_case(TIER2_ITEM, config, gold=gold)
    draft = only_draft(outcome)
    event = outcome.events[0]
    assert event.type is EventType.DRAFTED
    assert event.template_id == "nachforderung_v1"
    assert event.informational_only is False
    serialized = json.dumps(event.payload, ensure_ascii=False)
    assert draft.body[:60] not in serialized
    assert "Sehr geehrte" not in serialized
    assert event.payload["body_chars"] == len(draft.body)
    assert event.payload["resolved_tokens"] == draft.resolved_tokens
    assert event.payload["requirement_ids"] == draft.requirement_ids


def test_drafting_twice_writes_one_draft_and_one_event(
    config: ConfigBundle, gold: Path
) -> None:
    """Idempotence, the part-07 property applied to a store that holds PII."""
    payload = json.loads((gold / f"{TIER2_ITEM}.json").read_text(encoding="utf-8"))
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    drafts = InMemoryDraftStore()
    result = run_pipeline(
        payload, config=config, journal=journal, vault=vault, now=FIXED
    )
    facts = facts_from(result.extractions)
    case_id = result.envelope.case_id
    kwargs: dict[str, Any] = {
        "config": config,
        "journal": journal,
        "vault": vault,
        "drafts": drafts,
        "facts": facts,
        "now": FIXED,
    }
    first = draft_case(journal.read(case_id), **kwargs)
    second = draft_case(journal.read(case_id), **kwargs)
    third = draft_case(journal.read(case_id), **kwargs)
    assert (first.count, second.count, third.count) == (1, 0, 0)
    assert drafts.count() == 1
    assert owed_drafts(journal.read(case_id), config=config) == ()
    assert (
        sum(1 for event in journal.read(case_id) if event.type is EventType.DRAFTED)
        == 1
    )


def test_an_agency_that_prepares_no_drafts_owes_none(
    config: ConfigBundle, gold: Path
) -> None:
    """No ``config/drafting/`` means no drafts, at every entry point."""
    without = replace(config, drafting=None)
    payload = json.loads((gold / f"{TIER2_ITEM}.json").read_text(encoding="utf-8"))
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    result = run_pipeline(
        payload, config=config, journal=journal, vault=vault, now=FIXED
    )
    events = journal.read(result.envelope.case_id)
    assert owed_drafts(events, config=without) == ()
    outcome = draft_case(
        events,
        config=without,
        journal=journal,
        vault=vault,
        drafts=InMemoryDraftStore(),
        facts=facts_from(result.extractions),
    )
    assert outcome.count == 0
    with pytest.raises(DraftingError, match="prepares no drafts"):
        build_letter(
            DraftRequest(
                case_id="case-x",
                envelope_id="env-x",
                kind=KIND_NACHFORDERUNG,
                tier=2,
                vault_ref="vault-x",
            ),
            config=without,
            record=VaultRecord(
                vault_ref="vault-x", case_id="case-x", created_at=FIXED, entries=()
            ),
        )


def test_a_gap_without_wording_blocks_the_letter_rather_than_dropping_it(
    config: ConfigBundle, gold: Path
) -> None:
    """A Nachforderung that silently asked for one thing less would be worse
    than none: the applicant would answer and still be incomplete."""
    payload = json.loads((gold / f"{TIER2_ITEM}.json").read_text(encoding="utf-8"))
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    result = run_pipeline(
        payload, config=config, journal=journal, vault=vault, now=FIXED
    )
    events = journal.read(result.envelope.case_id)
    stripped = []
    for event in events:
        if event.type is EventType.EVIDENCE_ASSEMBLED:
            recorded = cast(list[dict[str, Any]], event.payload["gaps"])
            gaps = [{**gap, "request_text": ""} for gap in recorded]
            event = event.model_copy(
                update={"payload": {**event.payload, "gaps": gaps}}
            )
        stripped.append(event)
    outcome = draft_case(
        stripped,
        config=config,
        journal=journal,
        vault=vault,
        drafts=InMemoryDraftStore(),
        facts=facts_from(result.extractions),
    )
    assert outcome.count == 0
    assert "no request wording" in outcome.blocked[0].reason


def test_an_unreadable_vault_blocks_the_letter(
    config: ConfigBundle, gold: Path
) -> None:
    """Ruling 2 at the level of the whole draft: no partial output."""
    payload = json.loads((gold / f"{TIER2_ITEM}.json").read_text(encoding="utf-8"))
    journal = InMemoryJournalStore()
    result = run_pipeline(
        payload, config=config, journal=journal, vault=InMemoryVaultStore(), now=FIXED
    )
    drafts = InMemoryDraftStore()
    outcome = draft_case(
        journal.read(result.envelope.case_id),
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),  # a DIFFERENT vault: the record is not in it
        drafts=drafts,
        facts=facts_from(result.extractions),
    )
    assert outcome.count == 0
    assert drafts.count() == 0
    assert "could not be read" in outcome.blocked[0].reason


def test_a_template_naming_an_undefined_key_fails_loudly(
    config: ConfigBundle, gold: Path
) -> None:
    """The loader refuses this at startup; the renderer refuses it again.

    ``model_construct`` skips the loader's validation on purpose - the point is
    that a template that got past it still cannot render a blank into a letter.
    """
    drafting = config.drafting
    assert drafting is not None
    broken = DraftTemplate.model_construct(
        template_id="broken_v1",
        kind=KIND_NACHFORDERUNG,
        subject="{{ sachbearbeiter }}",
        body="{{ sachbearbeiter }}",
    )
    patched = drafting.model_copy(update={"templates": [broken, drafting.templates[1]]})
    _, outcome, _ = run_case(TIER2_ITEM, replace(config, drafting=patched), gold=gold)
    assert outcome.count == 0
    assert "unknown name" in outcome.blocked[0].reason


def test_blank_line_debris_is_collapsed_and_an_empty_wrap_stays_empty(
    config: ConfigBundle, gold: Path
) -> None:
    """The cosmetic tidy, exercised through a template that leaves debris."""
    assert wrap("") == ""
    drafting = config.drafting
    assert drafting is not None
    noisy = drafting.templates[0].model_copy(
        update={"body": "Kopf\n\n\n\n{{ case_id }}\n\n\n\nFuss"}
    )
    patched = drafting.model_copy(update={"templates": [noisy, drafting.templates[1]]})
    _, outcome, _ = run_case(TIER2_ITEM, replace(config, drafting=patched), gold=gold)
    body = only_draft(outcome).body
    assert "\n\n\n" not in body
    assert body.splitlines()[0] == "Kopf"


def test_facts_without_a_procedure_are_sorted(config: ConfigBundle) -> None:
    """Requirement order needs a procedure; without one, order is still fixed."""
    drafting = config.drafting
    assert drafting is not None
    rendered = build_letter(
        DraftRequest(
            case_id="case-x",
            envelope_id="env-x",
            kind=KIND_PREPARED_DECISION,
            tier=1,
            vault_ref="vault-x",
            procedure_id=None,
            facts={"zweitens": "b", "erstens": "a"},
        ),
        config=config,
        record=VaultRecord(
            vault_ref="vault-x", case_id="case-x", created_at=FIXED, entries=()
        ),
    )
    assert rendered.body.index("Erstens: a") < rendered.body.index("Zweitens: b")
    assert rendered.resolved_tokens == 0


def test_a_draft_saved_but_not_journaled_is_journaled_on_the_next_run(
    config: ConfigBundle, gold: Path
) -> None:
    """ADR-022's ordering, one artifact further on: save first, journal second."""
    payload = json.loads((gold / f"{TIER2_ITEM}.json").read_text(encoding="utf-8"))
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    drafts = InMemoryDraftStore()
    result = run_pipeline(
        payload, config=config, journal=journal, vault=vault, now=FIXED
    )
    case_id = result.envelope.case_id
    owed = owed_drafts(journal.read(case_id), config=config)
    assert len(owed) == 1
    # Simulate the crash: the draft is in the store, no DRAFTED event exists.
    outcome = draft_case(
        journal.read(case_id),
        config=config,
        journal=InMemoryJournalStore(),  # a journal the real one never sees
        vault=vault,
        drafts=drafts,
        facts=facts_from(result.extractions),
        now=FIXED,
    )
    assert outcome.count == 1
    recovered = draft_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        vault=vault,
        drafts=drafts,
        facts=facts_from(result.extractions),
        now=FIXED,
    )
    assert recovered.skipped == 1
    assert recovered.count == 1  # the event was written, the draft not duplicated
    assert drafts.count() == 1
