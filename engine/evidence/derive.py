"""Content-based procedure derivation (ADR-013).

Part 01 took the channel's ``procedure_hint`` verbatim, which left a
chicken-and-egg hole part 02 measured with ``xx-0004``: no procedure means no
``field_map``, no ``field_map`` means no extraction, and no extraction means
every content rule is dead. This module closes it deterministically, with no
model anywhere: each procedure declares in its own config which payload content
identifies it, and the signals are evaluated against the pre-extraction context
(``payload.*``, ``procedure_hint``, ``channel``).

Precedence, in order:

1. **Ambiguous content stops everything.** Two or more procedures' signals
   match: source ``none``. Not even a valid hint rescues this - if the form
   reads like two different Antraege, the honest answer is that nobody knows
   which one it is, and a hint is not evidence about content.
2. **A valid hint wins.** The channel declared a procedure this config knows
   and the content does not point somewhere else: source ``hint``.
3. **Unambiguous content wins next.** No usable hint, exactly one procedure's
   signals match: source ``content``. This is what makes ``xx-0004`` routable.
4. **Everything else is None.** No signal at all, or a valid hint that
   unambiguous content contradicts: source ``none``, which makes completeness
   NOT_EVALUABLE and therefore tier 3.

Cases 1 and 4 are the reason this function exists at all: guessing between two
procedures, or letting a channel hint overrule a form that plainly says
something else, would both be *invention*. The refusal is recorded (candidates,
ambiguity, contradiction) so the journal shows why an item was not evaluable,
and so parts 05 and 06 have a signal to work with.

Pure: a function of (envelope, procedure configs). No clock, no randomness, no
I/O. Errors push toward tier 3, never toward tier 1 - the only tier this
function can hand out is "unknown".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from engine.config_loader import ProcedureConfig
from engine.evidence.context import build_payload_context
from engine.predicate import Context
from schemas.envelope import Envelope
from schemas.evidence import DerivationOutcome
from schemas.evidence import DerivationSource as ContractDerivationSource
from schemas.textlayer import TextLayer


class DerivationSource(StrEnum):
    """Where the procedure id came from."""

    HINT = "hint"
    CONTENT = "content"
    NONE = "none"


class HintStatus(StrEnum):
    """What the channel's ``procedure_hint`` was worth."""

    ABSENT = "absent"
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcedureDerivation:
    """The derivation outcome, as evidence rather than as a decision."""

    procedure_id: str | None
    source: DerivationSource
    hint: str | None
    hint_status: HintStatus
    candidates: tuple[str, ...]
    ambiguous: bool
    hint_contradicted: bool
    detail: str

    def as_payload(self) -> dict[str, object]:
        """Journal-shaped view for the evidence_assembled event."""
        return {
            "procedure_id": self.procedure_id,
            "source": self.source.value,
            "hint": self.hint,
            "hint_status": self.hint_status.value,
            "candidates": list(self.candidates),
            "ambiguous": self.ambiguous,
            "hint_contradicted": self.hint_contradicted,
            "detail": self.detail,
        }

    def as_outcome(self) -> DerivationOutcome:
        """Contract-shaped view for ``EvidenceRecord.derivation`` (ADR-016).

        The narrower of the two views: the record carries the source, the
        candidates and the reason, while hint provenance stays journal-only.
        Both are rendered from this one dataclass, so the record and the audit
        trail cannot disagree about why a procedure was (not) identified.
        """
        return DerivationOutcome(
            source=ContractDerivationSource(self.source.value),
            candidates=list(self.candidates),
            detail=self.detail,
        )


def content_candidates(
    procedures: Mapping[str, ProcedureConfig], context: Context
) -> tuple[str, ...]:
    """Procedure ids whose content signals hold, sorted for determinism.

    A procedure without a ``derivation`` block never appears here: silence is
    not a signal, and a procedure that has not said how to recognise it must
    not be recognised by accident.
    """
    return tuple(
        sorted(
            procedure_id
            for procedure_id, procedure in procedures.items()
            if _matches(procedure, context)
        )
    )


