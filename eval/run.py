"""Eval CLI: ``python -m eval.run``.

Exit code 1 when a gate fails, so CI does not need to parse the report to know
that a change may not ship. Four gates now, reported separately because they
fail for unrelated reasons: the false-clear gate (an item gold says needs
oversight was cleared to tier 1), the redaction gate (a labelled identifier in
the seeded PII golden set was not found by the detector union), the
structured-subset gate (an item with no free text in it scored differently than
it did before the text path existed) and the anomaly-reasons gate (part 09: the
shadow scorer flagged an item without a feature-level reason a caseworker can
read).

The reasons gate is the only part-09 number that gates, and the asymmetry is
deliberate. The score distribution, the recall on the labelled anomalies and
the bias skew are all reported and none of them fails a build, because a gate
on a quality number creates pressure to move the number rather than the system.
"A flag carries readable reasons" is not a quality number - it is the promise
ADR-004 made in part 01, and a promise is exactly the kind of thing that should
fail a build.

The span-verification section is reported and NOT gated. It is a quality number
about extraction, and a gate on it would create pressure to lower the match
threshold until the number looked good - the opposite of what the threshold is
for. A collapse in it shows up in the gated numbers as caution, because every
discarded span pushes its item toward tier 3.

Part 06 adds two more reported-never-gated sections. The **threshold review**
(P-5) lists every governing number with its provenance and a measured operating
point, and prints a notice when the review date has passed - a notice, not a
gate: a report may tell a human that a calendar page turned, and CI may not
start failing because one did. The **classifier** section reports the fallback
classifier's configured state and the items it addresses; ``--classifier`` loads
the configured model and fills in what it would have suggested. That flag is
never part of a gate, for the part-04 reason: a gated number may not depend on
which wheels a machine happens to have.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from engine.config_loader import ConfigBundle, load_config
from engine.evidence import Embedder
from eval.harness import (
    DEFAULT_GOLD_DIR,
    DEFAULT_REPORT_PATH,
    evaluate_corpus,
    load_corpus,
)
from eval.thresholds import review_warning


def main(argv: list[str] | None = None) -> int:
    """Run the eval harness and write the report."""
    parser = argparse.ArgumentParser(prog="eval.run", description=__doc__)
    parser.add_argument(
        "--gold",
        type=Path,
        default=DEFAULT_GOLD_DIR,
        help=f"corpus directory to evaluate (default: {DEFAULT_GOLD_DIR})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"where to write the JSON report (default: {DEFAULT_REPORT_PATH})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config directory (default: the repo's config/)",
    )
    parser.add_argument(
        "--classifier",
        action="store_true",
        help="load the configured embedding model and measure what the fallback "
        "classifier would suggest; needs the [classify] extra, is never part of "
        "a gate, and changes no gated number",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="date the review-date notice is computed against (default: today); "
        "the notice is informational and never changes the exit code",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    items = load_corpus(args.gold)
    embedder = _embedder(config) if args.classifier else None
    report = evaluate_corpus(
        items, config=config, gold_dir=args.gold, embedder=embedder, today=args.today
    )
    written = report.write(args.report)

    print(report.summary())
    warning = review_warning(report.thresholds_review)
    if warning:
        print(f"\n  NOTICE: {warning}")
    print(f"\n  report written to {written}")
    failed = False
    if not report.gate_passed:
        print("\n  FAIL: false_clear_rate must be 0.0", file=sys.stderr)
        failed = True
    if not report.redaction_gate_passed:
        print(
            "\n  FAIL: redaction recall must be 1.000 on the deterministic kinds",
            file=sys.stderr,
        )
        failed = True
    if not report.structured_subset_gate_passed:
        print(
            "\n  FAIL: the structured subset moved; the items with no free text "
            "in them must score exactly what they scored before the text path",
            file=sys.stderr,
        )
        failed = True
    if not report.anomaly_reasons_gate_passed:
        print(
            "\n  FAIL: an item was flagged without a readable feature-level "
            "reason. A flag without reasons never ships (ADR-004)",
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


def _embedder(config: ConfigBundle) -> Embedder | None:
    """Load the configured model, or explain and carry on without it.

    Imported here rather than at module scope so that ``python -m eval.run``
    without the flag never touches the optional package at all.
    """
    from engine.evidence import embedding

    model_id = (
        config.classifier.model_id
        if config.classifier is not None
        else embedding.DEFAULT_MODEL_ID
    )
    loaded = embedding.load_embedder(model_id)
    if loaded is None:
        print(
            f"  classifier model {model_id} unavailable: "
            f"{embedding.unavailable_reason(model_id)}",
            file=sys.stderr,
        )
    return loaded


if __name__ == "__main__":
    raise SystemExit(main())
