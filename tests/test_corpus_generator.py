"""The corpus generator: specs, rendering, determinism and the self-check.

The tests that matter here are the ones that prove the generator REFUSES:
a wrong label, a paraphrase that changed a fact, or a corpus on disk that no
longer matches its specs must all fail loudly, because a quietly mislabelled
gold item would corrupt every metric downstream of it.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from corpus.generator.build import (
    LABEL_SUFFIX,
    MANIFEST_NAME,
    build_items,
    build_manifest,
    diff_corpus,
    load_specs,
    main,
    render_files,
    self_check,
    validate_specs,
    write_corpus,
)
from corpus.generator.paraphrase import (
    DeterministicParaphraser,
    NullParaphraser,
    assert_labels_preserved,
)
from corpus.generator.render import (
    FIELD_PATHS,
    GeneratorError,
    check_field_paths,
    item_rng,
    mapped_values,
    render_labels,
    render_payload,
)
from corpus.generator.spec import ScenarioSpec, parse_scenario_file
from engine.config_loader import ConfigBundle

SEED = 42
REPO_ROOT = Path(__file__).resolve().parents[1]


def _spec(**overrides: Any) -> ScenarioSpec:
    document: dict[str, Any] = {
        "scenario_id": "ar-9001-testfall",
        "kind": "complete_clear",
        "description": "Ein vollstaendiger Testfall fuer die Unit-Tests.",
        "procedure_id": "altersrente",
        "procedure_hint": "altersrente",
        "facts": {
            "geburtsdatum": "1959-04-17",
            "versicherungsnummer": "17170459B012",
            "rentenart": "regelaltersrente",
            "rentenbeginn": "2026-11-01",
            "auslandsbezug": "nein",
        },
        "expected": {
            "unit_id": "Referat_312_Renten",
            "tier": 1,
            "derivation_source": "hint",
            "gaps": [],
        },
    }
    document.update(overrides)
    return ScenarioSpec.model_validate(document)


# --------------------------------------------------------------- spec model ---


def test_unknown_keys_in_a_spec_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _spec(erwartung="tier 1")


def test_anomalous_items_must_document_their_pattern() -> None:
    with pytest.raises(ValidationError, match="anomaly_pattern"):
        _spec(kind="anomalous_rule_passing", anomaly_expected=True)


def test_anomaly_pattern_without_the_flag_is_rejected() -> None:
    with pytest.raises(ValidationError, match="anomaly_expected"):
        _spec(anomaly_pattern="etwas ist komisch")


def test_unknown_procedure_items_may_not_name_a_procedure() -> None:
    with pytest.raises(ValidationError, match="must not name a"):
        _spec(kind="unknown_procedure")


def test_a_divergence_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="divergence_reason"):
        _spec(
            expected={
                "unit_id": "Referat_312_Renten",
                "tier": 2,
                "derivation_source": "hint",
                "known_divergence": ["unit"],
            }
        )


def test_a_tier_one_item_may_not_declare_a_tier_divergence() -> None:
    """Declaring "tier 1 is expected to be wrong" would license a false clear."""
    with pytest.raises(ValidationError, match="false clear"):
        _spec(
            expected={
                "unit_id": "Referat_312_Renten",
                "tier": 1,
                "derivation_source": "hint",
                "known_divergence": ["tier"],
                "divergence_reason": "weil ich es sage",
            }
        )


def test_scenario_file_errors_name_the_file() -> None:
    with pytest.raises(ValueError, match=r"kaputt\.yaml"):
        parse_scenario_file({"description": "x"}, source="kaputt.yaml")
    with pytest.raises(ValueError, match="YAML mapping"):
        parse_scenario_file(["nope"], source="kaputt.yaml")


# ------------------------------------------------------------ shipped specs ---


def test_shipped_scenarios_load_and_validate(
    scenario_dir: Path, config: ConfigBundle
) -> None:
    specs = load_specs(scenario_dir)
    assert 95 <= len(specs) <= 115, "the gold set must stay between 95 and 115 items"
    validate_specs(specs, config)
    kinds = {spec.kind.value for spec in specs}
    assert kinds == {
        "complete_clear",
        "missing_field",
        "invalid_field",
        "ambiguous_conflicting",
        "unknown_procedure",
        "anomalous_rule_passing",
        "hint_missing",
    }
    sources = {spec.expected.derivation_source for spec in specs}
    assert sources == {"hint", "content", "none"}, (
        "every derivation route needs items measuring it"
    )
    assert sum(1 for spec in specs if spec.anomaly_expected) >= 5
    assert {spec.procedure_id for spec in specs} == {
        "altersrente",
        "erwerbsminderungsrente",
        "statusfeststellung",
        None,
    }


def test_specs_referencing_unknown_units_are_rejected(config: ConfigBundle) -> None:
    with pytest.raises(GeneratorError, match="not in the taxonomy"):
        validate_specs(
            [
                _spec(
                    expected={
                        "unit_id": "Referat_999",
                        "tier": 1,
                        "derivation_source": "hint",
                    }
                )
            ],
            config,
        )


def test_specs_referencing_unknown_requirements_are_rejected(
    config: ConfigBundle,
) -> None:
    with pytest.raises(GeneratorError, match="not a requirement"):
        validate_specs(
            [
                _spec(
                    expected={
                        "unit_id": "Referat_312_Renten",
                        "tier": 2,
                        "derivation_source": "hint",
                        "gaps": [{"requirement_id": "lieblingsfarbe"}],
                    }
                )
            ],
            config,
        )


def test_generator_and_config_agree_about_payload_paths(config: ConfigBundle) -> None:
    check_field_paths(config)


def test_a_config_that_moves_a_field_aborts_the_build(config: ConfigBundle) -> None:
    procedure = config.procedures["altersrente"]
    moved = procedure.model_copy(
        update={
            "field_map": [
                entry.model_copy(update={"path": "woanders.geburtsdatum"})
                if entry.field == "geburtsdatum"
                else entry
                for entry in procedure.field_map
            ]
        }
    )
    # replace() rather than a full constructor call: the bundle gains fields
    # (part 04 added the redaction policy) and a test that re-lists all of them
    # breaks for reasons that have nothing to do with what it checks.
    broken = replace(config, procedures={**config.procedures, "altersrente": moved})
    with pytest.raises(GeneratorError, match="disagree about payload paths"):
        check_field_paths(broken)


# -------------------------------------------------------- render and labels ---


def test_labels_come_from_the_spec_not_from_the_payload() -> None:
    spec = _spec()
    labels = render_labels(spec, paraphrase="deterministic")
    assert labels["expected_tier"] == spec.expected.tier
    assert labels["expected_unit_id"] == spec.expected.unit_id
    assert labels["item_id"] == spec.scenario_id
    assert labels["procedure_id"] == "altersrente"
    assert labels["paraphrase"] == "deterministic"
    assert labels["notes"] == spec.description


def test_every_fact_lands_where_the_mapper_looks_for_it() -> None:
    spec = _spec()
    payload = render_payload(spec, rng=item_rng(SEED, spec.scenario_id))
    assert mapped_values(payload) == spec.facts
    for field in spec.facts:
        assert field in FIELD_PATHS


def test_a_fact_without_a_payload_path_is_a_build_error() -> None:
    spec = _spec(facts={"lieblingsfarbe": "blau"})
    with pytest.raises(GeneratorError, match="no payload path"):
        render_payload(spec, rng=item_rng(SEED, spec.scenario_id))


def test_render_is_deterministic_for_a_seed() -> None:
    spec = _spec()
    first = render_payload(spec, rng=item_rng(SEED, spec.scenario_id))
    second = render_payload(spec, rng=item_rng(SEED, spec.scenario_id))
    assert first == second


def test_a_different_seed_changes_the_surface_but_not_the_facts() -> None:
    spec = _spec()
    paraphraser = DeterministicParaphraser()
    first = paraphraser.apply(
        spec, render_payload(spec, rng=item_rng(1, spec.scenario_id)), item_rng(1, "a")
    )
    second = paraphraser.apply(
        spec, render_payload(spec, rng=item_rng(2, spec.scenario_id)), item_rng(2, "a")
    )
    assert first.payload != second.payload
    assert mapped_values(first.payload) == mapped_values(second.payload)


# ---------------------------------------------------------------- paraphrase ---


def test_paraphrase_preserves_every_mapper_visible_value() -> None:
    spec = _spec()
    canonical = render_payload(spec, rng=item_rng(SEED, spec.scenario_id))
    result = DeterministicParaphraser().apply(
        spec, canonical, item_rng(SEED, spec.scenario_id)
    )
    assert mapped_values(result.payload) == mapped_values(canonical)
    assert result.provenance == "deterministic"
    assert result.payload["data"]["antrag"]["hinweistext"]


def test_the_paraphrase_guard_catches_a_changed_fact() -> None:
    spec = _spec()
    before = render_payload(spec, rng=item_rng(SEED, spec.scenario_id))
    after = json.loads(json.dumps(before))
    after["data"]["antrag"]["rentenart"] = "altersrente_langjaehrig"
    with pytest.raises(GeneratorError, match="paraphrase changed"):
        assert_labels_preserved(spec, before, after)


def test_the_null_paraphraser_changes_nothing() -> None:
    spec = _spec()
    canonical = render_payload(spec, rng=item_rng(SEED, spec.scenario_id))
    result = NullParaphraser().apply(spec, canonical, item_rng(SEED, "x"))
    assert result.payload == canonical
    assert result.provenance == "none"


# ---------------------------------------------------------------- self-check ---


def test_self_check_rejects_a_wrong_tier_label(config: ConfigBundle) -> None:
    """An over-optimistic label (gold tier 1, pipeline tier 2) fails the build."""
    spec = _spec(
        facts={
            "geburtsdatum": "1959-04-17",
            "rentenart": "regelaltersrente",
            "rentenbeginn": "2026-11-01",
            "auslandsbezug": "nein",
        },
        expected={
            "unit_id": "Referat_312_Renten",
            "tier": 1,
            "derivation_source": "hint",
            "gaps": [{"requirement_id": "versicherungsnummer", "status": "missing"}],
        },
    )
    items = build_items([spec], seed=SEED, paraphraser=DeterministicParaphraser())
    with pytest.raises(GeneratorError, match="expected tier 1, got 2"):
        self_check(items, config)


def test_self_check_rejects_a_false_clear_label(config: ConfigBundle) -> None:
    """Gold says oversight, the pipeline clears it: the fatal error class."""
    spec = _spec(
        expected={
            "unit_id": "Referat_312_Renten",
            "tier": 2,
            "derivation_source": "hint",
            "known_divergence": ["tier"],
            "divergence_reason": "Ich behaupte, das darf abweichen.",
        }
    )
    items = build_items([spec], seed=SEED, paraphraser=DeterministicParaphraser())
    with pytest.raises(GeneratorError, match="FALSE CLEAR"):
        self_check(items, config)


def test_self_check_rejects_a_divergence_that_did_not_happen(
    config: ConfigBundle,
) -> None:
    spec = _spec(
        expected={
            "unit_id": "Referat_312_Renten",
            "tier": 1,
            "derivation_source": "hint",
            "known_divergence": ["unit"],
            "divergence_reason": "Behauptete Abweichung, die es nicht gibt.",
        }
    )
    items = build_items([spec], seed=SEED, paraphraser=DeterministicParaphraser())
    with pytest.raises(GeneratorError, match="did not happen"):
        self_check(items, config)


def test_self_check_rejects_a_wrong_gap_list(config: ConfigBundle) -> None:
    spec = _spec(
        expected={
            "unit_id": "Referat_312_Renten",
            "tier": 1,
            "derivation_source": "hint",
            "gaps": [{"requirement_id": "rentenbeginn", "status": "missing"}],
        }
    )
    items = build_items([spec], seed=SEED, paraphraser=DeterministicParaphraser())
    with pytest.raises(GeneratorError, match="expected gaps"):
        self_check(items, config)


def test_the_shipped_corpus_still_passes_the_self_check(
    scenario_dir: Path, config: ConfigBundle
) -> None:
    specs = load_specs(scenario_dir)
    items = build_items(specs, seed=SEED, paraphraser=DeterministicParaphraser())
    self_check(items, config)


# ------------------------------------------------------------------ manifest ---


def test_manifest_counts_and_hashes_match_the_items(
    scenario_dir: Path, config: ConfigBundle
) -> None:
    specs = load_specs(scenario_dir)
    items = build_items(specs, seed=SEED, paraphraser=DeterministicParaphraser())
    manifest = build_manifest(
        items,
        seed=SEED,
        config=config,
        scenario_dir=scenario_dir,
        paraphrase_mode="deterministic",
        llm_model=None,
    )
    assert manifest["frozen"] is True
    assert "never edited" in manifest["policy"]
    assert manifest["counts"]["items"] == len(items)
    assert sum(manifest["counts"]["by_kind"].values()) == len(items)
    assert sum(manifest["counts"]["by_paraphrase"].values()) == len(items)
    by_id = {entry["item_id"]: entry for entry in manifest["items"]}
    for item in items:
        assert by_id[item.item_id]["sha256"] == item.sha256()


def test_the_committed_manifest_matches_the_committed_items(
    gold_v2_dir: Path,
) -> None:
    """Integrity check on what is actually in the repo, not on a rebuild."""
    import hashlib

    manifest = yaml.safe_load((gold_v2_dir / MANIFEST_NAME).read_text("utf-8"))
    assert manifest["frozen"] is True
    assert manifest["counts"]["items"] == len(manifest["items"])
    for entry in manifest["items"]:
        payload = (gold_v2_dir / f"{entry['item_id']}.json").read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"], entry["item_id"]
        labels = yaml.safe_load(
            (gold_v2_dir / f"{entry['item_id']}{LABEL_SUFFIX}").read_text("utf-8")
        )
        assert labels["expected_tier"] == entry["expected_tier"]
        assert labels["paraphrase"] == entry["paraphrase"]


# ----------------------------------------------------------------------- CLI ---


def _build_to(tmp_path: Path, scenario_dir: Path, *, seed: int = SEED) -> int:
    return main(
        [
            "--out",
            str(tmp_path),
            "--seed",
            str(seed),
            "--scenarios",
            str(scenario_dir),
            "--paraphrase",
            "deterministic",
        ]
    )


def test_cli_build_is_byte_identical_on_a_rerun(
    tmp_path: Path, scenario_dir: Path
) -> None:
    assert _build_to(tmp_path, scenario_dir) == 0
    first = {path.name: path.read_bytes() for path in sorted(tmp_path.iterdir())}
    assert _build_to(tmp_path, scenario_dir) == 0
    second = {path.name: path.read_bytes() for path in sorted(tmp_path.iterdir())}
    assert first == second
    assert MANIFEST_NAME in first


def test_cli_check_mode_detects_an_edited_item(
    tmp_path: Path, scenario_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _build_to(tmp_path, scenario_dir) == 0
    edited = next(tmp_path.glob("ar-*.json"))
    edited.write_text("{}\n", encoding="utf-8", newline="\n")
    exit_code = main(
        [
            "--out",
            str(tmp_path),
            "--scenarios",
            str(scenario_dir),
            "--paraphrase",
            "deterministic",
            "--check",
        ]
    )
    assert exit_code == 1
    assert "differs" in capsys.readouterr().err


def test_the_committed_corpus_matches_its_specs(
    gold_v1_dir: Path, scenario_dir: Path
) -> None:
    """The frozen gold set is exactly what the committed specs produce."""
    exit_code = main(
        [
            "--out",
            str(gold_v1_dir),
            "--seed",
            "42",
            "--scenarios",
            str(scenario_dir),
            "--paraphrase",
            "deterministic",
            "--check",
        ]
    )
    assert exit_code == 0


def test_stale_generated_files_are_removed(tmp_path: Path, scenario_dir: Path) -> None:
    specs = load_specs(scenario_dir)[:2]
    items = build_items(specs, seed=SEED, paraphraser=DeterministicParaphraser())
    manifest = {"counts": {"items": len(items)}}
    files = render_files(items, manifest, seed=SEED)
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "alt-0001.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("nicht generiert", encoding="utf-8")
    removed = write_corpus(files, tmp_path)
    assert removed == ["alt-0001.json"]
    assert not (tmp_path / "alt-0001.json").exists()
    assert (tmp_path / "README.md").exists(), "hand-written files stay"
    assert diff_corpus(files, tmp_path) == []


def test_a_failing_build_writes_nothing(tmp_path: Path, scenario_dir: Path) -> None:
    broken = tmp_path / "scenarios"
    broken.mkdir()
    document = yaml.safe_load((scenario_dir / "altersrente.yaml").read_text("utf-8"))
    document["scenarios"] = document["scenarios"][:1]
    document["scenarios"][0]["expected"]["tier"] = 3
    (broken / "kaputt.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True), encoding="utf-8"
    )
    out = tmp_path / "out"
    exit_code = main(
        ["--out", str(out), "--scenarios", str(broken), "--paraphrase", "deterministic"]
    )
    assert exit_code == 2
    assert not out.exists()


# ------------------------------------------------ frozen sets and registry ---


def test_the_registry_knows_every_set_on_disk() -> None:
    """A gold directory nobody registered is a set nobody can verify."""
    from corpus.generator.build import load_registry

    registry = load_registry()
    on_disk = {
        path.name for path in (REPO_ROOT / "corpus" / "gold").iterdir() if path.is_dir()
    }
    assert on_disk <= set(registry), (
        f"unregistered gold sets: {on_disk - set(registry)}"
    )
    current = [
        name for name, entry in registry.items() if entry.get("status") == "current"
    ]
    assert current == ["v4"], "exactly one set is current"
    for name, entry in registry.items():
        if entry.get("status") == "superseded":
            assert entry.get("superseded_by"), f"{name} claims no successor"


def test_frozen_sets_are_verified_by_integrity_not_by_rebuild(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-running today's engine over a superseded set is meant to disagree."""
    assert main(["--out", "corpus/gold/v1", "--check"]) == 0
    output = capsys.readouterr().out
    assert "superseded by v2" in output
    assert "integrity" in output


