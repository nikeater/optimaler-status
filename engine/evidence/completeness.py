"""Completeness: requirement list against extracted fields.

Three verdicts, and the third one matters most: an unknown procedure is
``NOT_EVALUABLE``, not ``COMPLETE``. "We could not check" must never look like
"we checked and it was fine", which is why the decision table qualifies tier 1
on ``verdict == complete`` rather than on the absence of gaps.

Gaps are the raw material for the applicant notification later, so each one
names the requirement, says which constraint it failed, and carries the payload
path the value was read from. Text spans arrive with part 05; until then the
provenance a caseworker gets is "this key of the submission", which is the
truth for a structured payload.

Constraints, all declarative in ``config/procedures/*.yaml`` and all checked at
load time against ``engine.config_loader.SUPPORTED_VALIDATION_KEYS``:

    pattern       regular expression the value must match
    one_of        closed value list
    min_length    minimum text length
    max_length    maximum text length
    date          real calendar date, optionally within absolute bounds
    cross_field   coherence with another requirement's value

The date bounds are absolute dates from config, never "today" plus an offset:
this module is a pure function of (extractions, requirements), and a validator
that reads the wall clock would make yesterday's gold set fail tomorrow.

Part 04 added two things, both of which follow from the redaction boundary
(ADR-017):

**Witness resolution.** An extracted value that is EXACTLY one placeholder is
resolved through the request-scoped witness before validation, so a
Versicherungsnummer is still checked against its Pruefziffer pattern and against
the birth date it encodes. A placeholder the witness cannot resolve is INVALID
with a value-free detail - never silently valid, and never an exception: the
defensive direction is toward tier 3.

**Value visibility.** Problem strings for identity-classed fields never quote
the observed value, and a cross-field message hides the sealed operand while it
may still name the open one. Bounds, pattern texts and constraint names are
still shown, because they describe the RULE and not the person. Without this the
redaction boundary would leak straight back out through ``GapItem.detail``, the
EVIDENCE_ASSEMBLED journal payload and the Nachforderung text - which is exactly
where an invalid Versicherungsnummer used to land verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from engine.redact import Witness, parse_placeholder
from schemas.config import Requirement, RequirementList
from schemas.evidence import (
    CompletenessEvidence,
    CompletenessVerdict,
    GapItem,
    RequirementStatus,
)
from schemas.extraction import ExtractionRecord, ExtractionSet

UNKNOWN_REQUIREMENTS_VERSION = "unknown"

#: Where the DRV Versicherungsnummer carries the date of birth (DDMMJJ).
VSNR_BIRTHDATE_SLICE = slice(2, 8)

#: What a gap says when a sealed value could not be resolved for validation.
#: INVALID rather than MISSING: the applicant DID answer, and the system could
#: not check it, which is a reason for a human to look and never a reason to
#: clear the item.
UNRESOLVED_DETAIL = (
    "Der Wert dieses Feldes ist versiegelt und konnte fuer die Pruefung nicht "
    "aufgeloest werden"
)
UNRESOLVED_CONSTRAINT = "sealed.unresolved"


@dataclass(frozen=True)
class ValidationFailure:
    """Why a value was rejected, in a form a gap can carry."""

    constraint: str
    message: str


@dataclass(frozen=True)
class Visibility:
    """Whose observed value a problem string may quote.

    Built from the same policy row that drives sealing (``ConfigBundle
    .sealed_field_ids``), so a field cannot be sealed in one place and quoted in
    another.
    """

    sealed_fields: frozenset[str] = frozenset()

    def hides(self, field_id: str | None) -> bool:
        """Whether ``field_id``'s observed value must stay out of the text."""
        return field_id is not None and field_id in self.sealed_fields


OPEN = Visibility()


