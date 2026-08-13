"""What the drafting config refuses at STARTUP, and why each refusal exists.

Every check here turns a failure that would otherwise happen in front of a
caseworker - or worse, inside a letter a caseworker confirms without reading
closely - into a loud error before the process serves anything.

The C-7 one is the load-bearing case: the Amtsermittlung list decides both which
requests soften and which requirements are excluded from a par. 66 Abs. 3 scope,
so an entry under a misspelled requirement id would silently threaten an
applicant with a Versagung over a fact the agency can look up itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from engine.config_loader import (
    DRAFT_CONTEXT_KEYS,
    DRAFT_FILTERS,
    ConfigBundle,
    ConfigError,
    DraftingConfig,
    load_config,
)
from engine.draft.letters import DRAFT_FILTER_IMPLS

REPO_ROOT = Path(__file__).resolve().parents[1]


def drafting_document() -> dict[str, Any]:
    path = REPO_ROOT / "config" / "drafting" / "drafting_v1.yaml"
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document


def write_config(tmp_path: Path, document: dict[str, Any]) -> Path:
    """The shipped config directory with one drafting file replaced."""
    import shutil

    target = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", target)
    drafting_dir = target / "drafting"
    for existing in drafting_dir.glob("*.yaml"):
        existing.unlink()
    (drafting_dir / "drafting_v1.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


# ------------------------------------------------------- the shipped file ---


def test_the_shipped_config_loads_and_carries_both_letters(
    config: ConfigBundle,
) -> None:
    drafting = config.drafting
    assert drafting is not None
    assert drafting.version == "drafting_v1"
    assert {template.kind for template in drafting.templates} == {
        "nachforderung",
        "prepared_decision",
    }
    assert drafting.response_window_days == 30
    assert drafting.amtsermittlung.requirement_ids("altersrente") == frozenset(
        {"versicherungsnummer"}
    )
    assert drafting.amtsermittlung.requirement_ids(None) == frozenset()


def test_the_engine_implements_exactly_the_filters_the_config_permits() -> None:
    """Two lists, one truth: a template may only use a filter that exists."""
    assert set(DRAFT_FILTER_IMPLS) == set(DRAFT_FILTERS)


def test_a_config_directory_without_drafting_prepares_nothing(tmp_path: Path) -> None:
    """Absent is a choice with a defined meaning, not a degraded state."""
    import shutil

    target = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", target)
    shutil.rmtree(target / "drafting")
    assert load_config(target).drafting is None


# ----------------------------------------------------------- the refusals ---


def test_an_amtsermittlung_entry_for_an_unknown_requirement_is_refused(
    tmp_path: Path,
) -> None:
    """C-7: a softening that softens nothing would be worse than none."""
    document = drafting_document()
    document["amtsermittlung"]["entries"][0]["requirement_ids"] = ["versicherungsnr"]
    with pytest.raises(ConfigError, match="does not declare"):
        load_config(write_config(tmp_path, document))


def test_an_amtsermittlung_entry_for_an_unknown_procedure_is_refused(
    tmp_path: Path,
) -> None:
    document = drafting_document()
    document["amtsermittlung"]["entries"][0]["procedure_id"] = "wohngeld"
    with pytest.raises(ConfigError, match="unknown procedure"):
        load_config(write_config(tmp_path, document))


def test_a_betreff_for_an_unknown_procedure_is_refused(tmp_path: Path) -> None:
    document = drafting_document()
    document["procedure_names"]["wohngeld"] = "Ihr Antrag auf Wohngeld"
    with pytest.raises(ConfigError, match="unknown procedure"):
        load_config(write_config(tmp_path, document))


def test_a_letter_head_naming_an_unsealed_path_is_refused(tmp_path: Path) -> None:
    """A letter head may only print values that went through the vault."""
    document = drafting_document()
    document["addressee"]["anschrift"] = "antrag.rentenart"
    with pytest.raises(ConfigError, match="does not seal"):
        load_config(write_config(tmp_path, document))


def test_a_par_66_block_without_the_requirements_slot_is_refused() -> None:
    document = drafting_document()
    document["rechtsfolgenhinweis"]["body"] = (
        "Bei fehlender Mitwirkung kann die Leistung nach par. 66 Abs. 1 SGB I "
        "versagt werden."
    )
    with pytest.raises(ValidationError, match="boilerplate"):
        DraftingConfig.model_validate(document)


def test_a_par_66_block_that_does_not_cite_its_norm_is_refused() -> None:
    document = drafting_document()
    document["rechtsfolgenhinweis"]["body"] = (
        "Ohne die Angaben {{ requirements }} kann nicht entschieden werden."
    )
    with pytest.raises(ValidationError, match="does not cite"):
        DraftingConfig.model_validate(document)


def test_a_template_naming_an_unknown_context_key_is_refused() -> None:
    document = drafting_document()
    document["templates"][0]["body"] += "\n{{ sachbearbeiter_name }}"
    with pytest.raises(ValidationError, match="sachbearbeiter_name"):
        DraftingConfig.model_validate(document)


def test_a_template_using_an_unknown_filter_is_refused() -> None:
    document = drafting_document()
    document["templates"][0]["body"] += "\n{{ case_id | uppercase_please }}"
    with pytest.raises(ValidationError, match=r"not valid Jinja2"):
        DraftingConfig.model_validate(document)


def test_a_template_with_broken_jinja_is_refused() -> None:
    document = drafting_document()
    document["templates"][0]["body"] += "\n{% if %}"
    with pytest.raises(ValidationError, match="not valid Jinja2"):
        DraftingConfig.model_validate(document)


def test_a_missing_template_kind_is_refused() -> None:
    """A tier-1 item with no prepared-decision template would get nothing."""
    document = drafting_document()
    document["templates"] = [document["templates"][0]]
    with pytest.raises(ValidationError, match="prepared_decision"):
        DraftingConfig.model_validate(document)


def test_two_templates_of_one_kind_are_refused() -> None:
    document = drafting_document()
    duplicate = dict(document["templates"][0])
    duplicate["template_id"] = "nachforderung_v2"
    document["templates"].append(duplicate)
    with pytest.raises(ValidationError, match="same kind"):
        DraftingConfig.model_validate(document)


def test_an_unknown_template_kind_is_refused() -> None:
    document = drafting_document()
    document["templates"][0]["kind"] = "widerspruchsbescheid"
    with pytest.raises(ValidationError, match="known kinds"):
        DraftingConfig.model_validate(document)


def test_an_unmapped_inbound_channel_is_refused() -> None:
    document = drafting_document()
    document["channels"] = [
        entry for entry in document["channels"] if entry["channel"] != "scan"
    ]
    with pytest.raises(ValidationError, match="no draft channel mapping"):
        DraftingConfig.model_validate(document)


def test_an_unknown_dispatch_shape_is_refused() -> None:
    """C-8: the shape a par. 66 letter needs is a closed vocabulary."""
    document = drafting_document()
    document["channels"][0]["dispatch"] = "carrier_pigeon"
    with pytest.raises(ValidationError, match="dispatch shape"):
        DraftingConfig.model_validate(document)


@pytest.mark.parametrize("window", [0, 366])
def test_an_impossible_response_window_is_refused(window: int) -> None:
    document = drafting_document()
    document["response_window_days"] = window
    with pytest.raises(ValidationError):
        DraftingConfig.model_validate(document)


def test_the_context_keys_are_the_declared_ones() -> None:
    """The other half of the contract the loader enforces on templates."""
    assert "rechtsfolgenhinweis" in DRAFT_CONTEXT_KEYS
    assert "facts" in DRAFT_CONTEXT_KEYS
    assert "empfaenger_anschrift" in DRAFT_CONTEXT_KEYS
