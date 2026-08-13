"""``python -m eval.score_fit``: fit the scorer's reference population.

The part-06 precedent applied to the scorer: a fitted thing is emitted by a
named command, carries its own provenance, and is committed as a readable
artifact rather than as a model binary. Three modes:

* default - recompute ``config/scoring/reference_gold_v4.json`` from the frozen
  gold set and write it;
* ``--check`` - recompute and compare byte for byte, exit 1 on a difference.
  This is what makes "the reference population is a pure function of (corpus,
  feature set, seed, engine)" a gate rather than a claim;
* ``--distribution`` - print the score distribution, the labelled anomalies and
  a threshold sweep. This is the calibration evidence: the operating point in
  ``config/scoring/scoring_v1.yaml`` is chosen off this table and the rejected
  alternatives are the other rows of it.

Calibrating on the frozen gold set is the phase-1 instruction of the
implementation plan, and it is stated rather than hidden: the recall and
false-flag numbers this command prints are IN-SAMPLE. The forest itself sees no
labels - the corpus is a reference population, not a training set with targets
(ADR-010) - but the threshold was chosen while looking at the nine labelled
anomalies, and a number chosen that way is not an out-of-sample estimate of
anything. See ADR-024.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from engine.config_loader import ConfigBundle, load_config
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from engine.score import (
    FEATURE_IDS,
    ScoringInput,
    ScoringModel,
    build_features,
    parse_reference,
    reference_document,
)
from eval.harness import DEFAULT_GOLD_DIR, GoldItem, load_corpus

DEFAULT_OUT = Path("config/scoring/reference_gold_v4.json")

#: Repository root, so the artifact records WHICH corpus rather than where it
#: happened to sit on the machine that fitted it. An absolute path in a
#: committed artifact is a rebuild that differs by checkout directory.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where the sweep looks. Percentiles, so the row reads as "review the most
#: unusual N percent" - which is the sentence a Fachbereich can actually take a
#: decision on.
SWEEP = (0.75, 0.80, 0.84, 0.86, 0.88, 0.90, 0.93, 0.95, 0.97, 0.99)


def build_rows(
    items: Sequence[GoldItem], *, config: ConfigBundle
) -> list[tuple[str, list[float], bool]]:
    """One identity-blind feature vector per corpus item, with its gold label.

    The label rides along for the distribution table only; it never reaches the
    matrix that is written or the model that is fitted.
    """
    policy = config.scoring.policy if config.scoring is not None else None
    if policy is None:
        raise SystemExit("no config/scoring/ - nothing to fit")
    rows: list[tuple[str, list[float], bool]] = []
    for item in items:
        outcome = run_pipeline(
            item.payload,
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
        )
        procedure = config.procedure(outcome.procedure_id)
        vector = build_features(
            ScoringInput(
                envelope=outcome.envelope,
                extractions=outcome.extractions,
                evidence=outcome.evidence,
                procedure_id=outcome.procedure_id,
                field_paths=procedure.field_paths if procedure else {},
            ),
            policy,
        )
        rows.append((item.item_id, vector.values, item.labels.anomaly_expected))
    return sorted(rows, key=lambda entry: entry[0])


def fit_document(
    rows: Sequence[tuple[str, list[float], bool]],
    *,
    config: ConfigBundle,
    corpus: Path,
) -> tuple[dict[str, object], ScoringModel]:
    """The artifact document plus the model it describes."""
    scoring = config.scoring
    assert scoring is not None  # guarded by build_rows
    matrix = [(item_id, values) for item_id, values, _ in rows]

    def document(scores: dict[str, float]) -> dict[str, object]:
        return reference_document(
            feature_set_version=scoring.feature_set_version,
            reference_id=scoring.reference_id,
            corpus=_corpus_id(corpus),
            seed=scoring.forest.seed,
            rows=matrix,
            scores=scores,
        )

    # Fit on the ROUNDED rows: the artifact is what ships, so the model that
    # ships must be the one those numbers produce, not the one full-precision
    # floats would have produced.
    population = parse_reference(
        json.dumps(document({item_id: 0.0 for item_id, _ in matrix})), label="fit"
    )
    model = ScoringModel(population, scoring.forest_params)
    scores = {
        item_id: model.score(values)
        for item_id, values in zip(population.row_ids, population.rows, strict=True)
    }
    return document(scores), model


def _corpus_id(corpus: Path) -> str:
    """The corpus as this repository names it, never as a filesystem path."""
    resolved = corpus.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return corpus.name


def render(document: dict[str, object]) -> str:
    """The artifact's bytes: sorted keys never, indent always, newline at end."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def distribution_report(
    rows: Sequence[tuple[str, list[float], bool]],
    model: ScoringModel,
    *,
    config: ConfigBundle,
    items: Sequence[GoldItem],
) -> str:
    """The calibration table: every item's score, and what each threshold buys."""
    scoring = config.scoring
    assert scoring is not None
    scored = sorted(
        (
            (model.score(values), item_id, expected)
            for item_id, values, expected in rows
        ),
        reverse=True,
    )
    tiers = {item.item_id: item.labels.expected_tier for item in items}
    tier1 = _tier_one_ids(items)
    lines = [
        f"reference population   {len(scored)} items, "
        f"feature set {scoring.feature_set_version}",
        f"configured threshold   {scoring.threshold.threshold_id} = "
        f"{scoring.threshold.value}",
        f"tier-1 items           {len(tier1)}",
        "",
        "  score   item                                             anomaly tier",
    ]
    for score, item_id, expected in scored[:28]:
        lines.append(
            f"  {score:.3f}   {item_id:<47} {'JA ' if expected else '-  '}"
            f"    {tiers.get(item_id, 0)}"
        )
    lines.extend(
        [
            "",
            "  threshold  flags  recall  false  t1-flag  t1-false  rate   moved",
        ]
    )
    total_expected = sum(1 for _, _, expected in scored if expected)
    for bound in SWEEP:
        flagged = [entry for entry in scored if entry[0] >= bound]
        hits = sum(1 for _, _, expected in flagged if expected)
        false_flags = [item_id for _, item_id, expected in flagged if not expected]
        tier1_flagged = [item_id for _, item_id, _ in flagged if item_id in tier1]
        tier1_false = [item_id for item_id in false_flags if item_id in tier1]
        rate = len(tier1_false) / len(tier1) if tier1 else 0.0
        # What an ENFORCING run would actually move: a flagged item whose
        # deterministic tier is 1 or 2. Everything else is already at tier 3,
        # where a downgrade is a no-op and the value is the reason text.
        moved = sum(1 for _, item_id, _ in flagged if tiers.get(item_id, 3) < 3)
        lines.append(
            f"  {bound:.2f}       {len(flagged):<6} {hits}/{total_expected}     "
            f"{len(false_flags):<6} {len(tier1_flagged):<8} {len(tier1_false):<9} "
            f"{rate:.3f}  {moved}"
        )
    lines.append("")
    lines.append(
        "  rate = tier-1 items flagged that gold does NOT call anomalous, over "
        "all tier-1 items;\n  that is the reading the 0.15 downgrade-rate "
        "budget bounds. 'moved' = flagged items\n  whose deterministic tier is "
        "1 or 2, i.e. what an enforcing run would actually move."
    )
    return "\n".join(lines)