def evaluate_completeness(
    extractions: ExtractionSet,
    requirements: RequirementList | None,
    *,
    procedure_id: str | None = None,
    field_paths: Mapping[str, str] | None = None,
    witness: Witness | None = None,
    sealed_fields: Collection[str] = (),
) -> CompletenessEvidence:
    """Check one item against its procedure's requirement list.

    Args:
        extractions: the values that were read for this item.
        requirements: the procedure's requirement list, or None when the
            procedure is unknown (which is NOT_EVALUABLE, never COMPLETE).
        procedure_id: recorded on the evidence when there is no requirement
            list to take it from.
        field_paths: field id -> payload path, used as gap provenance.
        witness: request-scoped resolution for sealed values. Absent means
            sealed fields validate as unresolvable, which is defensive by
            construction.
        sealed_fields: requirement ids whose observed value may never appear in
            a problem string.
    """
    if requirements is None:
        return CompletenessEvidence(
            procedure_id=procedure_id,
            verdict=CompletenessVerdict.NOT_EVALUABLE,
            gaps=[],
            requirements_version=UNKNOWN_REQUIREMENTS_VERSION,
        )
    by_field: dict[str, ExtractionRecord] = {
        record.field: record for record in extractions.records
    }
    visibility = Visibility(frozenset(sealed_fields))
    values = {
        field: resolved
        for field, record in by_field.items()
        if (resolved := _resolve(record.value, witness)) is not None
    }
    paths = dict(field_paths or {})
    gaps = [
        gap
        for gap in (
            _check_requirement(
                requirement,
                by_field.get(requirement.requirement_id),
                values,
                paths,
                witness,
                visibility,
            )
            for requirement in requirements.requirements
        )
        if gap is not None
    ]
    verdict = CompletenessVerdict.INCOMPLETE if gaps else CompletenessVerdict.COMPLETE
    return CompletenessEvidence(
        procedure_id=requirements.procedure_id,
        verdict=verdict,
        gaps=gaps,
        requirements_version=requirements.version,
    )


def _resolve(value: str, witness: Witness | None) -> str | None:
    """The value a validator should see, or None when it cannot be resolved.

    A value that is not a placeholder is itself. A value that is EXACTLY one
    placeholder resolves through the witness. Anything else - a placeholder the
    witness does not know - is unresolvable, and the caller decides what that
    means (here: an INVALID gap with a value-free detail).
    """
    if parse_placeholder(value) is None:
        return value
    if witness is None:
        return None
    return witness.resolve(value)


def _check_requirement(
    requirement: Requirement,
    record: ExtractionRecord | None,
    values: Mapping[str, str],
    paths: Mapping[str, str],
    witness: Witness | None,
    visibility: Visibility,
) -> GapItem | None:
    provenance = _provenance(requirement.requirement_id, paths)
    if requirement.kind == "document":
        # No document evidence yet; attachments are classified in a later part.
        return GapItem(
            requirement_id=requirement.requirement_id,
            status=RequirementStatus.MISSING,
            span=None,
            detail=(
                f"Dokumentenpruefung ist noch nicht implementiert: "
                f"{requirement.description} {provenance}"
            ),
        )
    if record is None or not record.value.strip():
        return GapItem(
            requirement_id=requirement.requirement_id,
            status=RequirementStatus.MISSING,
            span=None,
            detail=f"Pflichtangabe fehlt: {requirement.description} {provenance}",
        )
    value = _resolve(record.value, witness)
    if value is None:
        # A sealed value nobody can resolve is a checking failure, not a clean
        # bill of health. Toward tier 3, always.
        return GapItem(
            requirement_id=requirement.requirement_id,
            status=RequirementStatus.INVALID,
            span=record.span,
            detail=(
                f"{UNRESOLVED_DETAIL} {provenance}, Pruefregel: {UNRESOLVED_CONSTRAINT}"
            ),
        )
    failure = validation_problem(
        requirement.validation,
        value,
        values,
        visibility=visibility,
        field_id=requirement.requirement_id,
    )
    if failure is not None:
        return GapItem(
            requirement_id=requirement.requirement_id,
            status=RequirementStatus.INVALID,
            span=record.span,
            detail=f"{failure.message} {provenance}, Pruefregel: {failure.constraint}",
        )
    return None


def _provenance(requirement_id: str, paths: Mapping[str, str]) -> str:
    path = paths.get(requirement_id)
    location = f", Pfad: {path}" if path else ""
    return f"(Feld: {requirement_id}{location})"


def validation_problem(
    validation: dict[str, Any] | None,
    value: str,
    values: Mapping[str, str] | None = None,
    *,
    visibility: Visibility = OPEN,
    field_id: str | None = None,
) -> ValidationFailure | None:
    """Return the first failed constraint, or None when the value is acceptable.

    Supported constraints are declared in
    ``engine.config_loader.SUPPORTED_VALIDATION_KEYS`` and checked at load time,
    so an unknown key can never silently pass here.

    ``visibility``/``field_id`` decide whether the message may quote the
    observed value. The default is the open one, which is what a caller without
    a redaction policy (a unit test, a bare helper call) gets.
    """
    if not validation:
        return None
    hidden = visibility.hides(field_id)
    for check in (
        _check_pattern,
        _check_one_of,
        _check_min_length,
        _check_max_length,
        _check_date,
    ):
        failure = check(validation, value, hidden)
        if failure is not None:
            return failure
    return _check_cross_field(validation, value, values or {}, hidden, visibility)


def _quoted(value: str, hidden: bool) -> str:
    """``Wert '<value>'`` or a value-free stand-in."""
    return "Der Wert" if hidden else f"Wert '{value}'"


