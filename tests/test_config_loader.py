"""Config loading: the shipped config is valid, and bad config fails loudly."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from engine.config_loader import (
    ConfigBundle,
    ConfigError,
    ProcedureConfig,
    RoutingConfig,
    load_config,
)
from engine.predicate import PredicateError
from schemas.config import RoutingRule

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    """An editable copy of the shipped config."""
    destination = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", destination)
    return destination


def _write(path: Path, document: object) -> None:
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")


def _only(directory: Path) -> Path:
    """The single YAML file the loader expects in a config directory.

    Resolved rather than named: taxonomy and rule files are SUPERSEDED by a new
    version, never edited, so a test that hard-codes ``routing_v2.yaml`` breaks
    on every version bump for no reason of its own.
    """
    candidates = sorted(directory.glob("*.yaml"))
    assert len(candidates) == 1, f"expected one config file in {directory}"
    return candidates[0]


def test_shipped_config_loads(config: ConfigBundle) -> None:
    assert config.taxonomy.version == "taxonomy_drv_bund_v2"
    assert config.routing.version == "routing_v3"
    assert config.decision_table.version == "table_v1"
    assert config.risk.version == "risk_v0"
    assert set(config.procedures) == {
        "altersrente",
        "erwerbsminderungsrente",
        "statusfeststellung",
    }


def test_taxonomy_covers_all_three_procedures_and_resolves(
    config: ConfigBundle,
) -> None:
    unit_ids = [node.unit_id for node in config.taxonomy.nodes]
    assert 6 <= len(unit_ids) <= 12
    assert "Referat_312_Renten" in unit_ids
    assert "Referat_316_Erwerbsminderungsrenten" in unit_ids
    assert "Referat_340_Clearingstelle" in unit_ids
    assert config.unit("Referat_312_Renten") is not None
    assert config.unit("Referat_999_Nirgendwo") is None
    for node in config.taxonomy.nodes:
        if node.parent_id is not None:
            assert node.parent_id in unit_ids


def test_every_taxonomy_source_is_honest(config: ConfigBundle) -> None:
    """A node either names a public basis or admits it is a placeholder."""
    for node in config.taxonomy.nodes:
        assert "Platzhalter" in node.source, (
            f"{node.unit_id} does not disclose which parts are derived"
        )
        assert "oeffentlich belegt" in node.source or "Abgeleiteter" in node.source, (
            f"{node.unit_id} cites neither a public basis nor a placeholder status"
        )


def test_procedure_flags_are_assembled_into_the_risk_config(
    config: ConfigBundle,
) -> None:
    """Flags have one editable home: config/procedures/."""
    flags = {entry.procedure_id: entry for entry in config.risk.procedures}
    assert set(flags) == {
        "altersrente",
        "erwerbsminderungsrente",
        "statusfeststellung",
    }
    assert flags["altersrente"].tier1_enabled is True
    # Discretion-heavy procedures: tier 1 is closed by config, not by code.
    assert flags["erwerbsminderungsrente"].tier1_enabled is False
    assert flags["statusfeststellung"].tier1_enabled is False
    assert all(entry.fully_automated is False for entry in flags.values())


def test_thresholds_file_may_not_duplicate_procedure_flags(config_copy: Path) -> None:
    document = yaml.safe_load((config_copy / "thresholds.yaml").read_text("utf-8"))
    document["procedures"] = [
        {"procedure_id": "altersrente", "tier1_enabled": True, "fully_automated": False}
    ]
    _write(config_copy / "thresholds.yaml", document)
    with pytest.raises(ConfigError, match="must not list procedures"):
        load_config(config_copy)


def test_scorer_starts_in_log_only(config: ConfigBundle) -> None:
    """ADR-004: the scorer may not enforce until its precision earns it."""
    assert config.risk.scorer_mode == "log_only"
    assert config.risk.downgrade_rate_budget == 0.15


def test_version_stamp_carries_every_config_version(config: ConfigBundle) -> None:
    stamp = config.version_stamp()
    assert stamp.taxonomy_version == config.taxonomy.version
    assert stamp.rules_version == config.routing.version
    assert stamp.decision_table_version == config.decision_table.version
    assert stamp.thresholds_version == config.risk.version


def test_procedure_lookup_of_unknown_id_is_none(config: ConfigBundle) -> None:
    assert config.procedure(None) is None
    assert config.procedure("bauantrag") is None


def test_clear_cut_predicate_is_parsed(config: ConfigBundle) -> None:
    procedure = config.procedure("altersrente")
    assert procedure is not None
    assert procedure.clear_cut_predicate is not None


def test_procedure_without_clear_cut_has_no_predicate() -> None:
    procedure = ProcedureConfig.model_validate(
        {
            "procedure_id": "test",
            "flags": {
                "procedure_id": "test",
                "tier1_enabled": False,
                "fully_automated": False,
            },
            "requirements": {
                "procedure_id": "test",
                "version": "v0",
                "requirements": [
                    {"requirement_id": "a", "description": "a", "kind": "field"}
                ],
            },
        }
    )
    assert procedure.clear_cut_predicate is None


def test_mismatched_procedure_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        ProcedureConfig.model_validate(
            {
                "procedure_id": "test",
                "flags": {
                    "procedure_id": "andere",
                    "tier1_enabled": False,
                    "fully_automated": False,
                },
                "requirements": {
                    "procedure_id": "test",
                    "version": "v0",
                    "requirements": [
                        {"requirement_id": "a", "description": "a", "kind": "field"}
                    ],
                },
            }
        )


def test_unsupported_validation_keys_are_rejected() -> None:
    """A constraint the evaluator does not implement must not load."""
    with pytest.raises(ValidationError, match="unsupported validation"):
        ProcedureConfig.model_validate(
            {
                "procedure_id": "test",
                "flags": {
                    "procedure_id": "test",
                    "tier1_enabled": False,
                    "fully_automated": False,
                },
                "requirements": {
                    "procedure_id": "test",
                    "version": "v0",
                    "requirements": [
                        {
                            "requirement_id": "a",
                            "description": "a",
                            "kind": "field",
                            "validation": {"luhn_checksum": True},
                        }
                    ],
                },
            }
        )


def test_routing_rules_must_reference_known_fixtures() -> None:
    with pytest.raises(ValidationError, match="unknown fixtures"):
        RoutingConfig.model_validate(
            {
                "version": "v0",
                "rules": [
                    {
                        "rule_id": "r",
                        "unit_id": "u",
                        "predicate": {
                            "field": "procedure_hint",
                            "op": "eq",
                            "value": "x",
                        },
                        "fixtures": ["missing"],
                    }
                ],
                "fixtures": [],
            }
        )


def test_fixtures_must_reference_known_rules() -> None:
    with pytest.raises(ValidationError, match="unknown rules"):
        RoutingConfig.model_validate(
            {
                "version": "v0",
                "rules": [
                    {
                        "rule_id": "r",
                        "unit_id": "u",
                        "predicate": {
                            "field": "procedure_hint",
                            "op": "eq",
                            "value": "x",
                        },
                        "fixtures": ["f"],
                    }
                ],
                "fixtures": [
                    {
                        "fixture_id": "f",
                        "description": "d",
                        "expect_rule_ids": ["gibtsnicht"],
                    }
                ],
            }
        )


def test_malformed_rule_predicate_fails_at_load(config_copy: Path) -> None:
    """A typo in a predicate must not load as a rule that never fires."""
    path = _only(config_copy / "rules")
    document = yaml.safe_load(path.read_text("utf-8"))
    document["rules"][0]["predicate"] = {"feld": "procedure_hint"}
    _write(path, document)
    # Pydantic wraps the PredicateError raised inside the model validator.
    with pytest.raises(ValidationError, match="unknown predicate node"):
        load_config(config_copy)


def test_predicate_error_is_raised_when_parsed_directly() -> None:
    from engine.predicate import parse_predicate

    with pytest.raises(PredicateError):
        parse_predicate({"feld": "procedure_hint"})


def test_missing_thresholds_file_is_a_config_error(config_copy: Path) -> None:
    (config_copy / "thresholds.yaml").unlink()
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(config_copy)


def test_missing_procedures_directory_is_a_config_error(config_copy: Path) -> None:
    shutil.rmtree(config_copy / "procedures")
    (config_copy / "procedures").mkdir()
    with pytest.raises(ConfigError, match="no procedure config"):
        load_config(config_copy)


def test_two_decision_tables_are_ambiguous(config_copy: Path) -> None:
    shutil.copy(
        config_copy / "decision" / "table_v1.yaml",
        config_copy / "decision" / "table_v2.yaml",
    )
    with pytest.raises(ConfigError, match="exactly one"):
        load_config(config_copy)


def test_non_mapping_document_is_rejected(config_copy: Path) -> None:
    (config_copy / "thresholds.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML mapping"):
        load_config(config_copy)


def test_config_dir_env_var_is_honoured(
    config_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EINGANGSLOTSE_CONFIG_DIR", str(config_copy))
    assert load_config().config_dir == config_copy


def test_duplicate_taxonomy_units_are_rejected(config_copy: Path) -> None:
    path = _only(config_copy / "taxonomy")
    document = yaml.safe_load(path.read_text("utf-8"))
    document["nodes"].append(document["nodes"][0])
    _write(path, document)
    with pytest.raises(ValidationError, match="duplicate unit_id"):
        load_config(config_copy)


def test_unknown_parent_unit_is_rejected(config_copy: Path) -> None:
    path = _only(config_copy / "taxonomy")
    document = yaml.safe_load(path.read_text("utf-8"))
    document["nodes"][0]["parent_id"] = "Abteilung_Nirgendwo"
    _write(path, document)
    with pytest.raises(ValidationError, match="unknown parent_id"):
        load_config(config_copy)


# ------------------------------------------------- part 03: config surface ---


def _procedure(**overrides: object) -> dict[str, object]:
    """A minimal procedure document; overrides replace whole blocks."""
    document: dict[str, object] = {
        "procedure_id": "test",
        "flags": {
            "procedure_id": "test",
            "tier1_enabled": False,
            "fully_automated": False,
        },
        "requirements": {
            "procedure_id": "test",
            "version": "v0",
            "requirements": [
                {"requirement_id": "a", "description": "Feld A", "kind": "field"},
                {"requirement_id": "b", "description": "Feld B", "kind": "field"},
            ],
        },
    }
    document.update(overrides)
    return document


def _with_validation(validation: dict[str, object]) -> dict[str, object]:
    return _procedure(
        requirements={
            "procedure_id": "test",
            "version": "v0",
            "requirements": [
                {
                    "requirement_id": "a",
                    "description": "Feld A",
                    "kind": "field",
                    "validation": validation,
                },
                {"requirement_id": "b", "description": "Feld B", "kind": "field"},
            ],
        }
    )


@pytest.mark.parametrize(
    ("validation", "message"),
    [
        ({"date": "1900-01-01"}, "must be a mapping"),
        ({"date": {"von": "1900-01-01"}}, "unsupported date keys"),
        ({"date": {"min": "gestern"}}, "not an ISO date"),
        ({"cross_field": {"kind": "not_before"}}, "must be a list"),
        ({"cross_field": ["nope"]}, "must be a mapping"),
        (
            {"cross_field": [{"kind": "gibtsnicht", "field": "b"}]},
            "unknown cross_field",
        ),
        ({"cross_field": [{"kind": "not_before", "field": "z"}]}, "not a requirement"),
        (
            {"cross_field": [{"kind": "min_years_after", "field": "b"}]},
            "needs integer 'years'",
        ),
        (
            {"cross_field": [{"kind": "not_before", "field": "b", "jahre": 3}]},
            "unsupported cross_field keys",
        ),
    ],
)
def test_malformed_validation_blocks_fail_at_load(
    validation: dict[str, object], message: str
) -> None:
    """A constraint that cannot be honoured must never load as 'no problem'."""
    with pytest.raises(ValidationError, match=message):
        ProcedureConfig.model_validate(_with_validation(validation))


def test_a_well_formed_validation_block_loads() -> None:
    procedure = ProcedureConfig.model_validate(
        _with_validation(
            {
                "date": {"min": "1900-01-01", "max": "2050-12-31"},
                "cross_field": [
                    {"kind": "min_years_after", "field": "b", "years": 60},
                    {"kind": "birthdate_in_vsnr", "field": "b", "detail": "Hinweis"},
                ],
            }
        )
    )
    assert procedure.requirements.requirements[0].validation is not None


def test_a_malformed_derivation_predicate_fails_at_load() -> None:
    with pytest.raises(ValidationError, match="unknown predicate node"):
        ProcedureConfig.model_validate(
            _procedure(
                derivation={
                    "signal_id": "s",
                    "description": "d",
                    "predicate": {"feld": "payload.antrag.rentenart"},
                }
            )
        )


def test_nachforderung_texts_must_reference_known_requirements() -> None:
    with pytest.raises(ValidationError, match="unknown requirement"):
        ProcedureConfig.model_validate(
            _procedure(nachforderung=[{"requirement_id": "z", "missing": "Bitte."}])
        )


def test_nachforderung_texts_may_not_be_declared_twice() -> None:
    with pytest.raises(ValidationError, match="duplicate nachforderung"):
        ProcedureConfig.model_validate(
            _procedure(
                nachforderung=[
                    {"requirement_id": "a", "missing": "Bitte."},
                    {"requirement_id": "a", "missing": "Nochmal bitte."},
                ]
            )
        )


def test_every_shipped_requirement_has_nachforderung_wording(
    config: ConfigBundle,
) -> None:
    """A gap a caseworker cannot send is only half a gap."""
    for procedure in config.procedures.values():
        for requirement in procedure.requirements.requirements:
            assert procedure.nachforderung_text(requirement.requirement_id), (
                f"{procedure.procedure_id}.{requirement.requirement_id} has no "
                f"Nachforderung wording"
            )


def test_every_shipped_procedure_declares_content_signals(
    config: ConfigBundle,
) -> None:
    """Without them a procedure can only ever be reached through a hint."""
    for procedure in config.procedures.values():
        assert procedure.derivation is not None
        assert procedure.derivation_predicate is not None


def test_rule_priority_defaults_to_the_middle_of_the_range() -> None:
    """Priority is a CONTRACT field now (ADR-016), not a loader-local shim."""
    from engine.config_loader import DEFAULT_RULE_PRIORITY, rule_order_key

    rule = RoutingRule(
        rule_id="r",
        unit_id="u",
        predicate={"field": "channel", "op": "eq", "value": "email"},
        fixtures=["f"],
    )
    assert rule.priority == DEFAULT_RULE_PRIORITY
    assert rule_order_key(rule) == (DEFAULT_RULE_PRIORITY, "r")


def test_the_loader_hands_out_contract_rules_not_a_shim() -> None:
    """Part 03's RoutingRuleSpec is gone; there is one model for a rule."""
    import engine.config_loader as loader

    assert not hasattr(loader, "RoutingRuleSpec")
    config = RoutingConfig.model_validate(
        {
            "version": "v0",
            "rules": [
                {
                    "rule_id": "r",
                    "unit_id": "u",
                    "priority": 20,
                    "predicate": {"field": "channel", "op": "eq", "value": "email"},
                    "fixtures": ["f"],
                }
            ],
            "fixtures": [{"fixture_id": "f", "description": "d"}],
        }
    )
    assert isinstance(config.rules[0], RoutingRule)
    assert config.rules[0].priority == 20


