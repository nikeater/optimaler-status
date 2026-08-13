"""KE-4: the extraction-confidence knob is wired, and here is what it costs.

`docs/KNOWN-ERRORS.md` KE-4 records an open policy question: a scanned letter
whose every span verified at a fuzzy score of 0.96 reaches tier 1 exactly like a
born-digital one that verified exactly, because ``table_v1`` does not read
``extraction.min_confidence``.

Two separate claims live in that entry and only one of them was ever checked.
"Nobody has decided this" is a statement about the Fachbereich. "The change is
available cheaply" is a statement about the ENGINE, and until this file it was
an assertion nobody had run.

Running it produced a finding that KE-4's own wording does not survive. The
field has been legal vocabulary since part 01 and the interpreter resolves it
correctly - but on gold v4 the confidence ORDER is the opposite of what the
entry assumes:

    ar-0064 (OCR scan, tier 1)      min_confidence 0.963  <- a measured score
    ar-0060 (e-mail, tier 1)        min_confidence 0.95   <- config.exact
    ar-0001 (form, tier 1)          min_confidence 1.0    <- read a key

An OCR span that matched at 0.963 carries what it measured; an exact
born-digital span carries the configured ``confidence.exact: 0.95``, which is
the deliberate statement that reading prose never earns 1.0. So a condition at
``>= 0.95`` moves NOTHING, and any condition strict enough to catch the scan
catches every e-mail with it. This field does not separate scan from
born-digital. It separates "read out of prose" from "read out of a key".

Both hypothetical tables are committed so a Fachbereich can see both halves.
The shipped table is untouched, and a test here asserts that too: demonstrating
a knob is not turning it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from engine.config_loader import ConfigBundle
from engine.decide import resolve_qualifying_fields
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import PipelineResult, run_pipeline
from engine.redact import InMemoryVaultStore
from schemas.common import Tier
from schemas.config import QUALIFYING_FIELDS, DecisionTable
from tests.factories import FIXED_NOW, make_evidence

HYPOTHETICAL_DIR = Path(__file__).parent / "golden" / "hypothetical"

#: The three shapes an item can have, all three of which reach tier 1 today.
#: The scan is the one KE-4 names; the other two are the control group, because
#: a condition that moved everything would not be a knob, it would be a switch.
SCAN_ITEM = "ar-0064-scan-regelaltersrente-vollstaendig.json"
BORN_DIGITAL_ITEM = "ar-0060-email-regelaltersrente-vollstaendig.json"
STRUCTURED_ITEM = "ar-0001-regelaltersrente-vollstaendig.json"
ALL_TIER1_SHAPES = [SCAN_ITEM, BORN_DIGITAL_ITEM, STRUCTURED_ITEM]


def _table(name: str) -> DecisionTable:
    return DecisionTable.model_validate(
        yaml.safe_load((HYPOTHETICAL_DIR / name).read_text(encoding="utf-8"))
    )


@pytest.fixture(scope="module")
def table_095() -> DecisionTable:
    """The table the obvious reading of KE-4 would produce."""
    return _table("table_min_confidence_095.yaml")


@pytest.fixture(scope="module")
def table_097() -> DecisionTable:
    """The table that actually moves something."""
    return _table("table_min_confidence_097.yaml")


def _payload(gold_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((gold_dir / name).read_text(encoding="utf-8"))


def _run(payload: dict[str, Any], config: ConfigBundle) -> PipelineResult:
    return run_pipeline(
        payload,
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        now=FIXED_NOW,
    )


def _tiers(
    gold_dir: Path, config: ConfigBundle, table: DecisionTable
) -> dict[str, Tier]:
    stricter = replace(config, decision_table=table)
    return {
        item: _run(_payload(gold_dir, item), stricter).decision.tier
        for item in ALL_TIER1_SHAPES
    }


# --------------------------------------------------------------------------
# The knob exists
# --------------------------------------------------------------------------


def test_the_field_is_legal_vocabulary_and_always_has_been() -> None:
    """No contract change is needed for this policy; part 01 already allows it."""
    assert "extraction.min_confidence" in QUALIFYING_FIELDS


def test_the_interpreter_fills_the_field_from_the_evidence_record() -> None:
    """A vocabulary the interpreter never resolved would be a condition that
    always failed - the safe direction, and still a lie in the table."""
    fields = resolve_qualifying_fields(make_evidence(min_confidence=0.94), None, False)
    assert fields["extraction.min_confidence"] == 0.94


def test_the_shipped_table_does_not_use_it(config: ConfigBundle) -> None:
    """Demonstrating the knob is not the same as turning it. KE-4 stays open."""
    used = {
        condition.field
        for row in config.decision_table.rows
        for condition in row.when_all
    }
    assert "extraction.min_confidence" not in used
    assert config.decision_table.version == "table_v1"


# --------------------------------------------------------------------------
# The confidence ordering KE-4 assumes, measured
# --------------------------------------------------------------------------


@pytest.mark.parametrize("item", ALL_TIER1_SHAPES)
def test_all_three_shapes_reach_tier_one_today(
    item: str, gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """The baseline KE-4 describes: the scan clears exactly like the others."""
    assert (
        _run(_payload(gold_v4_dir, item), config).decision.tier
        is Tier.CLEAR_AND_COMPLETE
    )


def test_the_scan_is_the_most_confident_of_the_three_prose_readings(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """The finding, pinned: the ordering is the opposite of KE-4's wording.

    A fuzzy record carries the score it measured; an exact record carries the
    configured ``confidence.exact``. 0.963 beats 0.95, so on this axis the scan
    is BETTER established than the e-mail, not worse.
    """
    confidences = {
        item: _run(
            _payload(gold_v4_dir, item), config
        ).evidence.extraction_min_confidence
        for item in ALL_TIER1_SHAPES
    }
    assert confidences[SCAN_ITEM] == pytest.approx(0.963)
    assert confidences[BORN_DIGITAL_ITEM] == config.extraction.confidence.exact
    assert confidences[STRUCTURED_ITEM] == 1.0
    assert confidences[SCAN_ITEM] > confidences[BORN_DIGITAL_ITEM]  # type: ignore[operator]


# --------------------------------------------------------------------------
# What each hypothetical table would cost
# --------------------------------------------------------------------------


def test_the_obvious_condition_moves_nothing_at_all(
    gold_v4_dir: Path, config: ConfigBundle, table_095: DecisionTable
) -> None:
    """A condition that reads a real field, evaluates, and changes no tier."""
    assert _tiers(gold_v4_dir, config, table_095) == {
        SCAN_ITEM: Tier.CLEAR_AND_COMPLETE,
        BORN_DIGITAL_ITEM: Tier.CLEAR_AND_COMPLETE,
        STRUCTURED_ITEM: Tier.CLEAR_AND_COMPLETE,
    }


def test_a_stricter_condition_moves_both_prose_shapes_and_no_form(
    gold_v4_dir: Path, config: ConfigBundle, table_097: DecisionTable
) -> None:
    """The knob works end to end - and it cannot single out the scan."""
    assert _tiers(gold_v4_dir, config, table_097) == {
        SCAN_ITEM: Tier.FULL_HUMAN_REVIEW,
        BORN_DIGITAL_ITEM: Tier.FULL_HUMAN_REVIEW,
        STRUCTURED_ITEM: Tier.CLEAR_AND_COMPLETE,
    }


def test_a_moved_item_says_which_condition_moved_it(
    gold_v4_dir: Path, config: ConfigBundle, table_097: DecisionTable
) -> None:
    """A tier that moved for an unreadable reason would be no better than KE-4."""
    stricter = replace(config, decision_table=table_097)
    decision = _run(_payload(gold_v4_dir, SCAN_ITEM), stricter).decision
    failed = [
        reason.detail
        for reason in decision.reasons
        if reason.rule_id == "tier1_clear_and_complete"
    ]
    assert failed and "extraction.min_confidence" in failed[0]
    assert "0.963" in failed[0]
    # Tier 3, not tier 2: the item is COMPLETE, so the tier-2 row (which needs
    # 'incomplete') does not catch it either. A Fachbereich should see that the
    # cost of this policy is a jump straight to full review.
    assert decision.tier is Tier.FULL_HUMAN_REVIEW


@pytest.mark.parametrize("table_name", ["095", "097"])
def test_the_structured_subset_never_moves_under_either_table(
    table_name: str, gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """KE-4 claims the structured subset is unaffected; here it is checked.

    Structured records carry confidence 1.0 by construction (the mapper read a
    key), so no form item can fail either condition. The claim in KE-4 was
    reasoning; this is the corpus.
    """
    stricter = replace(
        config, decision_table=_table(f"table_min_confidence_{table_name}.yaml")
    )
    forms = [
        path
        for path in sorted(gold_v4_dir.glob("*.json"))
        if not _run(json.loads(path.read_text(encoding="utf-8")), config).text_layer
    ]
    assert len(forms) == 77
    for path in forms:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert (
            _run(payload, stricter).decision.tier is _run(payload, config).decision.tier
        ), path.name
