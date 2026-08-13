"""The correction pool: every OVERRIDDEN event, as labelled training data.

    python -m engine.journal.corrections --journal <dir> --out <file>

A caseworker who re-routes an item or changes its tier has produced the most
valuable single artifact this system can generate: a labelled example where the
deterministic engine and a trained human disagreed, WITH the reason in words.
That is the classifier's future SetFit food (ADR-021's note), the input to the
next decision-table supersession, and C-5's measured Art. 22 override rate.

Four rules the pool file states in its own header, because a training set whose
provenance lives in a commit message is a training set nobody will trust:

1. **Gold sets stay frozen.** This pool is not a gold set and may never be
   merged into one (ADR-010). A gold label is a considered judgment made once,
   under review; a correction is one caseworker's decision on one afternoon.
   Training on the pool and EVALUATING on the frozen sets is the only order
   that keeps the numbers meaning anything.
2. **No person, anywhere.** The unit is the actor the journal knows (BPersVG,
   C-4). Nothing here identifies who corrected what, and a pool that did would
   be a performance-monitoring dataset that needs a Dienstvereinbarung before
   it may exist at all.
3. **No case content.** Case id, old value, new value, reason, unit, tier,
   procedure, channel. No extracted values, no letter text, no working copy -
   the pool is about the DECISION, not about the applicant. The reason text is
   free-form and written by a caseworker, which is the one field where content
   could leak; it is exported as written and the canary suite sweeps it.
4. **It is append-shaped, not authoritative.** Regenerating the file from the
   journal always reproduces it exactly; the journal stays the truth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.journal.projection import derive_case_state
from engine.journal.store import JournalStore, JsonlJournalStore
from schemas.events import Event, EventType

#: What the pool says about itself, at the top of every file it writes.
POOL_NOTE = (
    "Korrekturpool aus OVERRIDDEN-Ereignissen des Vorgangsjournals. "
    "Trainingsmaterial fuer eine spaetere Klassifikator-Feinjustierung "
    "(ADR-021) und Messgrundlage der Art.-22-Ueberschreibungsquote (C-5). "
    "KEIN Goldsatz: Goldsaetze sind eingefroren (ADR-010) und werden nie aus "
    "diesem Pool ergaenzt - trainiert wird auf dem Pool, gemessen auf dem "
    "Goldsatz. Keine natuerliche Person: der Akteur ist eine "
    "Organisationseinheit (BPersVG par. 80 Abs. 1 Nr. 21, C-4). Kein "
    "Vorgangsinhalt: nur Entscheidung, Korrektur und Begruendung."
)

DEFAULT_OUT = Path("eval/reports/corrections.json")
JOURNAL_DIR_ENV = "EINGANGSLOTSE_JOURNAL_DIR"


@dataclass(frozen=True)
class Correction:
    """One human correction, as the pool records it."""

    case_id: str
    field: str
    from_value: object
    to_value: object
    reason: str
    unit_id: str | None
    occurred_at: datetime
    machine_tier: int | None = None
    machine_unit_id: str | None = None
    procedure_id: str | None = None
    channel: str | None = None
    sampled: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "field": self.field,
            "from": self.from_value,
            "to": self.to_value,
            "reason": self.reason,
            "unit_id": self.unit_id,
            "occurred_at": self.occurred_at.isoformat(),
            "machine_tier": self.machine_tier,
            "machine_unit_id": self.machine_unit_id,
            "procedure_id": self.procedure_id,
            "channel": self.channel,
            "sampled": self.sampled,
        }


def corrections_for(case_id: str, events: Sequence[Event]) -> list[Correction]:
    """Every correction one case carries, in journal order.

    Reads defensively like every other projection here: an OVERRIDDEN whose
    payload lost a key contributes what it still has rather than raising, so a
    malformed event cannot take the export down for the other 400 cases.
    """
    state = derive_case_state(case_id, list(events))
    ordered = sorted(events, key=lambda event: event.sequence)
    return [
        Correction(
            case_id=case_id,
            field=str(event.payload.get("field", "")),
            from_value=event.payload.get("from"),
            to_value=event.payload.get("to"),
            reason=str(event.payload.get("reason", "")),
            unit_id=event.actor.unit_id,
            occurred_at=event.occurred_at,
            machine_tier=state.tier,
            machine_unit_id=state.routed_unit_id,
            procedure_id=state.procedure_id,
            channel=state.channel,
            sampled=bool(event.payload.get("sampled")),
        )
        for event in ordered
        if event.type is EventType.OVERRIDDEN
    ]


def collect(store: JournalStore) -> list[Correction]:
    """Every correction in a journal, sorted by case then by time."""
    pool: list[Correction] = []
    for case_id in store.case_ids():
        pool.extend(corrections_for(case_id, store.read(case_id)))
    pool.sort(key=lambda item: (item.case_id, item.occurred_at))
    return pool


def build_pool(store: JournalStore, *, now: datetime | None = None) -> dict[str, Any]:
    """The pool document: the note, the counts, and the corrections."""
    pool = collect(store)
    return {
        "note": POOL_NOTE,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "count": len(pool),
        "by_field": _counts(item.field for item in pool),
        "by_unit": _counts(item.unit_id or "(ohne Einheit)" for item in pool),
        "corrections": [item.as_payload() for item in pool],
    }


def write_pool(document: dict[str, Any], path: Path) -> Path:
    """Write the pool as pretty JSON; deterministic given the same journal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit 0 always: an empty pool is a normal state."""
    parser = argparse.ArgumentParser(
        prog="python -m engine.journal.corrections",
        description="Collect OVERRIDDEN events into a labelled correction pool.",
    )
    parser.add_argument(
        "--journal",
        default=os.environ.get(JOURNAL_DIR_ENV),
        help="journal directory (default: $EINGANGSLOTSE_JOURNAL_DIR)",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output JSON file")
    args = parser.parse_args(argv)
    if not args.journal:
        parser.error(
            "no journal directory: pass --journal or set "
            f"{JOURNAL_DIR_ENV}. An in-memory journal has nothing to export."
        )
    document = build_pool(JsonlJournalStore(args.journal))
    path = write_pool(document, Path(args.out))
    print(f"{document['count']} Korrektur(en) aus {args.journal} -> {path}")
    for field_id, count in sorted(document["by_field"].items()):
        print(f"  {field_id}: {count}")
    return 0


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