def test_duplicate_rule_ids_are_rejected() -> None:
    """rule_id is half the arbitration order; duplicates would make it undefined."""
    with pytest.raises(ValidationError, match="duplicate rule_id"):
        RoutingConfig.model_validate(
            {
                "version": "v0",
                "rules": [
                    {
                        "rule_id": "same",
                        "unit_id": "u",
                        "predicate": {"field": "channel", "op": "eq", "value": "email"},
                        "fixtures": ["f"],
                    },
                    {
                        "rule_id": "same",
                        "unit_id": "u2",
                        "predicate": {"field": "channel", "op": "eq", "value": "scan"},
                        "fixtures": ["f"],
                    },
                ],
                "fixtures": [{"fixture_id": "f", "description": "d"}],
            }
        )


def test_the_shipped_rules_carry_explicit_priorities(config: ConfigBundle) -> None:
    """File order stopped being the arbitration policy; priorities are it now."""
    from engine.config_loader import DEFAULT_RULE_PRIORITY, rule_order_key

    priorities = {rule.rule_id: rule.priority for rule in config.routing.rules}
    assert all(priority != DEFAULT_RULE_PRIORITY for priority in priorities.values()), (
        f"a shipped rule left its priority implicit: {priorities}"
    )
    keys = [rule_order_key(rule) for rule in config.routing.rules]
    assert len(set(keys)) == len(keys), "the order must be total, not partial"


