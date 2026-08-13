"""Corpus generator: scenario specs in, labelled gold items out.

Evidence-plane tooling. Nothing in this package is imported by the engine, the
API or the decision path; it exists to produce ``corpus/gold/<version>/`` and to
refuse to produce it when the labels would be wrong.

    scenarios/*.yaml  -> spec.ScenarioSpec       (facts + declared ground truth)
                      -> render.render_payload   (FIT-Connect-shaped payload)
                      -> paraphrase.*            (surface realism, provenance)
                      -> build.self_check        (real pipeline, real labels)
                      -> corpus/gold/v1/         (items, sidecars, MANIFEST)
"""

from __future__ import annotations

from corpus.generator.render import GENERATOR_VERSION, GeneratorError
from corpus.generator.spec import ScenarioKind, ScenarioSpec

__all__ = [
    "GENERATOR_VERSION",
    "GeneratorError",
    "ScenarioKind",
    "ScenarioSpec",
]