def _tier_one_ids(items: Sequence[GoldItem]) -> set[str]:
    """Item ids whose deterministic tier is 1, read off the gold labels."""
    return {item.item_id for item in items if item.labels.expected_tier == 1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.score_fit", description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="recompute and compare instead of writing; exit 1 on a difference",
    )
    parser.add_argument(
        "--distribution",
        action="store_true",
        help="print the score distribution and the threshold sweep",
    )
    args = parser.parse_args(argv)

    config = load_config()
    items = load_corpus(args.gold)
    rows = build_rows(items, config=config)
    document, model = fit_document(rows, config=config, corpus=args.gold)
    text = render(document)

    if args.distribution:
        print(distribution_report(rows, model, config=config, items=items))
        print()

    print(
        f"features {len(FEATURE_IDS)}, items {len(rows)}, "
        f"scikit-learn {document['sklearn_version']}"
    )
    if args.check:
        if not args.out.is_file():
            print(f"  MISSING: {args.out}", file=sys.stderr)
            return 1
        current = args.out.read_text(encoding="utf-8")
        if current != text:
            print(
                f"  DIFFERENT: {args.out} is not what this corpus and this "
                f"feature set produce",
                file=sys.stderr,
            )
            return 1
        print(f"  reference population unchanged: {args.out}")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"  written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
