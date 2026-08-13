"""Deterministic renderer: scenario facts to a FIT-Connect-shaped payload.

Ground truth is emitted from the same ``facts`` mapping that renders the
payload, never re-derived by parsing the rendered file. That is the whole point
of the generator: a label and its payload have one source, so a corpus item
cannot be mislabelled by a rendering bug - it can only be missing.

The one place the two could drift apart is the payload path a field is written
to. :data:`FIELD_PATHS` is the generator's single answer to "where does field X
live in the payload", and :func:`check_field_paths` refuses to build if any
procedure's ``field_map`` disagrees with it. A silent disagreement would produce
items whose values the mapper cannot see, which would look like a corpus full of
missing fields.

Nothing here uses the wall clock or unseeded randomness: the same
(specs, seed, generator version) triple always produces byte-identical output.
"""

from __future__ import annotations

import random
from typing import Any

from corpus.generator.letter import render_letter
from corpus.generator.spec import ScenarioSpec
from engine.config_loader import ConfigBundle
from engine.extract import FIXTURE_KEY

#: Generator version; part of the corpus identity recorded in the MANIFEST.
GENERATOR_VERSION = "corpus_generator_v1"

#: Field id -> payload path. The single answer to "where does this field live".
#:
#: Not every entry is a requirement of some procedure. The Statusfeststellung
#: block below carries the Indizien of the par. 7a Abs. 2 SGB IV
#: Gesamtwuerdigung (Weisungsgebundenheit, Eingliederung, Umsatzanteil,
#: Honorarmodell ...), which are deliberately NOT requirements: a scenario has
#: to be able to state them as facts so the anomaly patterns have something to
#: describe, without the completeness checker turning an Abwaegung into a
#: checklist. ``check_field_paths`` only walks config -> generator, so an entry
#: no ``field_map`` mentions is fine; the reverse never is.
FIELD_PATHS: dict[str, str] = {
    "geburtsdatum": "antragsteller.geburtsdatum",
    "versicherungsnummer": "antragsteller.versicherungsnummer",
    "rentenart": "antrag.rentenart",
    "rentenbeginn": "antrag.rentenbeginn",
    "auslandsbezug": "antrag.auslandsbezug",
    "eintritt_erwerbsminderung": "antrag.eintritt_erwerbsminderung",
    "letzte_taetigkeit": "antrag.letzte_taetigkeit",
    "gutachten_status": "antrag.gutachten_status",
    # Statusfeststellung nach par. 7a SGB IV: Pflichtangaben (V0027) ...
    "antragsart": "antrag.antragsart",
    "antragsteller_rolle": "antrag.antragsteller_rolle",
    "taetigkeit_bezeichnung": "antrag.taetigkeit_bezeichnung",
    "taetigkeit_beginn": "antrag.taetigkeit_beginn",
    "auftraggeber_name": "auftraggeber.firmenname",
    # ... und die Indizien der Gesamtwuerdigung, die keine sind.
    "weisungsgebunden": "antrag.weisungsgebunden",
    "eingliederung_arbeitsorganisation": "antrag.eingliederung_arbeitsorganisation",
    "arbeitsort": "antrag.arbeitsort",
    "weitere_auftraggeber": "antrag.weitere_auftraggeber",
    "umsatzanteil_hauptauftraggeber": "antrag.umsatzanteil_hauptauftraggeber",
    "honorar_modell": "antrag.honorar_modell",
    "honorar_monatlich": "antrag.honorar_monatlich",
    "rahmenvertrag": "antrag.rahmenvertrag",
    "dreiecksverhaeltnis": "antrag.dreiecksverhaeltnis",
    "auftraggeber_betriebsnummer": "auftraggeber.betriebsnummer",
}

#: Payload keys the paraphrase pass may invent. Kept disjoint from FIELD_PATHS
#: so surface variation can never touch a value the mapper reads.
DECORATIVE_PATHS = (
    "antragsteller.anschrift",
    "antrag.hinweistext",
    "meta.eingangsdatum",
    "meta.bearbeitungswunsch",
)

DESTINATION_ID = "drv-bund-eingang-test"

#: Every address below is invented; see corpus/gold/v1/README.md.
_ADDRESSES: tuple[dict[str, str], ...] = (
    {"strasse": "Musterweg", "hausnummer": "3", "plz": "10115", "ort": "Musterstadt"},
    {
        "strasse": "Lindenallee",
        "hausnummer": "17a",
        "plz": "04109",
        "ort": "Beispielau",
    },
    {"strasse": "Am Hang", "hausnummer": "8", "plz": "99084", "ort": "Musterhausen"},
    {
        "strasse": "Kirchgasse",
        "hausnummer": "2",
        "plz": "24103",
        "ort": "Beispielstadt",
    },
    {"strasse": "Feldstrasse", "hausnummer": "45", "plz": "70173", "ort": "Musterdorf"},
)

_SUBMITTED_AT = (
    "2026-07-27T08:04:00+00:00",
    "2026-07-29T11:37:00+00:00",
    "2026-08-03T09:12:00+00:00",
    "2026-08-05T14:48:00+00:00",
    "2026-08-06T07:21:00+00:00",
)

_SERVICE_TYPES: dict[str | None, dict[str, str]] = {
    "altersrente": {
        "name": "Antrag auf Altersrente",
        "identifier": "urn:de:fim:leika:leistung:99000000000000000",
    },
    "erwerbsminderungsrente": {
        "name": "Antrag auf Rente wegen Erwerbsminderung",
        "identifier": "urn:de:fim:leika:leistung:99000000000000001",
    },
    "statusfeststellung": {
        "name": "Antrag auf Feststellung des Erwerbsstatus",
        "identifier": "urn:de:fim:leika:leistung:99000000000000002",
    },
    None: {
        "name": "Allgemeines Anliegen",
        "identifier": "urn:de:fim:leika:leistung:99000000000000009",
    },
}


