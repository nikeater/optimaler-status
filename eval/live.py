"""Measuring a live extraction model, and swapping it for another one (P-16).

Two questions, one harness, and neither of them is ever a gate:

**How well can a model actually read a letter?** The gated numbers come from the
replay extractor, which locates values by the labels the corpus generator itself
wrote - it measures the verifier, the merge and the discard accounting, not
extraction. This module answers the other half by running the same items with
the fixture REMOVED and a model in its place, and comparing what the model got
verified against what the corpus declares.

**Could this agency swap the model?** That is backlog row P-16, digital
sovereignty: the claim that no part of this system depends on one vendor's
endpoint. It is answered by pointing the same comparison at two or more
configured endpoints and printing the rows next to each other. If the numbers
differ, that is a procurement fact; if the harness cannot be pointed somewhere
else, the claim was never true.

**Why it can never gate.** A metric that moved because a model was warm, or
because a GPU was busy, or because somebody pulled a newer tag of the same
model name, is not a metric. The gate runs on the deterministic path and would
produce the same numbers on a laptop with no model on it at all.

Nothing here runs unless an endpoint is explicitly configured (ADR-012's rule,
unchanged): no probe, no fallback to "some model that happens to be running".
The transport is injectable, so the tests measure a scripted model rather than a
socket.

Usage (the tags below are the two that were actually measured in part 12; pin a
point version, because ``mistral:7b-instruct`` floats and the extractor stamps
the tag into every record's provenance)::

    python -m eval.live \\
        --model ollama=http://localhost:11434,mistral:7b-instruct-v0.3-q4_K_M
    python -m eval.live \\
        --model a=http://localhost:11434,mistral:7b-instruct-v0.3-q4_K_M \\
        --model b=http://localhost:11434,qwen2.5:7b-instruct-q4_K_M
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.config_loader import ConfigBundle, load_config
from engine.extract import LiveExtractor, LiveSettings
from engine.extract.llm import Transport
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from eval.harness import DEFAULT_GOLD_DIR, GoldItem, load_corpus

#: Where the comparison lands when nobody says otherwise.
DEFAULT_REPORT_PATH = Path("eval/reports/live.json")

#: The submission key the corpus fixture rides on. Removed for a live run: the
#: point is what the MODEL can read, not what the generator already knew.
FIXTURE_KEY = "extractionFixture"


@dataclass(frozen=True)
class ModelUnderTest:
    """One configured endpoint, named so a report row can be read."""

    label: str
    base_url: str
    model: str

    @property
    def settings(self) -> LiveSettings:
        return LiveSettings(base_url=self.base_url, model=self.model)

    @classmethod
    def parse(cls, specification: str) -> ModelUnderTest:
        """``label=base_url,model`` - the whole configuration of one row."""
        label, _, remainder = specification.partition("=")
        base_url, _, model = remainder.partition(",")
        if not (label and base_url and model):
            raise ValueError(
                f"--model expects 'label=base_url,model', got {specification!r}"
            )
        return cls(label=label, base_url=base_url, model=model)


@dataclass(frozen=True)
class FieldOutcome:
    """What one model made of one field of one item."""

    item_id: str
    field: str
    expected: str
    actual: str | None

    @property
    def found(self) -> bool:
        return self.actual is not None

    @property
    def agrees(self) -> bool:
        """Whether the model's verified value is the corpus's value.

        Compared after collapsing whitespace and folding case, and nothing else:
        an extraction that differs in a digit differs in the fact.
        """
        return self.actual is not None and _fold(self.actual) == _fold(self.expected)


@dataclass(frozen=True)
class ModelResult:
    """One model's row of the comparison."""

    label: str
    model: str
    base_url: str
    reachable: bool
    items: int = 0
    outcomes: list[FieldOutcome] = field(default_factory=list)
    proposals: int = 0
    verified: int = 0
    discarded: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    tier_agreements: int = 0
    #: Wall clock spent inside the LIVE runs only, summed over items. The
    #: replay baseline each item is compared against is deliberately excluded:
    #: the question is what a model costs, not what the harness costs. Reported
    #: because "usable on this machine" is partly a speed claim and partly a
    #: quality one, and a number that mixed the two would answer neither.
    seconds: float = 0.0

    @property
    def expected_fields(self) -> int:
        return len(self.outcomes)

    @property
    def agreeing(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.agrees)

    @property
    def found(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.found)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "base_url": self.base_url,
            "reachable": self.reachable,
            "items": self.items,
            "expected_fields": self.expected_fields,
            "fields_extracted": self.found,
            "fields_agreeing": self.agreeing,
            # Recall against the corpus's declared facts. There is no precision
            # counterpart: a value the model produced that the corpus does not
            # declare is not necessarily wrong, it may be a field the scenario
            # never stated, and counting it as an error would punish reading.
            "field_recall": _ratio(self.agreeing, self.expected_fields),
            "spans_proposed": self.proposals,
            "spans_verified": self.verified,
            "spans_discarded": self.discarded,
            "verified_rate": _ratio(self.verified, self.proposals),
            "failures": self.failures,
            "tier_agreement": _ratio(self.tier_agreements, self.items),
            "seconds_total": round(self.seconds, 3),
            "seconds_per_item": round(self.seconds / self.items, 3)
            if self.items
            else 0.0,
            "disagreements": [
                {
                    "item_id": outcome.item_id,
                    "field": outcome.field,
                    "extracted": outcome.actual is not None,
                }
                for outcome in self.outcomes
                if not outcome.agrees
            ],
        }


