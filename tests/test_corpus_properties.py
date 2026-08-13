"""Hypothesis properties for the generator.

The invariant worth fuzzing is not "the labels are right" (that is what the
build's self-check asserts against the real pipeline); it is that surface
variation is *inert*: whatever facts a spec declares, the paraphrased payload
must run through the pipeline and produce exactly the outcome the canonical
payload produces. Every corpus item's ground truth rests on that.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from corpus.generator.paraphrase import DeterministicParaphraser
from corpus.generator.render import item_rng, mapped_values, render_payload
from corpus.generator.spec import ScenarioSpec
from engine.config_loader import ConfigBundle, load_config
from engine.evidence import derive_procedure
from engine.ingest.envelope import build_envelope
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline

CONFIG: ConfigBundle = load_config()
PARAPHRASER = DeterministicParaphraser()

_DATES = st.sampled_from(
    ["1955-01-01", "1959-04-17", "1980-12-31", "17.04.1959", "2026-11-01", "2029-02-30"]
)
_VSNR = st.sampled_from(
    ["17045917B012", "17045917b012", "1704", "01010157A001", "  17045917B012  "]
)
_RENTENART = st.sampled_from(
    [
        "regelaltersrente",
        "altersrente_langjaehrig",
        "erwerbsminderungsrente_voll",
        "witwenrente",
        "",
    ]
)
_JA_NEIN = st.sampled_from(["ja", "nein", "vielleicht"])
_TAETIGKEIT = st.sampled_from(["Maurer", "IT", "x" * 130, "Pflegehelferin"])


@st.composite
def scenario_specs(draw: st.DrawFn) -> ScenarioSpec:
    """Random specs over the fact space the two procedures span."""
    procedure_id = draw(
        st.sampled_from(["altersrente", "erwerbsminderungsrente", None])
    )
    facts: dict[str, str] = {}
    for field, strategy in (
        ("geburtsdatum", _DATES),
        ("versicherungsnummer", _VSNR),
        ("rentenart", _RENTENART),
        ("rentenbeginn", _DATES),
        ("auslandsbezug", _JA_NEIN),
        ("eintritt_erwerbsminderung", _DATES),
        ("letzte_taetigkeit", _TAETIGKEIT),
        ("gutachten_status", st.sampled_from(["liegt_vor", "ausstehend"])),
    ):
        if draw(st.booleans()):
            facts[field] = draw(strategy)
    index = draw(st.integers(min_value=0, max_value=9999))
    return ScenarioSpec.model_validate(
        {
            "scenario_id": f"fuzz-{index:04d}-generiert",
            "kind": "unknown_procedure" if procedure_id is None else "invalid_field",
            "description": "Zufaellig erzeugter Spec fuer die Property-Tests.",
            "procedure_id": procedure_id,
            "procedure_hint": draw(
                st.sampled_from([procedure_id, None, "reha", "grundsicherung"])
            ),
            "facts": facts,
            # The property tests never assert on these labels - the build's
            # self-check does that - so "nothing was derivable" is the honest
            # placeholder rather than a claim the fuzzer cannot make.
            "expected": {"unit_id": None, "tier": 3, "derivation_source": "none"},
        }
    )


def _run(payload: dict[str, Any]) -> tuple[int, str | None, list[tuple[str, str]]]:
    outcome = run_pipeline(payload, config=CONFIG, journal=InMemoryJournalStore())
    return (
        int(outcome.decision.tier),
        outcome.decision.routed_unit_id,
        sorted(
            (gap.requirement_id, gap.status.value)
            for gap in outcome.evidence.completeness.gaps
        ),
    )


@given(spec=scenario_specs(), seed=st.integers(min_value=0, max_value=1000))
def test_rendered_items_are_ingestable_and_paraphrase_is_inert(
    spec: ScenarioSpec, seed: int
) -> None:
    canonical = render_payload(spec, rng=item_rng(seed, spec.scenario_id))
    varied = PARAPHRASER.apply(
        spec, canonical, item_rng(seed, spec.scenario_id)
    ).payload
    assert mapped_values(varied) == mapped_values(canonical)
    assert _run(varied) == _run(canonical)


@given(spec=scenario_specs(), seed=st.integers(min_value=0, max_value=1000))
def test_generation_is_reproducible_for_a_seed(spec: ScenarioSpec, seed: int) -> None:
    def build() -> dict[str, Any]:
        rng = item_rng(seed, spec.scenario_id)
        return PARAPHRASER.apply(spec, render_payload(spec, rng=rng), rng).payload

    assert build() == build()


@given(spec=scenario_specs(), seed=st.integers(min_value=0, max_value=1000))
def test_no_item_is_ever_cleared_without_a_derivable_procedure(
    spec: ScenarioSpec, seed: int
) -> None:
    """Tier 1 requires an evaluable procedure; fuzzing must not find a way out.

    Part 03 widened what "known" means: a procedure may now come from content
    as well as from the channel hint, so the precondition is the derivation
    outcome, not the hint. What did NOT widen is the consequence - no
    procedure still means no completeness verdict and therefore no clearance.
    """
    payload = render_payload(spec, rng=item_rng(seed, spec.scenario_id))
    envelope = build_envelope(payload, versions=CONFIG.version_stamp())
    derivation = derive_procedure(envelope, CONFIG.procedures)
    tier, _, _ = _run(payload)
    if derivation.procedure_id is None:
        assert tier > 1
    if derivation.ambiguous or derivation.hint_contradicted:
        assert derivation.procedure_id is None, "a refusal may never name a procedure"