def derive_procedure(
    envelope: Envelope,
    procedures: Mapping[str, ProcedureConfig],
    *,
    layer: TextLayer | None = None,
) -> ProcedureDerivation:
    """Identify the procedure for one envelope from hint, payload and text.

    ``layer`` adds the ``text.*`` namespace, which is what makes a free-text
    Anschreiben derivable at all: it has no structured payload, so every
    ``payload.*`` signal is silent and part 03's answer for it would have been
    "no procedure, tier 3" forever. The house rule does not change with the new
    signals - two procedures' signals firing at once is still an ambiguity and
    still resolves to "we do not know" (ADR-013, ADR-020).
    """
    context = build_payload_context(envelope, layer)
    candidates = content_candidates(procedures, context)
    hint = envelope.procedure_hint
    hint_status = _hint_status(hint, procedures)

    if len(candidates) > 1:
        return ProcedureDerivation(
            procedure_id=None,
            source=DerivationSource.NONE,
            hint=hint,
            hint_status=hint_status,
            candidates=candidates,
            ambiguous=True,
            hint_contradicted=False,
            detail=(
                f"Inhalt passt auf mehrere Verfahren ({', '.join(candidates)}); "
                f"es wird nicht geraten, das Verfahren bleibt offen"
            ),
        )

    if hint is not None and hint_status is HintStatus.KNOWN:
        if candidates and hint not in candidates:
            return ProcedureDerivation(
                procedure_id=None,
                source=DerivationSource.NONE,
                hint=hint,
                hint_status=hint_status,
                candidates=candidates,
                ambiguous=False,
                hint_contradicted=True,
                detail=(
                    f"Kanalhinweis '{hint}' widerspricht dem Inhalt "
                    f"({', '.join(candidates)}); Verfahren nicht bestimmbar, "
                    f"keine Vollstaendigkeitspruefung"
                ),
            )
        return ProcedureDerivation(
            procedure_id=hint,
            source=DerivationSource.HINT,
            hint=hint,
            hint_status=hint_status,
            candidates=candidates,
            ambiguous=False,
            hint_contradicted=False,
            detail=f"Verfahren '{hint}' aus dem Kanalhinweis uebernommen",
        )

    if len(candidates) == 1:
        derived = candidates[0]
        return ProcedureDerivation(
            procedure_id=derived,
            source=DerivationSource.CONTENT,
            hint=hint,
            hint_status=hint_status,
            candidates=candidates,
            ambiguous=False,
            hint_contradicted=False,
            detail=(
                f"Verfahren '{derived}' eindeutig aus dem Inhalt abgeleitet "
                f"({_hint_note(hint_status, hint)})"
            ),
        )

    return ProcedureDerivation(
        procedure_id=None,
        source=DerivationSource.NONE,
        hint=hint,
        hint_status=hint_status,
        candidates=candidates,
        ambiguous=False,
        hint_contradicted=False,
        detail=(
            f"Kein Verfahren bestimmbar ({_hint_note(hint_status, hint)}, "
            f"keine inhaltlichen Signale)"
        ),
    )


def _matches(procedure: ProcedureConfig, context: Context) -> bool:
    predicate = procedure.derivation_predicate
    if predicate is None:
        return False
    return predicate.evaluate(context)


def _hint_status(
    hint: str | None, procedures: Mapping[str, ProcedureConfig]
) -> HintStatus:
    if hint is None:
        return HintStatus.ABSENT
    return HintStatus.KNOWN if hint in procedures else HintStatus.UNKNOWN


def _hint_note(status: HintStatus, hint: str | None) -> str:
    if status is HintStatus.ABSENT:
        return "kein Kanalhinweis"
    return f"Kanalhinweis '{hint}' ist kein konfiguriertes Verfahren"