@dataclass(frozen=True)
class LiveReport:
    """Every configured model, measured against the replay path."""

    gold_dir: str
    text_items: int
    models: list[ModelResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gold_dir": self.gold_dir,
            "text_items": self.text_items,
            "gated": False,
            "note": (
                "Live-model numbers are measured and never gated: they depend on "
                "a model, a machine and a moment. The gate runs the "
                "deterministic replay extractor."
            ),
            "models": [model.to_dict() for model in self.models],
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def summary(self) -> str:
        lines = [
            "EingangsLotse live-extraction comparison (P-16)",
            f"  gold dir           {self.gold_dir}",
            f"  text items         {self.text_items}",
            "",
            "  model              reach  fields  agree  recall  spans  verified  tier"
            "   s/item",
        ]
        for model in self.models:
            row = model.to_dict()
            lines.append(
                f"  {model.label:<18} {'yes' if model.reachable else 'NO ':<6}"
                f" {row['expected_fields']:<7} {row['fields_agreeing']:<6}"
                f" {row['field_recall']:.3f}   {row['spans_proposed']:<6}"
                f" {row['verified_rate']:.3f}     {row['tier_agreement']:.3f}"
                f"  {row['seconds_per_item']:.2f}"
            )
        lines.extend(
            [
                "",
                "  Measured, never gated. The gate runs the deterministic replay",
                "  extractor and produces the same numbers on a machine with no",
                "  model installed.",
            ]
        )
        return "\n".join(lines)


def text_items(items: Sequence[GoldItem]) -> list[GoldItem]:
    """The corpus items that arrive as prose; the only ones a model can read."""
    return [item for item in items if item.payload.get("bodyText")]


def compare_models(
    items: Sequence[GoldItem],
    *,
    config: ConfigBundle,
    models: Sequence[ModelUnderTest],
    transport: Transport | None = None,
    gold_dir: Path = DEFAULT_GOLD_DIR,
) -> LiveReport:
    """Run every text item against every configured model and compare."""
    letters = text_items(items)
    return LiveReport(
        gold_dir=str(gold_dir),
        text_items=len(letters),
        models=[
            _measure(letters, config=config, model=model, transport=transport)
            for model in models
        ],
    )


def _measure(
    items: Sequence[GoldItem],
    *,
    config: ConfigBundle,
    model: ModelUnderTest,
    transport: Transport | None,
) -> ModelResult:
    extractor = LiveExtractor(
        model.settings,
        system_prompt=config.extraction.prompt.system,
        user_prompt=config.extraction.prompt.user,
        transport=transport,
    )
    if not extractor.available():
        # Loud, not silent: somebody explicitly asked for this endpoint.
        return ModelResult(
            label=model.label,
            model=model.model,
            base_url=model.base_url,
            reachable=False,
        )
    outcomes: list[FieldOutcome] = []
    proposals = verified = discarded = tier_agreements = 0
    failures: dict[str, int] = {}
    seconds = 0.0
    for item in items:
        expected = _declared_values(item, config=config)
        started = time.perf_counter()
        live = _run(item, config=config, extractor=extractor)
        seconds += time.perf_counter() - started
        baseline = _run(item, config=config, extractor=None, with_fixture=True)
        actual = {record.field: record.value for record in live.records}
        outcomes.extend(
            FieldOutcome(
                item_id=item.item_id,
                field=field_id,
                expected=value,
                actual=actual.get(field_id),
            )
            for field_id, value in sorted(expected.items())
        )
        proposals += live.proposals
        verified += live.verified
        discarded += live.discarded
        tier_agreements += int(live.tier == baseline.tier)
        for kind, count in live.failures.items():
            failures[kind] = failures.get(kind, 0) + count
    return ModelResult(
        label=model.label,
        model=model.model,
        base_url=model.base_url,
        reachable=True,
        items=len(items),
        outcomes=outcomes,
        proposals=proposals,
        verified=verified,
        discarded=discarded,
        failures=dict(sorted(failures.items())),
        tier_agreements=tier_agreements,
        seconds=seconds,
    )


@dataclass(frozen=True)
class _Run:
    """One pipeline run, reduced to what the comparison needs."""

    tier: int
    records: tuple[Any, ...]
    proposals: int
    verified: int
    discarded: int
    failures: dict[str, int]


def _run(
    item: GoldItem,
    *,
    config: ConfigBundle,
    extractor: LiveExtractor | None,
    with_fixture: bool = False,
) -> _Run:
    payload = dict(item.payload)
    if not with_fixture:
        # The model reads the letter, not the generator's notes about it.
        payload.pop(FIXTURE_KEY, None)
    outcome = run_pipeline(
        payload,
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        live_extractor=extractor,
    )
    stats = outcome.extraction.stats() if outcome.extraction is not None else {}
    return _Run(
        tier=int(outcome.decision.tier),
        records=tuple(outcome.extractions.records),
        proposals=int(stats.get("proposals", 0)),
        verified=int(stats.get("verified", 0)),
        discarded=int(stats.get("discarded", 0)),
        failures=dict(stats.get("failures", {})),
    )


def _declared_values(item: GoldItem, *, config: ConfigBundle) -> dict[str, str]:
    """The values the corpus says stand in this letter, from its own sidecar.

    Sealed entries are skipped: their value is a placeholder minted at ingest, so
    "did the model say the same thing" has no stable answer for them. What is
    left is exactly the set of facts a reader could copy out of the letter.
    """
    fixture = item.payload.get(FIXTURE_KEY)
    if not isinstance(fixture, list):
        return {}
    return {
        str(entry["field"]): str(entry["value"])
        for entry in fixture
        if isinstance(entry, dict)
        and entry.get("mode") == "literal"
        and entry.get("value")
    }


def _fold(value: str) -> str:
    return " ".join(value.split()).casefold()


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def main(argv: list[str] | None = None) -> int:
    """Compare one or more configured extraction models. Never a gate."""
    parser = argparse.ArgumentParser(prog="eval.live", description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="LABEL=BASE_URL,MODEL",
        help="an endpoint to measure; repeat for the P-16 swap comparison",
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.model:
        print(
            "no endpoint configured; nothing to measure.\n"
            "  python -m eval.live --model ollama=http://localhost:11434,"
            "mistral:7b-instruct-v0.3-q4_K_M\n"
            "See docs/BUILD.md for the Ollama setup and the part-12 numbers.",
            file=sys.stderr,
        )
        return 2
    try:
        models = [ModelUnderTest.parse(entry) for entry in args.model]
    except ValueError as error:
        print(f"live comparison failed: {error}", file=sys.stderr)
        return 2

    report = compare_models(
        load_corpus(args.gold),
        config=load_config(args.config),
        models=models,
        gold_dir=args.gold,
    )
    written = report.write(args.report)
    print(report.summary())
    print(f"\n  report written to {written}")
    unreachable = [model.label for model in report.models if not model.reachable]
    if unreachable:
        print(
            f"\n  NOTE: not reached: {', '.join(unreachable)}. Explicitly "
            f"requested endpoints that are not there are reported, not ignored.",
            file=sys.stderr,
        )
    # Exit 0 even then: this command measures, it does not gate. CI runs
    # `python -m eval.run`, which never touches an endpoint.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