def test_statusfeststellung_ships_without_clear_cut_criteria(
    config: ConfigBundle,
) -> None:
    """The one procedure that omits the block, and the reason it may.

    ``clear_cut`` is optional in the loader, and erwerbsminderungsrente uses
    that freedom to record INERT criteria as a documented target state. For
    par. 7a SGB IV there is no target state to record: the decision is a
    Gesamtwuerdigung aller Umstaende des Einzelfalles, so the honest config is
    an absent block, not an empty or always-false one. A future edit that adds
    criteria "for symmetry" has to delete this test first.
    """
    procedure = config.procedure("statusfeststellung")
    assert procedure is not None
    assert procedure.clear_cut is None
    assert procedure.clear_cut_predicate is None
    assert procedure.flags.tier1_enabled is False
    assert procedure.flags.fully_automated is False
    assert procedure.derivation is not None, "content signals are still required"
    assert [entry.requirement_id for entry in procedure.nachforderung] == [
        item.requirement_id for item in procedure.requirements.requirements
    ]


def test_no_procedure_declares_a_document_requirement_yet(
    config: ConfigBundle,
) -> None:
    """Part 04's boundary, asserted rather than remembered.

    ``engine/evidence/completeness.py`` reports MISSING for every document
    requirement, so activating one today would make every item of that
    procedure incomplete and push it to tier 2 - which would hide exactly the
    tier-3-by-design shape the Statusfeststellung exists to show. Both the
    Befundbericht (EM) and the Vertragskopien (par. 7a Abs. 3 SGB IV) wait as
    TODO(part 04) header comments.
    """
    for procedure in config.procedures.values():
        kinds = {item.kind for item in procedure.requirements.requirements}
        assert kinds == {"field"}, f"{procedure.procedure_id} activated a document"


