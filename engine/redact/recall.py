"""P-7: measure the redactor's recall, not just its canaries.

Canary tests answer "did THIS fake identity survive". They cannot answer "what
share of German identifiers does the union find at all", and that second
question is the one that decides whether the boundary works on material nobody
wrote a fixture for. The prior-art pass filed it as P-7 after the Presidio
documentation's own warning: recall is not guaranteed, the defaults are
English-tuned, and missing custom recognizers are the number-one reason real PII
slips through.

So there is a seeded, labelled German-PII set (``corpus/pii_golden``) and this
module turns it into a CI metric.

**Recall is measured by containment, not by overlap.** A label counts as found
only when some detection of the same kind covers it entirely. A detection that
catches nine digits of an eleven-digit Steuer-ID would score as a hit under an
overlap rule while leaving two digits and the shape of the number in the
working copy, which is not redaction.

**Precision is reported, never gated.** Over-redaction costs utility; under-
redaction costs a person's data. A gate on precision would create pressure in
exactly the wrong direction, so false positives are inventoried in the output
and left for a human to judge.

**The gate splits by what produced the number.** The deterministic recognizers
must find every label of their kinds with or without the optional extra. NAME is
gated only when the extra is installed, because a bare German person name in
prose has no grammar to match - which is precisely why P-7 asks for a union
rather than for more regular expressions.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from engine.redact.detector import Detector, redact_detector
from engine.redact.ner import available as ner_available
from engine.redact.placeholders import Kind
from engine.redact.recognizers import Detection

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PII_GOLDEN = REPO_ROOT / "corpus" / "pii_golden" / "items.yaml"

#: Kinds the deterministic union must find in full, extra installed or not.
DETERMINISTIC_GATE_KINDS = frozenset(
    {
        Kind.VSNR,
        Kind.STID,
        Kind.IBAN,
        Kind.BNR,
        Kind.AKTZ,
        Kind.EMAIL,
        Kind.TEL,
        Kind.ADDR,
        Kind.GEBDAT,
        Kind.ORG,
    }
)

#: Kinds that need the model-backed member of the union. One entry, and it is
#: the whole argument for P-7's "union" instead of "one more regex".
NER_GATE_KINDS = frozenset({Kind.NAME})


@dataclass(frozen=True, order=True)
class Label:
    """One labelled PII span in a golden snippet."""

    start: int
    end: int
    kind: Kind

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Label:
        return cls(
            start=int(document["start"]),
            end=int(document["end"]),
            kind=Kind(document["kind"]),
        )


@dataclass(frozen=True)
class LabelledText:
    """One golden snippet: German administrative prose plus its ground truth."""

    item_id: str
    scenario: str
    text: str
    labels: tuple[Label, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "scenario": self.scenario,
            "text": self.text,
            "labels": [label.to_dict() for label in sorted(self.labels)],
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> LabelledText:
        return cls(
            item_id=str(document["item_id"]),
            scenario=str(document["scenario"]),
            text=str(document["text"]),
            labels=tuple(
                Label.from_dict(label) for label in document.get("labels", [])
            ),
        )


@dataclass(frozen=True)
class KindMetrics:
    """Recall and precision for one kind."""

    kind: Kind
    label_count: int
    found_count: int
    detection_count: int
    true_positive_count: int

    @property
    def recall(self) -> float:
        """Share of labels a detection of the same kind fully covered."""
        if self.label_count == 0:
            return 1.0
        return self.found_count / self.label_count

    @property
    def precision(self) -> float:
        """Share of detections that hit a label of the same kind."""
        if self.detection_count == 0:
            return 1.0
        return self.true_positive_count / self.detection_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": self.label_count,
            "found": self.found_count,
            "detections": self.detection_count,
            "true_positives": self.true_positive_count,
            "recall": round(self.recall, 4),
            "precision": round(self.precision, 4),
        }


@dataclass(frozen=True)
class RecallReport:
    """What the union found on the golden set, per kind and overall."""

    item_count: int
    by_kind: dict[Kind, KindMetrics]
    misses: tuple[tuple[str, Label], ...] = ()
    false_positives: tuple[tuple[str, Detection], ...] = ()
    ner_installed: bool = False
    detector_inventory: dict[str, Any] | None = None

    @property
    def deterministic_recall(self) -> float:
        """Micro recall over the kinds the deterministic union owns."""
        return self._micro_recall(DETERMINISTIC_GATE_KINDS)

    @property
    def overall_recall(self) -> float:
        """Micro recall over every labelled kind."""
        return self._micro_recall(set(self.by_kind))

    @property
    def deterministic_gate_passed(self) -> bool:
        """The CI gate: every deterministic kind at recall 1.000."""
        return all(
            metrics.recall == 1.0
            for kind, metrics in self.by_kind.items()
            if kind in DETERMINISTIC_GATE_KINDS
        )

    @property
    def ner_gate_passed(self) -> bool:
        """The gate that only applies with the extra installed."""
        if not self.ner_installed:
            return True
        return all(
            metrics.recall == 1.0
            for kind, metrics in self.by_kind.items()
            if kind in NER_GATE_KINDS
        )

    def _micro_recall(self, kinds: Iterable[Kind]) -> float:
        selected = [self.by_kind[kind] for kind in kinds if kind in self.by_kind]
        labels = sum(metrics.label_count for metrics in selected)
        found = sum(metrics.found_count for metrics in selected)
        return 1.0 if labels == 0 else found / labels

    def to_dict(self) -> dict[str, Any]:
        """The ``redaction`` section of the eval report. Value-free.

        Misses and false positives are reported as kind, item id and length -
        never as the text that was or was not found. The golden set is synthetic,
        but the report format is the one a pilot would run against real intake.
        """
        return {
            "item_count": self.item_count,
            "ner_installed": self.ner_installed,
            "deterministic_recall": round(self.deterministic_recall, 4),
            "overall_recall": round(self.overall_recall, 4),
            "deterministic_gate_passed": self.deterministic_gate_passed,
            "ner_gate_passed": self.ner_gate_passed,
            "gated_kinds": {
                "deterministic": sorted(
                    kind.value for kind in DETERMINISTIC_GATE_KINDS
                ),
                "ner_only": sorted(kind.value for kind in NER_GATE_KINDS),
            },
            "by_kind": {
                kind.value: metrics.to_dict()
                for kind, metrics in sorted(
                    self.by_kind.items(), key=lambda pair: pair[0].value
                )
            },
            "misses": [
                {
                    "item_id": item_id,
                    "kind": label.kind.value,
                    "length": label.end - label.start,
                }
                for item_id, label in self.misses
            ],
            "false_positives": [
                {
                    "item_id": item_id,
                    "kind": hit.kind.value,
                    "recognizer_id": hit.recognizer_id,
                    "length": hit.length,
                }
                for item_id, hit in self.false_positives
            ],
            "detector": self.detector_inventory or {},
        }

    def summary(self) -> str:
        """One-screen human rendering, for the eval CLI."""
        lines = [
            f"  redaction recall   {self.deterministic_recall:.3f}"
            f"  (deterministic kinds; gate: 1.000)",
            f"  redaction overall  {self.overall_recall:.3f}"
            f"  ({self.item_count} labelled snippets, NER "
            f"{'installed' if self.ner_installed else 'NOT installed'})",
            "",
            "  kind    labels  recall  precision",
        ]
        for kind, metrics in sorted(
            self.by_kind.items(), key=lambda pair: pair[0].value
        ):
            gate = "gate" if kind in DETERMINISTIC_GATE_KINDS else "ner "
            lines.append(
                f"  {kind.value:<7} {metrics.label_count:<7} "
                f"{metrics.recall:.3f}   {metrics.precision:.3f}   {gate}"
            )
        return "\n".join(lines)


def load_labelled_texts(path: Path | str | None = None) -> tuple[LabelledText, ...]:
    """Read the seeded PII golden set."""
    source = Path(path) if path is not None else DEFAULT_PII_GOLDEN
    document: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{source} must contain a YAML mapping")
    return tuple(LabelledText.from_dict(item) for item in document.get("items", []))


def measure(
    texts: Sequence[LabelledText], *, detector: Detector | None = None
) -> RecallReport:
    """Run the union over the golden set and aggregate per kind."""
    union = detector if detector is not None else redact_detector()
    labels_by_kind: dict[Kind, int] = {}
    found_by_kind: dict[Kind, int] = {}
    detections_by_kind: dict[Kind, int] = {}
    hits_by_kind: dict[Kind, int] = {}
    misses: list[tuple[str, Label]] = []
    false_positives: list[tuple[str, Detection]] = []

    for item in texts:
        detections = union.scan(item.text)
        for label in item.labels:
            labels_by_kind[label.kind] = labels_by_kind.get(label.kind, 0) + 1
            if _covered(label, detections):
                found_by_kind[label.kind] = found_by_kind.get(label.kind, 0) + 1
            else:
                misses.append((item.item_id, label))
        for hit in detections:
            detections_by_kind[hit.kind] = detections_by_kind.get(hit.kind, 0) + 1
            if _hits_a_label(hit, item.labels):
                hits_by_kind[hit.kind] = hits_by_kind.get(hit.kind, 0) + 1
            else:
                false_positives.append((item.item_id, hit))

    kinds = set(labels_by_kind) | set(detections_by_kind)
    return RecallReport(
        item_count=len(texts),
        by_kind={
            kind: KindMetrics(
                kind=kind,
                label_count=labels_by_kind.get(kind, 0),
                found_count=found_by_kind.get(kind, 0),
                detection_count=detections_by_kind.get(kind, 0),
                true_positive_count=hits_by_kind.get(kind, 0),
            )
            for kind in kinds
        },
        misses=tuple(misses),
        false_positives=tuple(false_positives),
        ner_installed=union.uses_ner,
        detector_inventory=union.inventory(),
    )


def redaction_metrics(path: Path | str | None = None) -> dict[str, Any] | None:
    """The ``redaction`` section of the eval report, or None when unavailable.

    Returns None rather than raising when the golden set is missing: the eval
    harness's existing gates are about triage quality and must not start failing
    because a corpus directory moved.
    """
    source = Path(path) if path is not None else DEFAULT_PII_GOLDEN
    if not source.is_file():
        return None
    report = measure(load_labelled_texts(source))
    document = report.to_dict()
    # Relative to the repo when it lives there: an absolute path in a committed
    # -adjacent report is machine noise and says nothing a reader needs.
    document["golden_set"] = _relative(source)
    document["ner_available"] = ner_available()
    return document


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:  # pragma: no cover - a golden set outside the repo
        return str(path)


def _covered(label: Label, detections: Sequence[Detection]) -> bool:
    return any(
        hit.kind is label.kind and hit.contains(label.start, label.end)
        for hit in detections
    )


def _hits_a_label(hit: Detection, labels: Sequence[Label]) -> bool:
    return any(
        label.kind is hit.kind and hit.start < label.end and label.start < hit.end
        for label in labels
    )
