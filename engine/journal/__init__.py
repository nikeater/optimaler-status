"""Append-only case journal.

Every stage writes immutable, version-stamped events; all current state is a
projection of them (ADR-005). PostgreSQL lands with the compose profile in a
later part; S1 ships the protocol plus an in-memory and a JSONL store so the
rest of the pipeline is written against the protocol from day one.
"""

from engine.journal.projection import CaseState, derive_case_state
from engine.journal.store import (
    InMemoryJournalStore,
    JournalStore,
    JsonlJournalStore,
    SequenceConflictError,
    emit,
    make_event,
)

__all__ = [
    "CaseState",
    "InMemoryJournalStore",
    "JournalStore",
    "JsonlJournalStore",
    "SequenceConflictError",
    "derive_case_state",
    "emit",
    "make_event",
]