def _check_pattern(
    validation: dict[str, Any], value: str, hidden: bool
) -> ValidationFailure | None:
    pattern = validation.get("pattern")
    if isinstance(pattern, str) and re.match(pattern, value) is None:
        # The pattern itself is the RULE and stays visible: it tells the
        # caseworker what was expected without saying what arrived.
        return ValidationFailure(
            "pattern", f"{_quoted(value, hidden)} entspricht nicht dem Format {pattern}"
        )
    return None


def _check_one_of(
    validation: dict[str, Any], value: str, hidden: bool
) -> ValidationFailure | None:
    allowed = validation.get("one_of")
    if isinstance(allowed, list) and value not in allowed:
        return ValidationFailure(
            "one_of",
            f"{_quoted(value, hidden)} ist nicht zulaessig; erlaubt: {allowed}",
        )
    return None


def _check_min_length(
    validation: dict[str, Any], value: str, hidden: bool
) -> ValidationFailure | None:
    minimum = validation.get("min_length")
    if isinstance(minimum, int) and len(value) < minimum:
        return ValidationFailure(
            "min_length", f"{_quoted(value, hidden)} ist kuerzer als {minimum} Zeichen"
        )
    return None


def _check_max_length(
    validation: dict[str, Any], value: str, hidden: bool
) -> ValidationFailure | None:
    maximum = validation.get("max_length")
    if isinstance(maximum, int) and len(value) > maximum:
        return ValidationFailure(
            "max_length", f"{_quoted(value, hidden)} ist laenger als {maximum} Zeichen"
        )
    return None


def _check_date(
    validation: dict[str, Any], value: str, hidden: bool
) -> ValidationFailure | None:
    """Real calendar date within absolute, config-declared bounds."""
    block = validation.get("date")
    if not isinstance(block, dict):
        return None
    parsed = parse_iso_date(value)
    if parsed is None:
        return ValidationFailure(
            "date",
            f"{_quoted(value, hidden)} ist kein gueltiges Kalenderdatum (ISO 8601)",
        )
    minimum = _bound(block.get("min"))
    if minimum is not None and parsed < minimum:
        return ValidationFailure(
            "date.min",
            f"{_dated(value, hidden)} liegt vor der Untergrenze {minimum.isoformat()}",
        )
    maximum = _bound(block.get("max"))
    if maximum is not None and parsed > maximum:
        return ValidationFailure(
            "date.max",
            f"{_dated(value, hidden)} liegt nach der Obergrenze {maximum.isoformat()}",
        )
    return None


def _dated(value: str, hidden: bool) -> str:
    """``Datum <value>`` or a value-free stand-in."""
    return "Das Datum" if hidden else f"Datum {value}"


def _check_cross_field(
    validation: dict[str, Any],
    value: str,
    values: Mapping[str, str],
    hidden: bool,
    visibility: Visibility,
) -> ValidationFailure | None:
    """Coherence with another requirement's value.

    A comparison is skipped when the other value is absent or itself unusable:
    that field already has (or will have) its own gap, and reporting the same
    problem twice would send a caseworker after a consequence instead of a
    cause. ``birthdate_in_vsnr`` is the exception, because half of it is a
    structural check on this value alone.

    Both operands get their own visibility answer: ``min_years_after`` on the
    Rentenbeginn may name the Rentenbeginn (open) and must not name the
    Geburtsdatum (sealed).
    """
    checks = validation.get("cross_field")
    if not isinstance(checks, list):
        return None
    for entry in checks:
        if not isinstance(entry, dict):
            continue
        other_id = str(entry.get("field", ""))
        other_value = values.get(other_id, "").strip()
        other_hidden = visibility.hides(other_id)
        if str(entry.get("kind", "")) == "birthdate_in_vsnr":
            failure = _check_birthdate_in_vsnr(
                value, other_value, other_id, entry.get("detail"), hidden, other_hidden
            )
        elif other_value:
            failure = _cross_field_failure(
                entry, value, other_value, other_id, hidden, other_hidden
            )
        else:
            failure = None
        if failure is not None:
            return failure
    return None


