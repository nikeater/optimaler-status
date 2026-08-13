"""Conditional drafting with round-trip vault re-hydration (ADR-003, ADR-023).

    tier decision (journal)
      -> projection.py   what does this case owe? tier 2 with gaps, or tier 1
      -> letters.py      config wording + gap sentences + the case's facts
      -> rehydrate.py    THE vault read; every token resolved or no draft
      -> store.py        the rendered letter, PII-bearing, canary-exempt
      -> DRAFTED         back into the journal: ids and counts, never the text

Nothing here is dispatched. A Nachforderung becomes a request and a
Bewilligungsentwurf becomes a Verwaltungsakt only when a human confirms and
sends it, which is part 10's work; ``fully_automated`` stays false everywhere.

This package is where ADR-002's promise is kept: identity was sealed at ingest
(part 04), travelled as placeholders through five parts that never dereferenced
the vault, and comes back here, at outbound template rendering, round-trip
checked, with an unknown placeholder as a hard error that blocks the draft.
"""

from engine.draft.bekanntgabe import (
    BEKANNTGABE_DAYS,
    ResponseDeadline,
    bekanntgabe_date,
    is_working_day,
    next_working_day,
    response_deadline,
)
from engine.draft.letters import (
    KIND_NACHFORDERUNG,
    KIND_PREPARED_DECISION,
    DraftingError,
    DraftRequest,
    GapSentence,
    RenderedDraft,
    addressee_slots,
    build_letter,
    gap_sentences,
    requirement_label,
)
from engine.draft.projection import (
    BlockedDraft,
    DraftOutcome,
    OwedDraft,
    draft_case,
    draft_kind_for,
    drafted_source_event_ids,
    owed_drafts,
)
from engine.draft.rehydrate import (
    RehydrationError,
    RehydrationResult,
    Rehydrator,
    format_address,
    format_value,
    placeholders_by_path,
    rehydrate,
    round_trip_ok,
)
from engine.draft.store import (
    DRAFTS_DIR_ENV,
    DraftRecord,
    DraftStore,
    InMemoryDraftStore,
    JsonlDraftStore,
    default_draft_store,
    draft_id_for,
)

__all__ = [
    "BEKANNTGABE_DAYS",
    "DRAFTS_DIR_ENV",
    "KIND_NACHFORDERUNG",
    "KIND_PREPARED_DECISION",
    "BlockedDraft",
    "DraftOutcome",
    "DraftRecord",
    "DraftRequest",
    "DraftStore",
    "DraftingError",
    "GapSentence",
    "InMemoryDraftStore",
    "JsonlDraftStore",
    "OwedDraft",
    "RehydrationError",
    "RehydrationResult",
    "Rehydrator",
    "RenderedDraft",
    "ResponseDeadline",
    "addressee_slots",
    "bekanntgabe_date",
    "build_letter",
    "default_draft_store",
    "draft_case",
    "draft_id_for",
    "draft_kind_for",
    "drafted_source_event_ids",
    "format_address",
    "format_value",
    "gap_sentences",
    "is_working_day",
    "next_working_day",
    "owed_drafts",
    "placeholders_by_path",
    "rehydrate",
    "requirement_label",
    "response_deadline",
    "round_trip_ok",
]
