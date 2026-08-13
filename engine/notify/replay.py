"""Replay CLI: ``python -m engine.notify.replay --journal DIR``.

Folds every case in a JSONL journal directory and sends whatever it still owes.
Running it on a journal that is already up to date prints zeros and writes
nothing - that is the whole demonstration, and it is the reason the fold dedupes
on the source event id rather than on a "sent" flag somebody could forget to set.

Two things an operator would otherwise have to discover the hard way, so they are
stated here: the worker WRITES to the journal (one NOTIFIED per dispatch), and
``--dry-run`` is the way to see what it would send without writing anything.

No async infrastructure, no scheduler. The API runs the same fold inline after
each pipeline run; this is the same code with a directory in front of it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine.config_loader import load_config
from engine.journal.store import JsonlJournalStore
from engine.notify.outbox import OUTBOX_DIR_ENV, JsonlOutbox, default_outbox
from engine.notify.projection import notify_case, owed_notifications
from schemas.events import Event, EventType


def main(argv: list[str] | None = None) -> int:
    """Replay a journal directory into the outbox."""
    parser = argparse.ArgumentParser(prog="engine.notify.replay", description=__doc__)
    parser.add_argument(
        "--journal",
        type=Path,
        required=True,
        help="JSONL journal directory to replay (one file per case)",
    )
    parser.add_argument(
        "--outbox",
        type=Path,
        default=None,
        help=f"outbox directory to deliver into (default: ${OUTBOX_DIR_ENV}, "
        "or an in-memory outbox that is discarded when the process exits)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config directory (default: the repo's config/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent and write nothing, to neither store",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if config.notifications is None:
        print(
            "  no config/notifications/ in this config directory: this agency "
            "sends no applicant notifications",
            file=sys.stderr,
        )
        return 1
    journal = JsonlJournalStore(args.journal)
    outbox = JsonlOutbox(args.outbox) if args.outbox is not None else default_outbox()

    case_ids = journal.case_ids()
    sent = 0
    already = 0
    for case_id in case_ids:
        events = journal.read(case_id)
        owed = owed_notifications(events, config=config)
        already += _notified_count(events)
        if args.dry_run:
            for item in owed:
                print(f"  would send  {item.template_id:<24} {item.case_id}")
            sent += len(owed)
            continue
        outcome = notify_case(
            events, config=config, journal=journal, outbox=outbox, now=None
        )
        for event in outcome.events:
            print(f"  sent        {event.template_id:<24} {event.case_id}")
        sent += outcome.count

    verb = "would send" if args.dry_run else "sent"
    print(
        f"\n  {len(case_ids)} cases, {already} notifications already recorded, "
        f"{sent} {verb} now."
    )
    if not sent:
        print(
            "  Nothing owed: the fold is idempotent, so a replay of an "
            "up-to-date journal is a no-op."
        )
    return 0


def _notified_count(events: list[Event]) -> int:
    return sum(1 for event in events if event.type is EventType.NOTIFIED)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
