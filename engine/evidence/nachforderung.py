"""Turn gaps into sentences a caseworker can send (consumed by part 08).

A ``GapItem`` is machine shape: a requirement id, a status and a technical
detail with the payload path and the constraint that failed. None of that goes
to an applicant. This module is the one place that turns it into German prose,
and it is deliberately *not* a drafting step: it renders config-authored
wording with substituted values, it does not compose text. The LLM drafting
path in part 08 starts from this data, and having the deterministic version
first means there is always something to fall back to.

Two outputs per gap:

* ``sentence`` - the request, ready to paste into a Nachforderung;
* ``template_data`` - the same facts as a flat mapping, so part 08 can feed a
  template (or a model) without re-parsing prose.

Wording lives in each procedure's ``nachforderung:`` block, keyed by
requirement id, with a ``missing`` and an optional ``invalid`` variant.
Placeholders use the ``{name}`` form the decision table already uses, and an
unknown placeholder stays literal rather than raising: agency-editable config
must not be able to crash the evidence plane.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from engine.config_loader import ProcedureConfig
from schemas.config import Requirement
from schemas.evidence import CompletenessEvidence, GapItem, RequirementStatus

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

#: Fallbacks when a procedure has not authored wording for a requirement. They
#: are usable, not polished: the point is that a new requirement produces a
#: sensible sentence on day one instead of an empty string.
DEFAULT_MISSING = "Bitte reichen Sie folgende Angabe nach: {description}"
DEFAULT_INVALID = (
    "Bitte pruefen und berichtigen Sie folgende Angabe: {description} ({problem})"
)


@dataclass(frozen=True)
class GapRendering:
    """One gap, ready for a caseworker and for part 08's drafting step."""

    requirement_id: str
    status: str
    sentence: str
    template_data: dict[str, str]


def render_gap(
    gap: GapItem,
    requirement: Requirement | None,
    *,
    procedure_id: str | None = None,
    payload_path: str | None = None,
    template: str | None = None,
) -> GapRendering:
    """Render one gap into a sentence plus its template data."""
    description = (
        requirement.description if requirement is not None else gap.requirement_id
    )
    data = {
        "requirement_id": gap.requirement_id,
        "status": gap.status.value,
        "description": description,
        "problem": gap.detail or "",
        "procedure_id": procedure_id or "",
        "payload_path": payload_path or "",
    }
    fallback = (
        DEFAULT_INVALID if gap.status is RequirementStatus.INVALID else DEFAULT_MISSING
    )
    return GapRendering(
        requirement_id=gap.requirement_id,
        status=gap.status.value,
        sentence=_render(template or fallback, data),
        template_data=data,
    )


def render_gaps(
    completeness: CompletenessEvidence, procedure: ProcedureConfig | None
) -> list[GapRendering]:
    """Render every gap of one completeness result, in requirement order."""
    requirements = _requirements(procedure)
    paths = procedure.field_paths if procedure is not None else {}
    return [
        render_gap(
            gap,
            requirements.get(gap.requirement_id),
            procedure_id=completeness.procedure_id,
            payload_path=paths.get(gap.requirement_id),
            template=_template(procedure, gap),
        )
        for gap in completeness.gaps
    ]


def _requirements(procedure: ProcedureConfig | None) -> dict[str, Requirement]:
    if procedure is None:
        return {}
    return {
        requirement.requirement_id: requirement
        for requirement in procedure.requirements.requirements
    }


def _template(procedure: ProcedureConfig | None, gap: GapItem) -> str | None:
    if procedure is None:
        return None
    text = procedure.nachforderung_text(gap.requirement_id)
    if text is None:
        return None
    if gap.status is RequirementStatus.INVALID:
        return text.invalid or text.missing
    return text.missing


def _render(template: str, values: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders; unknown names stay literal."""
    return _PLACEHOLDER.sub(
        lambda match: values.get(match.group(1), match.group(0)), template
    )
