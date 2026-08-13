"""Replay CLI: ``python -m engine.draft.replay --journal DIR --vault DIR``.

The notification replay of part 07 with one more store in front of it. Folds
every case in a JSONL journal directory, works out which drafts it owes, reads
the sealed record for each and writes the letters. Running it on a journal
whose drafts already exist prints zeros and writes nothing - the draft id is a
pure function of the tier-decision event and the template, so a replay
re-derives it, finds it and stops.

Two things an operator would otherwise discover the hard way:

* the worker WRITES to the journal (one DRAFTED per draft) and to a store that
  holds re-hydrated identity data. ``--dry-run`` shows what it would do and
  writes to neither.
* **prepared decisions are not fully replayable, on purpose.** A tier-1 letter
  states the item's extracted values back to the applicant, and the journal
  deliberately carries none of them (the EXTRACTED payload records field ids
  and counts - part 05 was right not to keep a second copy of the submission).
  Those cases are reported as blocked rather than drafted with an empty fact
  list. Nachforderungen replay in full, because every sentence they contain is
  already in the EVIDENCE_ASSEMBLED payload.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine.config_loader import load_config
from engine.draft.projection import draft_case, owed_drafts
from engine.draft.store import DRAFTS_DIR_ENV, JsonlDraftStore, default_draft_store
from engine.journal.store import JsonlJournalStore
from engine.redact.vault import JsonlVaultStore
from schemas.events import Event, EventType


def main(argv: list[str] | None = None) -> int:
    """Replay a journal directory into the draft store."""
    parser = argparse.ArgumentParser(prog="engine.draft.replay", description=__doc__)
    parser.add_argument(
        "--journal",
        type=Path,
        required=True,
        help="JSONL journal directory to replay (one file per case)",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="vault directory holding the sealed records to re-hydrate from",
    )
    parser.add_argument(
        "--drafts",
        type=Path,
        default=None,
        help=f"draft directory to write into (default: ${DRAFTS_DIR_ENV}, or an "
        "in-memory store that is discarded when the process exits)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config directory (default: the repo's config/)",
    )
    parser.add_argument(
        "--rechtsfolgenhinweis",
        action="store_true",
        help="render the par. 66 Abs. 3 SGB I block into every Nachforderung. "
        "OFF by default and deliberately awkward to switch on here: the choice "
        "is a caseworker's, per case, in the review UI of part 10",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be drafted and write nothing, to no store",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if config.drafting is None:
        print(
            "  no config/drafting/ in this config directory: this agency "
            "prepares no drafts",
            file=sys.stderr,
        )
        return 1
    journal = JsonlJournalStore(args.journal)
    vault = JsonlVaultStore(args.vault)
    drafts = (
        JsonlDraftStore(args.drafts)
        if args.drafts is not None
        else default_draft_store()
    )

    case_ids = journal.case_ids()
    written = 0
    already = 0
    blocked = 0
    for case_id in case_ids:
        events = journal.read(case_id)
        already += _drafted_count(events)
        if args.dry_run:
            for item in owed_drafts(events, config=config):
                print(f"  would draft  {item.kind:<18} {item.case_id}")
                written += 1
            continue
        outcome = draft_case(
            events,
            config=config,
            journal=journal,
            vault=vault,
            drafts=drafts,
            rechtsfolgenhinweis=args.rechtsfolgenhinweis,
        )
        for event in outcome.events:
            print(f"  drafted      {event.template_id:<18} {event.case_id}")
        for stopped in outcome.blocked:
            print(
                f"  blocked      {stopped.kind:<18} {stopped.case_id}: {stopped.reason}"
            )
        written += outcome.count
        blocked += len(outcome.blocked)

    verb = "would draft" if args.dry_run else "drafted"
    print(
        f"\n  {len(case_ids)} cases, {already} drafts already recorded, "
        f"{written} {verb} now, {blocked} blocked."
    )
    if not written:
        print(
            "  Nothing owed: the fold dedupes on the tier-decision event, so a "
            "replay of an up-to-date journal is a no-op."
        )
    if blocked:
        print(
            "  Blocked drafts are reported, never half-written: a letter is "
            "produced whole or not at all."
        )
    return 0


def _drafted_count(events: list[Event]) -> int:
    return sum(1 for event in events if event.type is EventType.DRAFTED)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