def test_integrity_catches_a_hand_edited_frozen_item(tmp_path: Path) -> None:
    from corpus.generator.build import verify_integrity

    frozen = tmp_path / "v1"
    shutil.copytree(REPO_ROOT / "corpus" / "gold" / "v1", frozen)
    assert verify_integrity(frozen) == []

    victim = next(frozen.glob("ar-0001*.json"))
    victim.write_text(
        victim.read_text(encoding="utf-8").replace("nein", "ja"), encoding="utf-8"
    )
    (frozen / "geschmuggelt.json").write_text("{}", encoding="utf-8")
    problems = verify_integrity(frozen)
    assert any("sha256 mismatch" in problem for problem in problems)
    assert any("not listed in the manifest" in problem for problem in problems)


def test_integrity_reports_a_missing_item_and_a_missing_manifest(
    tmp_path: Path,
) -> None:
    from corpus.generator.build import verify_integrity

    assert "nothing to verify against" in verify_integrity(tmp_path)[0]

    frozen = tmp_path / "v1"
    shutil.copytree(REPO_ROOT / "corpus" / "gold" / "v1", frozen)
    next(frozen.glob("ar-0002*.json")).unlink()
    assert any("missing:" in problem for problem in verify_integrity(frozen))


def test_the_manifest_records_the_derivation_shape_of_the_set(
    gold_v2_dir: Path,
) -> None:
    manifest = yaml.safe_load((gold_v2_dir / MANIFEST_NAME).read_text("utf-8"))
    assert manifest["gold_set"] == "v2"
    assert set(manifest["counts"]["by_derivation_source"]) == {
        "hint",
        "content",
        "none",
    }
    assert all("derivation_source" in entry for entry in manifest["items"])


def test_a_set_without_a_manifest_reports_that_rather_than_failing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """s1 is hand-written pre-gold: there is no claim to verify, and no lie."""
    assert main(["--out", "corpus/gold/s1", "--check"]) == 0
    output = capsys.readouterr().out
    assert "nothing to verify" in output