# --------------------------------------------------- the text.* lint (05) ---


def _derivation_predicate(config_dir: Path, predicate: object) -> None:
    """Replace the altersrente derivation predicate with ``predicate``."""
    path = config_dir / "procedures" / "altersrente_v1.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["derivation"]["predicate"] = predicate
    _write(path, document)


def test_the_shipped_text_signals_mirror_the_payload_signals(
    config: ConfigBundle,
) -> None:
    """Every procedure states how to recognise it in prose as well as in a form,
    and states it with the same signature."""
    for procedure in config.procedures.values():
        assert procedure.derivation is not None
        rendered = yaml.safe_dump(procedure.derivation.predicate)
        assert "text.normalized" in rendered, procedure.procedure_id


def test_a_text_rule_may_not_quote_an_identity_value(config_copy: Path) -> None:
    """The presence-only lint protects identity PATHS and cannot see this: a
    text rule names no path, it names a literal - and a Versicherungsnummer in
    a config file is in git, in every report, and on a person."""
    _derivation_predicate(
        config_copy,
        {"field": "text.normalized", "op": "contains", "value": "17170459B012"},
    )
    with pytest.raises(ConfigError, match="identity data"):
        load_config(config_copy)


def test_a_text_rule_on_an_unknown_text_field_is_a_typo_not_a_rule(
    config_copy: Path,
) -> None:
    _derivation_predicate(
        config_copy,
        {"field": "text.raw", "op": "contains", "value": "Altersrente"},
    )
    with pytest.raises(ConfigError, match="unknown text field"):
        load_config(config_copy)