def _cross_field_failure(
    entry: Mapping[str, Any],
    value: str,
    other_value: str,
    other_id: str,
    hidden: bool,
    other_hidden: bool,
) -> ValidationFailure | None:
    kind = str(entry.get("kind", ""))
    detail = entry.get("detail")
    this_date = parse_iso_date(value)
    other_date = parse_iso_date(other_value)
    if this_date is None or other_date is None:
        return None
    constraint = f"cross_field.{kind}"
    other = _named(other_id, other_value, other_hidden)
    if kind == "not_before" and this_date < other_date:
        return ValidationFailure(
            constraint,
            _explain(detail, f"{_dated(value, hidden)} liegt vor {other}"),
        )
    if kind == "not_after" and this_date > other_date:
        return ValidationFailure(
            constraint,
            _explain(detail, f"{_dated(value, hidden)} liegt nach {other}"),
        )
    years = entry.get("years")
    if not isinstance(years, int):
        return None
    this = value if not hidden else "diesem Datum"
    if kind == "min_years_after" and _years_between(other_date, this_date) < years:
        return ValidationFailure(
            constraint,
            _explain(
                detail,
                f"Zwischen {other} und {this} liegen weniger als {years} Jahre",
            ),
        )
    if kind == "max_years_after" and _years_between(other_date, this_date) > years:
        return ValidationFailure(
            constraint,
            _explain(
                detail,
                f"Zwischen {other} und {this} liegen mehr als {years} Jahre",
            ),
        )
    return None


def _named(other_id: str, other_value: str, hidden: bool) -> str:
    """``<field> (<value>)`` or just the field name when the value is sealed."""
    return other_id if hidden else f"{other_id} ({other_value})"


def _check_birthdate_in_vsnr(
    vsnr: str,
    birthdate: str,
    other_id: str,
    detail: object,
    hidden: bool,
    other_hidden: bool,
) -> ValidationFailure | None:
    """The Versicherungsnummer carries the date of birth in positions 3 to 8.

    Two checks in one, which is why it runs even without the other value:

    * structural - the DDMMJJ block must be a real calendar date, so a
      Versicherungsnummer that merely has the right *number* of digits is not
      mistaken for a valid one;
    * coherence - it must agree with the stated date of birth when that value
      is present and usable.

    A mismatch is a hard contradiction between two values that are each
    format-valid on their own, which is exactly the class of error a
    deterministic checker should catch instead of leaving to a scorer. Both
    operands are identity-classed in the shipped policy, so in practice both
    branches below run value-free; the visibility flags are still read per
    operand, because an agency that declassifies one of them should get the
    more useful message back without a code change.
    """
    encoded = _vsnr_birthdate(vsnr)
    if encoded is None:
        # The configured detail describes the MISMATCH; using it here would
        # tell an applicant their two dates disagree when the real problem is
        # that one of them is not a date.
        subject = (
            "der Versicherungsnummer" if hidden else f"der Versicherungsnummer '{vsnr}'"
        )
        return ValidationFailure(
            "cross_field.birthdate_in_vsnr",
            f"Die Stellen 3 bis 8 {subject} ergeben kein gueltiges Geburtsdatum",
        )
    stated = parse_iso_date(birthdate)
    if stated is None:
        return None
    if (encoded.day, encoded.month, encoded.year % 100) != (
        stated.day,
        stated.month,
        stated.year % 100,
    ):
        return ValidationFailure(
            "cross_field.birthdate_in_vsnr",
            _explain(
                detail,
                _mismatch_text(vsnr, encoded, stated, other_id, hidden, other_hidden),
            ),
        )
    return None


def _mismatch_text(
    vsnr: str,
    encoded: date,
    stated: date,
    other_id: str,
    hidden: bool,
    other_hidden: bool,
) -> str:
    if hidden or other_hidden:
        # Naming either date would reconstruct the other one from the fact that
        # they disagree, so the value-free variant hides both.
        return (
            f"Die Versicherungsnummer kodiert ein anderes Geburtsdatum als das "
            f"unter {other_id} angegebene"
        )
    return (
        f"Versicherungsnummer '{vsnr}' kodiert den "
        f"{encoded.strftime('%d.%m.')}{encoded.year % 100:02d}, angegeben "
        f"ist als {other_id} der {stated.strftime('%d.%m.%Y')}"
    )


def _vsnr_birthdate(vsnr: str) -> date | None:
    """The DDMMJJ block of a Versicherungsnummer as a date, or None.

    The two-digit year is read into 1900+YY: the century is not encoded, and
    guessing 2000 for a pension applicant would invent information.
    """
    digits = vsnr[VSNR_BIRTHDATE_SLICE]
    if len(digits) != 6 or not digits.isdigit():
        return None
    day, month, year = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    try:
        return date(1900 + year, month, day)
    except ValueError:
        return None


def parse_iso_date(value: str) -> date | None:
    """Parse an ISO date, or None. Never raises."""
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _bound(value: object) -> date | None:
    return parse_iso_date(value) if isinstance(value, str) else None


def _years_between(start: date, end: date) -> int:
    """Full years from ``start`` to ``end``; negative when end precedes start."""
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return years


def _explain(detail: object, fallback: str) -> str:
    return str(detail) if isinstance(detail, str) and detail else fallback
