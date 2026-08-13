"""The privacy boundary itself: seal, verify, seal again, or refuse.

One function, :func:`redact_payload`, and the sequence it enforces:

1. mint an opaque vault handle (not derivable from the submission id),
2. seal every identity-classed path the policy declares, and every identity SPAN
   the detector union finds in a free-text part,
3. sweep the working copy - structured leaves with the precision-first VERIFY
   profile, prose with the recall-first union that sealed it plus the
   placeholder check (:func:`engine.redact.verify.sweep_texts`),
4. if the sweep found residue at a path the policy does not cover - a
   Versicherungsnummer typed into a free-text field, say - **auto-seal that leaf
   as TEXT, re-seal the offending prose, and sweep once more**,
5. if it is still dirty, **refuse the submission** with
   :class:`RedactionRefusedError` before a single journal event is written.

Step 4 is the defensive direction: when in doubt, seal MORE. Step 5 is the other
half of the same posture: never forward content the boundary could not verify,
and never leave half a case in the journal. The error carries kinds, paths and
lengths, never the text it found, so mapping it onto an HTTP 422 cannot leak the
thing that triggered it.

Exactly one re-verification round. A loop would either terminate on the same
condition or hide a recognizer that fires on its own placeholder output; one
round makes "the sweep could not clean this" an explicit, reportable state.

**Text is sealed HERE, before the text layer exists** (ADR-019). The normalized
layer of part 05 is built over ``redacted_text``, so every offset the system ever
computes lives in redacted coordinates and the raw letter never re-enters the
model path. Doing it the other way round - normalize, then seal - would put a
raw letter through a transformation nobody audited and leave every span pointing
at text that still had a name in it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from engine.redact.detector import Detector, verify_detector
from engine.redact.placeholders import (
    Kind,
    PlaceholderRegistry,
    SecretsTokenSource,
    TokenSource,
)
from engine.redact.policy import IdentityFieldsPolicy
from engine.redact.seal import Witness, seal_leaf, seal_payload
from engine.redact.text import seal_text, seal_texts, text_seal_detector
from engine.redact.vault import SealedEntry, VaultRecord, build_record
from engine.redact.verify import (
    VerificationReport,
    merge_reports,
    sweep_texts,
    verify_payload,
)

#: Kind an auto-sealed leaf gets. Not the kind of the thing that was found: the
#: sweep seals the WHOLE leaf, and a free-text field that happened to contain a
#: Versicherungsnummer is text, not a Versicherungsnummer.
AUTOSEAL_KIND = Kind.TEXT

#: Prefix a free-text part carries in a verification finding, so a report can
#: never confuse "the residue is in the prose of part-text-0" with "the residue
#: is at the payload path text".
TEXT_PATH_PREFIX = "text:"


class RedactionRefusedError(RuntimeError):
    """The working copy could not be verified clean; the item is refused.

    Carries the value-free findings so an API layer can say WHAT kind of residue
    survived and WHERE, without ever quoting it. ``str()`` and ``repr()`` are
    both value-free, because an exception is the single most likely object to
    end up in a log line nobody audited.
    """

    def __init__(self, report: VerificationReport) -> None:
        self.report = report
        super().__init__(
            "redaction verification failed after auto-sealing: "
            + "; ".join(str(finding) for finding in report.findings)
        )

    def __repr__(self) -> str:
        return f"RedactionRefusedError({len(self.report.findings)} findings)"

    def as_payload(self) -> dict[str, Any]:
        """Value-free rendering for an error response."""
        return {
            "error": "redaction_unverified",
            "findings": [finding.to_dict() for finding in self.report.findings],
        }


@dataclass(frozen=True)
class RedactionOutcome:
    """Everything the boundary produced for one submission."""

    payload: dict[str, Any]
    record: VaultRecord
    witness: Witness
    report: VerificationReport
    auto_sealed_paths: tuple[str, ...] = ()
    texts: dict[str, str] = field(default_factory=dict)
    text_sealed_counts: dict[str, int] = field(default_factory=dict)

    @property
    def vault_ref(self) -> str:
        """The opaque handle the envelope carries."""
        return self.record.vault_ref

    @property
    def sealed_count(self) -> int:
        """How many values left the working copy, auto-sealed ones included."""
        return len(self.record.entries)

    @property
    def text_sealed_count(self) -> int:
        """How many spans were sealed out of free text."""
        return sum(self.text_sealed_counts.values())

    @property
    def verified(self) -> bool:
        """Exactly what ``Envelope.redaction_verified`` is set from."""
        return self.report.clean

    def summary(self) -> dict[str, Any]:
        """Value-free metadata for the RECEIVED journal payload."""
        return {
            "vault_ref": self.vault_ref,
            "sealed_count": self.sealed_count,
            "auto_sealed_paths": list(self.auto_sealed_paths),
            "redaction_verified": self.verified,
            "text_sealed_counts": dict(sorted(self.text_sealed_counts.items())),
        }


def redact_payload(
    payload: Mapping[str, Any],
    *,
    policy: IdentityFieldsPolicy,
    case_id: str,
    created_at: datetime,
    token_source: TokenSource | None = None,
    detector: Detector | None = None,
    texts: Mapping[str, str] | None = None,
    text_detector: Detector | None = None,
) -> RedactionOutcome:
    """Seal, verify, auto-seal once, verify again, or refuse.

    Args:
        payload: the structured submission data, as received.
        texts: free-text parts as ``part_id -> raw text``. Sealed span by span
            with ``text_detector``; the working copies come back on
            :attr:`RedactionOutcome.texts` and are what the envelope carries.
        text_detector: union that decides what to seal in prose. Defaults to the
            DETERMINISTIC REDACT union (no NER), because every gate path runs
            through here; production passes one built with the model member.

    Raises:
        RedactionRefusedError: when identity-shaped residue survives the second
            sweep. Raised BEFORE any journal event exists, so a refused
            submission leaves no half-ingested case behind.
    """
    raw_texts = dict(texts or {})
    registry = PlaceholderRegistry(
        token_source if token_source is not None else SecretsTokenSource(),
        # Everything the case says, in one string: a token that literally occurs
        # in the submission is re-drawn, and prose is source content too.
        reserved=json.dumps(payload, ensure_ascii=False, default=str)
        + "".join(raw_texts.values()),
    )
    vault_ref = registry.vault_ref()
    outcome = seal_payload(payload, policy=policy, registry=registry)
    working = outcome.payload
    entries: list[SealedEntry] = list(outcome.entries)
    witness: dict[str, str] = {}
    sweeper = detector if detector is not None else verify_detector()
    text_sweeper = text_detector if text_detector is not None else text_seal_detector()

    sealed_texts, text_entries = seal_texts(
        raw_texts, registry=registry, detector=text_sweeper, witness=witness
    )
    entries.extend(text_entries)
    text_counts = {
        label: sum(1 for entry in text_entries if entry.part_id == label)
        for label in sealed_texts
    }

    report = _sweep(working, sealed_texts, sweeper, text_sweeper)
    auto_sealed: tuple[str, ...] = ()
    if not report.clean:
        auto_sealed = _auto_seal(working, report, registry, entries, witness)
        _reseal_texts(
            sealed_texts,
            report,
            registry=registry,
            detector=text_sweeper,
            entries=entries,
            witness=witness,
            counts=text_counts,
        )
        report = _sweep(working, sealed_texts, sweeper, text_sweeper)
        if not report.clean:
            raise RedactionRefusedError(report)

    merged = outcome.witness.merged(Witness(witness))
    record = build_record(
        vault_ref=vault_ref,
        case_id=case_id,
        created_at=created_at,
        entries=entries,
    )
    return RedactionOutcome(
        payload=working,
        record=record,
        witness=merged,
        report=report,
        auto_sealed_paths=auto_sealed,
        texts=sealed_texts,
        text_sealed_counts=text_counts,
    )


def _sweep(
    working: Mapping[str, Any],
    texts: Mapping[str, str],
    sweeper: Detector,
    text_sweeper: Detector,
) -> VerificationReport:
    """One report over the structured leaves AND the prose."""
    structured = verify_payload(working, detector=sweeper)
    if not texts:
        return structured
    prose = sweep_texts(
        {f"{TEXT_PATH_PREFIX}{label}": text for label, text in texts.items()},
        detector=text_sweeper,
    )
    return merge_reports(structured, prose)


def _auto_seal(
    working: dict[str, Any],
    report: VerificationReport,
    registry: PlaceholderRegistry,
    entries: list[SealedEntry],
    witness: dict[str, str],
) -> tuple[str, ...]:
    """Seal every leaf the sweep complained about; return the paths handled."""
    sealed: list[str] = []
    for path in report.paths:
        if path.startswith(TEXT_PATH_PREFIX):
            continue  # prose is re-sealed span by span, never as a whole leaf
        entry = seal_leaf(
            working, path, kind=AUTOSEAL_KIND, registry=registry, witness=witness
        )
        if entry is not None:
            entries.append(entry)
            sealed.append(path)
    return tuple(sealed)


def _reseal_texts(
    texts: dict[str, str],
    report: VerificationReport,
    *,
    registry: PlaceholderRegistry,
    detector: Detector,
    entries: list[SealedEntry],
    witness: dict[str, str],
    counts: dict[str, int],
) -> None:
    """Run the sealer once more over any prose the sweep still complained about.

    Deliberately NOT "seal the whole part as one placeholder": a letter reduced
    to a single token is not a working copy, and the second round exists for the
    narrow case where substitution changed the text enough for a recognizer to
    see something new at a seam. If the second pass still leaves residue, the
    caller refuses the submission - which is the same posture as the structured
    path, one round and then an explicit, reportable state.
    """
    for path in report.paths:
        if not path.startswith(TEXT_PATH_PREFIX):
            continue
        label = path[len(TEXT_PATH_PREFIX) :]
        if label not in texts:  # pragma: no cover - defensive
            continue
        sealed = seal_text(
            texts[label],
            label=label,
            registry=registry,
            detector=detector,
            witness=witness,
        )
        texts[label] = sealed.text
        entries.extend(sealed.entries)
        counts[label] = counts.get(label, 0) + sealed.sealed_count