def test_an_ordinary_operator_on_a_whole_letter_is_a_config_error(
    config_copy: Path,
) -> None:
    """``eq`` against a whole letter can never be true and ``gt`` on prose is
    meaningless; both are errors rather than rules that silently never fire."""
    _derivation_predicate(
        config_copy,
        {"field": "text.normalized", "op": "eq", "value": "Altersrente"},
    )
    with pytest.raises(ConfigError, match=r"uses op eq"):
        load_config(config_copy)


def test_asking_whether_an_item_has_any_text_at_all_stays_allowed(
    config_copy: Path,
) -> None:
    _derivation_predicate(
        config_copy,
        {"field": "text.normalized", "op": "ne", "value": None},
    )
    assert load_config(config_copy).procedure("altersrente") is not None


def test_a_broken_matches_pattern_fails_at_startup(config_copy: Path) -> None:
    """Compiled at load time, so an operator can fix it - rather than left as a
    rule that can never fire."""
    _derivation_predicate(
        config_copy,
        {"field": "text.normalized", "op": "matches", "value": "Alters(rente"},
    )
    # Raised inside the procedure model, so pydantic wraps it - the message
    # is what an operator reads, and it names the pattern.
    with pytest.raises(ValidationError, match="not a valid regex"):
        load_config(config_copy)


