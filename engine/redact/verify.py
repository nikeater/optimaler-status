"""The post-redaction second pass: prove the working copy clean.

ADR-002 asks for a second detector sweep over the redacted content that must
find nothing. This is that sweep, and it runs the precision-first VERIFY profile
(:mod:`engine.redact.recognizers`): checksum-validated Versicherungsnummern,
Steuer-IDs and IBANs, e-mail addresses, and anything imitating the reserved
placeholder syntax. Not bare dates, not bare eight-digit numbers - a Rentenbeginn
and a Betrag are legitimate payload content and a gate that shouts about them is
a gate somebody switches off.

A finding carries kind, path and match length, and NEVER the matched text. A
report that quotes what it found is itself a leak: it ends up in a log line, in
an exception message, in a test failure someone pastes into a ticket. The length
is enough to tell "a VSNR-shaped 12-character run" from "a 22-character IBAN"
when reading a report.

``Envelope.redaction_verified`` is exactly :attr:`VerificationReport.clean`. It
is computed here and asserted nowhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from engine.redact.detector import Detector, redact_detector, verify_detector
from engine.redact.placeholders import PLACEHOLDER_RE, Kind
from engine.redact.seal import walk_strings


@dataclass(frozen=True, order=True)
class Finding:
    """One piece of identity-shaped residue, described without quoting it."""

    path: str
    kind: Kind
    length: int
    recognizer_id: str

    def to_dict(self) -> dict[str, Any]:
        """Value-free rendering, safe for a journal payload or an API error."""
        return {
            "path": self.path,
            "kind": self.kind.value,
            "length": self.length,
            "recognizer_id": self.recognizer_id,
        }

    def __str__(self) -> str:
        return f"{self.kind.value} at {self.path} ({self.length} chars)"


@dataclass(frozen=True)
class VerificationReport:
    """The outcome of one sweep."""

    findings: tuple[Finding, ...] = ()
    scanned_leaves: int = 0

    @property
    def clean(self) -> bool:
        """Whether the sweep found nothing. This is ``redaction_verified``."""
        return not self.findings

    @property
    def paths(self) -> tuple[str, ...]:
        """The distinct leaf paths that carry residue, in report order."""
        seen: list[str] = []
        for finding in self.findings:
            if finding.path not in seen:
                seen.append(finding.path)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        """Value-free rendering of the whole report."""
        return {
            "clean": self.clean,
            "scanned_leaves": self.scanned_leaves,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def __str__(self) -> str:
        if self.clean:
            return f"clean ({self.scanned_leaves} leaves scanned)"
        return "; ".join(str(finding) for finding in self.findings)


def verify_payload(
    payload: Mapping[str, Any],
    *,
    detector: Detector | None = None,
    prefix: str = "",
) -> VerificationReport:
    """Sweep every string leaf of a structured payload."""
    scanner = detector if detector is not None else verify_detector()
    findings: list[Finding] = []
    scanned = 0
    for path, value in walk_strings(payload, prefix):
        scanned += 1
        findings.extend(_findings_in(path, value, scanner))
    return VerificationReport(findings=tuple(findings), scanned_leaves=scanned)


def verify_texts(
    texts: Mapping[str, str], *, detector: Detector | None = None
) -> VerificationReport:
    """Sweep a mapping of ``label -> text``.

    The symmetric entry point for free text: part 05's ``redacted_text`` goes
    through here so a text part cannot reach the model plane on a weaker check
    than a structured one.
    """
    scanner = detector if detector is not None else verify_detector()
    findings: list[Finding] = []
    for label, value in texts.items():
        findings.extend(_findings_in(label, value, scanner))
    return VerificationReport(findings=tuple(findings), scanned_leaves=len(texts))


def sweep_texts(
    texts: Mapping[str, str], *, detector: Detector | None = None
) -> VerificationReport:
    """The free-text sweep: the sealing union AND the precision-first one.

    Two unions, because they answer two different questions and neither one
    subsumes the other:

    * the REDACT union (``detector``, the same one that decided what to seal)
      answers "is there identity-shaped text left in this prose" - addresses,
      phone numbers, bare dates behind a Geburtsdatum label. This is the one
      part 04's findings named for free text: the VERIFY profile is deliberately
      narrow because it sweeps structured leaves, and using it on prose would
      leave an address in a sentence unremarked.
    * the VERIFY union answers "is something imitating the reserved placeholder
      syntax", which the REDACT profile does not ask at all. A forged
      ``[[PII|...]]`` in an inbound letter has to be residue, or part 08's
      re-hydrator would meet a token it never minted.

    Findings are deduplicated, so a hit both unions agree on is reported once.

    The default for ``detector`` is the DETERMINISTIC redact union, not the
    verify one: a caller that does not name a union must get the recall-first
    sweep this function's whole docstring describes, and defaulting to the
    narrow profile would have made the second paragraph above quietly false.

    **The recall sweep runs over the MASKED text** (:func:`mask_placeholders`).
    A recognizer that fires on a placeholder has found the redaction, not
    residue, and reporting it would make the boundary refuse its own output.
    That is not hypothetical: the canary suite caught the model member tagging
    a random token run as a PERSON and an ORGANIZATION, which no re-sealing
    round can ever clean, so every letter would have been refused at a rate set
    by which twelve characters the token source happened to draw. The
    placeholder sweep still reads the UNMASKED text, because a forged token is
    exactly what it is looking for.
    """
    sealing = verify_texts(
        {label: mask_placeholders(text) for label, text in texts.items()},
        detector=detector or redact_detector(with_ner=False),
    )
    placeholders = verify_texts(texts, detector=verify_detector())
    merged = merge_reports(sealing, placeholders)
    # Both unions saw the same texts, so the honest leaf count is how many there
    # were, not how many scans happened.
    return VerificationReport(findings=merged.findings, scanned_leaves=len(texts))


def mask_placeholders(text: str) -> str:
    """Blank every well-formed placeholder, keeping the string LENGTH.

    Same length on purpose: the finding lengths a report prints stay meaningful
    for the residue that really is residue, and masking cannot pull two distant
    fragments together into something that looks like an identifier. Spaces are
    the filler because no recognizer in the union matches whitespace, so the
    masked run cannot become a hit of its own.
    """
    return PLACEHOLDER_RE.sub(lambda match: " " * len(match.group(0)), text)


def merge_reports(*reports: VerificationReport) -> VerificationReport:
    """One report carrying every distinct finding of the inputs, sorted.

    Sorted rather than concatenated, because a merged report is read by a human
    debugging a refusal and "every finding for one path together" is what makes
    that readable. ``Finding`` is ordered on (path, kind, length, recognizer),
    so the order is a pure function of the set and never of which sweep ran
    first.
    """
    findings: list[Finding] = []
    for report in reports:
        findings.extend(
            finding for finding in report.findings if finding not in findings
        )
    return VerificationReport(
        findings=tuple(sorted(findings)),
        scanned_leaves=sum(report.scanned_leaves for report in reports),
    )


def _findings_in(path: str, value: str, detector: Detector) -> list[Finding]:
    return [
        Finding(
            path=path,
            kind=hit.kind,
            length=hit.length,
            recognizer_id=hit.recognizer_id,
        )
        for hit in detector.scan(value)
    ]