class GeneratorError(RuntimeError):
    """Raised when the generator refuses to build a corpus."""


def check_field_paths(config: ConfigBundle) -> None:
    """Refuse to build when a procedure maps a field to a different path.

    Raises:
        GeneratorError: on the first disagreement, listing every problem found.
    """
    problems: list[str] = []
    for procedure in config.procedures.values():
        for entry in procedure.field_map:
            expected = FIELD_PATHS.get(entry.field)
            if expected is None:
                problems.append(
                    f"{procedure.procedure_id}: field {entry.field!r} is mapped in "
                    f"the config but unknown to the generator (FIELD_PATHS)"
                )
            elif expected != entry.path:
                problems.append(
                    f"{procedure.procedure_id}: field {entry.field!r} is at "
                    f"{entry.path!r} in the config but at {expected!r} in the "
                    f"generator"
                )
    if problems:
        raise GeneratorError(
            "generator and config disagree about payload paths:\n  "
            + "\n  ".join(problems)
        )


def item_rng(seed: int, scenario_id: str) -> random.Random:
    """Per-item RNG, derived from the build seed and the scenario id.

    Explicitly seeded and explicitly passed: no module-level RNG exists, so
    adding an item cannot change the surface variation of the items before it.
    """
    return random.Random(f"{GENERATOR_VERSION}:{seed}:{scenario_id}")


def render_payload(spec: ScenarioSpec, *, rng: random.Random) -> dict[str, Any]:
    """Render the canonical (un-paraphrased) submission payload for one spec."""
    # The data block is rendered FIRST because it is the first thing that
    # draws from the per-item RNG, and the draw order is part of the corpus
    # identity: a refactor that reordered it would rewrite 77 frozen items
    # while changing nothing anybody asked to change.
    data = _render_data(spec, rng=rng)
    payload: dict[str, Any] = {
        "submissionId": spec.scenario_id,
        "destinationId": DESTINATION_ID,
        "channel": spec.channel,
        "submittedAt": rng.choice(_SUBMITTED_AT),
        "serviceType": dict(
            _SERVICE_TYPES.get(spec.procedure_id, _SERVICE_TYPES[None])
        ),
        "data": data,
        "attachments": [],
    }
    if spec.letter is not None:
        letter = render_letter(
            subject=spec.letter.subject,
            opening=spec.letter.opening,
            closing=spec.letter.closing,
            facts=dict(spec.facts),
            with_sender=spec.letter.with_sender,
            ocr_noise=spec.letter.ocr_noise,
            rng=rng,
        )
        payload["bodyText"] = letter.text
        payload[FIXTURE_KEY] = letter.fixture
    if spec.procedure_hint is not None:
        payload["procedureHint"] = spec.procedure_hint
    return payload


def _render_data(spec: ScenarioSpec, *, rng: random.Random) -> dict[str, Any]:
    """The structured half of a submission - EMPTY for a letter item.

    Not "mostly empty": a letter item that still carried its facts as JSON would
    be answered by the schema mapper and would measure nothing about the text
    path, and a decorative address in ``data`` would be a structured leaf on an
    item that arrived as an e-mail. The sender's address of a letter belongs in
    the letter, where the detector union has to find it.
    """
    if spec.letter is not None:
        return {}
    data: dict[str, Any] = {}
    for field, value in spec.facts.items():
        path = FIELD_PATHS.get(field)
        if path is None:
            raise GeneratorError(
                f"{spec.scenario_id}: fact {field!r} has no payload path; add it "
                f"to FIELD_PATHS and to the procedure's field_map"
            )
        _set_path(data, path, value)
    _set_path(data, "antragsteller.anschrift", dict(rng.choice(_ADDRESSES)))
    return data


def render_labels(spec: ScenarioSpec, *, paraphrase: str) -> dict[str, Any]:
    """Render the label sidecar for one spec.

    Every value comes from the spec, not from the rendered payload.
    """
    labels: dict[str, Any] = {
        "item_id": spec.scenario_id,
        "procedure_id": spec.procedure_id,
        "scenario_kind": spec.kind.value,
        "expected_unit_id": spec.expected.unit_id,
        "expected_tier": spec.expected.tier,
        "derived_procedure_id": spec.expected_procedure_id,
        "derivation_source": spec.expected.derivation_source,
        "expected_gaps": [
            {"requirement_id": gap.requirement_id, "status": gap.status}
            for gap in spec.expected.gaps
        ],
        "anomaly_expected": spec.anomaly_expected,
        "anomaly_pattern": spec.anomaly_pattern,
        "paraphrase": paraphrase,
        "known_divergence": list(spec.expected.known_divergence),
        "divergence_reason": spec.expected.divergence_reason,
        "notes": spec.notes or spec.description,
    }
    return labels


def mapped_values(payload: dict[str, Any]) -> dict[str, str]:
    """Every value the mapper would read, keyed by field id.

    Used as the label-preservation guard around the paraphrase pass: a surface
    variation that changes any entry here has changed ground truth.
    """
    data = payload.get("data", {})
    values: dict[str, str] = {}
    for field, path in FIELD_PATHS.items():
        value = _resolve(data, path)
        if isinstance(value, str):
            values[field] = value.strip()
        elif value is not None:
            values[field] = str(value).strip()
    return values


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    segments = path.split(".")
    current = target
    for segment in segments[:-1]:
        child = current.setdefault(segment, {})
        if not isinstance(child, dict):
            raise GeneratorError(f"path {path!r} collides with a scalar value")
        current = child
    current[segments[-1]] = value


def _resolve(payload: Any, path: str) -> Any:
    current = payload
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current
