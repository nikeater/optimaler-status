"""Fit the classifier's calibration: ``python -m eval.calibrate``.

Needs the ``[classify]`` extra and the configured model's weights. Prints a YAML
block for a human to paste into ``config/classifier/classifier_v1.yaml``, and
writes nothing: the loader refuses to enable the classifier without a
calibration and its provenance, and that refusal would be theatre if the
calibration could appear in the config without anybody deciding to put it there.

Exit codes are about whether the fit RAN, never about whether the numbers are
good. A fit that produces a poor map is a finding; a fit that could not run is
a missing prerequisite, and only the second one is an error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from engine.config_loader import load_config
from engine.evidence import embedding
from eval.calibration import DEFAULT_BIN_COUNT
from eval.classifier import fit_from_observations, observe_corpus
from eval.harness import DEFAULT_GOLD_DIR, load_corpus


def main(argv: list[str] | None = None) -> int:
    """Run the corpus through the real model and print the calibration block."""
    parser = argparse.ArgumentParser(prog="eval.calibrate", description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--bins",
        type=int,
        default=DEFAULT_BIN_COUNT,
        help=f"number of equal-frequency bins (default: {DEFAULT_BIN_COUNT})",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="the date stamped into the block as fitted_at (default: today)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if config.classifier is None:
        print(
            "no config/classifier/ in this config directory: nothing to calibrate",
            file=sys.stderr,
        )
        return 2
    model_id = config.classifier.model_id
    embedder = embedding.load_embedder(model_id)
    if embedder is None:
        print(
            f"the [classify] extra or the model {model_id} is unavailable: "
            f"{embedding.unavailable_reason(model_id)}\n"
            f"install it with: pip install -e '.[classify]'",
            file=sys.stderr,
        )
        return 2

    items = load_corpus(args.gold)
    print(
        f"scoring {len(items)} items from {args.gold.as_posix()} with {model_id} ...",
        file=sys.stderr,
    )
    observations = observe_corpus(items, config=config, embedder=embedder)
    fitted = fit_from_observations(
        observations,
        model_id=model_id,
        gold_dir=args.gold.as_posix(),
        today=args.today or datetime.now(UTC).date(),
        bin_count=args.bins,
    )
    if fitted is None:
        print(
            "no item in this corpus carries an expected unit, so there is "
            "nothing to fit against",
            file=sys.stderr,
        )
        return 2
    print(fitted.as_yaml())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