def test_matches_needs_a_string_pattern(config_copy: Path) -> None:
    _derivation_predicate(
        config_copy,
        {"field": "text.normalized", "op": "matches", "value": 7},
    )
    with pytest.raises(ValidationError, match="string pattern"):
        load_config(config_copy)


def test_the_text_lint_covers_routing_rules_and_clear_cut_criteria_too(
    config_copy: Path,
) -> None:
    """One vocabulary, one lint, all three kinds of predicate."""
    path = config_copy / "procedures" / "altersrente_v1.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["clear_cut"]["predicate"] = {
        "field": "text.normalized",
        "op": "contains",
        "value": "17170459B012",
    }
    _write(path, document)
    with pytest.raises(ConfigError, match="clear-cut criteria"):
        load_config(config_copy)


# --------------------------------------------------------------------------
# The classifier block (config/classifier/) and the review register
# --------------------------------------------------------------------------


def _classifier(config_dir: Path, **changes: object) -> None:
    path = _only(config_dir / "classifier")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(changes)
    _write(path, document)


def test_the_shipped_classifier_ships_disabled_and_uncalibrated(
    config: ConfigBundle,
) -> None:
    """The state the whole design rests on, asserted rather than assumed."""
    assert config.classifier is not None
    assert config.classifier.version == "classifier_v1"
    assert config.classifier.enabled is False
    assert config.classifier.calibration is None


def test_enabling_the_classifier_without_a_calibration_is_refused(
    config_copy: Path,
) -> None:
    _classifier(config_copy, enabled=True)
    with pytest.raises(ValidationError, match="not a confidence"):
        load_config(config_copy)


