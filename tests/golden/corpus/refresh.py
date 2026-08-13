"""Regenerate ``tests/golden/corpus/expected.yaml``.

Run from the repo root when a renderer or sidecar change is intended::

    python -m tests.golden.corpus.refresh

It writes the file and prints what changed at the item level. Refreshing a
golden expectation is a decision, never a side effect of a corpus rebuild, so
this is a separate command that a human has to type.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from corpus.generator.build import build_items, load_specs
from corpus.generator.paraphrase import DeterministicParaphraser

GOLDEN_DIR = Path(__file__).resolve().parent
SCENARIO_DIR = GOLDEN_DIR / "scenarios"
EXPECTED_PATH = GOLDEN_DIR / "expected.yaml"
GOLDEN_SEED = 7


def main() -> int:
    """Write the expectation file for the golden scenarios."""
    items = build_items(
        load_specs(SCENARIO_DIR),
        seed=GOLDEN_SEED,
        paraphraser=DeterministicParaphraser(),
    )
    document = {
        "seed": GOLDEN_SEED,
        "items": {
            item.item_id: {"labels": item.labels, "payload_sha256": item.sha256()}
            for item in items
        },
    }
    EXPECTED_PATH.write_text(
        "# GENERATED expectation for tests/test_corpus_golden.py.\n"
        "# Refresh deliberately with: python -m tests.golden.corpus.refresh\n"
        + yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {EXPECTED_PATH} with {len(items)} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