def test_a_calibration_for_another_model_does_not_enable_this_one(
    config_copy: Path,
) -> None:
    _classifier(
        config_copy,
        enabled=True,
        calibration={
            "calibrated_on": "gold v4",
            "model_id": "some/other-model",
            "fitted_at": "2026-08-12",
            "bins": [{"upper": 1.0, "confidence": 0.9}],
        },
    )
    with pytest.raises(ValidationError, match="says nothing about another"):
        load_config(config_copy)


def test_a_complete_calibration_enables_the_classifier(config_copy: Path) -> None:
    _classifier(
        config_copy,
        enabled=True,
        calibration={
            "calibrated_on": "gold v4",
            "model_id": "intfloat/multilingual-e5-small",
            "fitted_at": "2026-08-12",
            "expected_calibration_error": 0.03,
            "bins": [
                {"upper": 0.8, "confidence": 0.4},
                {"upper": 1.0, "confidence": 0.95},
            ],
        },
    )
    loaded = load_config(config_copy)
    assert loaded.classifier is not None
    assert loaded.classifier.enabled is True
    assert loaded.classifier.calibration is not None


@pytest.mark.parametrize(
    ("bins", "problem"),
    [
        (
            [{"upper": 1.0, "confidence": 0.9}, {"upper": 0.5, "confidence": 0.4}],
            "sorted",
        ),
        (
            [{"upper": 0.5, "confidence": 0.9}, {"upper": 1.0, "confidence": 0.4}],
            "fall",
        ),
        ([{"upper": 0.9, "confidence": 0.9}], "no mapping"),
        (
            [{"upper": 0.5, "confidence": 0.4}, {"upper": 0.5, "confidence": 0.9}],
            "sorted",
        ),
    ],
)
def test_a_calibration_that_is_not_a_monotone_complete_map_is_refused(
    config_copy: Path, bins: list[dict[str, float]], problem: str
) -> None:
    _classifier(
        config_copy,
        calibration={
            "calibrated_on": "gold v4",
            "model_id": "intfloat/multilingual-e5-small",
            "fitted_at": "2026-08-12",
            "bins": bins,
        },
    )
    with pytest.raises(ValidationError, match=problem):
        load_config(config_copy)


def test_a_calibration_without_a_real_date_is_refused(config_copy: Path) -> None:
    _classifier(
        config_copy,
        calibration={
            "calibrated_on": "gold v4",
            "model_id": "intfloat/multilingual-e5-small",
            "fitted_at": "last Tuesday",
            "bins": [{"upper": 1.0, "confidence": 0.9}],
        },
    )
    with pytest.raises(ValidationError, match="not an ISO date"):
        load_config(config_copy)


def test_excluding_a_unit_that_does_not_exist_is_refused(config_copy: Path) -> None:
    _classifier(config_copy, exclude_unit_ids=["Referat_999_Erfunden"])
    with pytest.raises(ConfigError, match="unknown unit"):
        load_config(config_copy)


def test_a_config_without_a_classifier_directory_simply_has_none(
    config_copy: Path,
) -> None:
    """Absent is a choice with a defined meaning, not a degraded state."""
    shutil.rmtree(config_copy / "classifier")
    assert load_config(config_copy).classifier is None


def test_the_review_register_fills_the_contract_field(config: ConfigBundle) -> None:
    assert config.review is not None
    assert config.review.version == "threshold_review_v1"
    assert config.risk.review_due == config.review.review_due.isoformat()


def test_a_config_without_a_review_register_has_no_review_date(
    config_copy: Path,
) -> None:
    shutil.rmtree(config_copy / "review")
    loaded = load_config(config_copy)
    assert loaded.review is None
    assert loaded.risk.review_due is None


def test_thresholds_may_not_set_the_review_date_itself(config_copy: Path) -> None:
    """One editable home, checked rather than trusted - as with procedures."""
    path = config_copy / "thresholds.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["review_due"] = "2027-01-01"
    _write(path, document)
    with pytest.raises(ConfigError, match="one editable home"):
        load_config(config_copy)
