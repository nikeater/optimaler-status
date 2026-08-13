"""Where rendered drafts live: a store behind a protocol, PII-bearing by design.

Same shape as the journal (ADR-008), the identity vault (ADR-018) and the
applicant outbox (part 07): one protocol, an in-memory backend for tests and
dev, a JSONL backend when an operator points ``EINGANGSLOTSE_DRAFTS_DIR``
somewhere. A real deployment replaces the backend; nothing above this module
knows which.

**This store JOINS THE VAULT on the canary exception list, and it is the only
other member.** A draft is a letter to a named person about their own case: it
carries the applicant's re-hydrated identity because a Nachforderung without it
would be unpostable. The canary suite therefore asserts two things here rather
than one - that the seeded identities DO appear in a draft (that is
re-hydration working) and that they appear nowhere else, including in the
DRAFTED journal event this same package writes.

The consequence for a deployment is written down rather than left to be
discovered: this store needs the vault's protections, not the journal's. Same
encryption at rest, same retention question, same missing ``purge`` operation
(docs/vault-dpia-input.md), and access behind the role model part 10 builds -
``GET /drafts/{case_id}`` is open today because the data is synthetic.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from schemas.common import StrictModel

DRAFTS_DIR_ENV = "EINGANGSLOTSE_DRAFTS_DIR"

_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class DraftRecord(StrictModel):
    """One rendered draft, exactly as a caseworker would confirm it.

    ``subject`` and ``body`` are re-hydrated text and therefore PII-bearing:
    this model is the reason the store above is on the canary exception list.
    Everything else on it is value-free and is what the DRAFTED journal event
    repeats - so "which draft exists" is answerable from the journal alone,
    and "what does it say" is answerable only from here.
    """

    draft_id: str
    case_id: str
    envelope_id: str
    kind: str = Field(description="nachforderung | prepared_decision")
    template_id: str
    procedure_id: str | None = None
    tier: int
    requirement_ids: list[str] = Field(
        default_factory=list,
        description="Gap requirement ids the letter asks about (Nachforderung), "
        "or the requirements a prepared decision states as satisfied",
    )
    amtsermittlung_ids: list[str] = Field(
        default_factory=list,
        description="Requirement ids softened under C-7 and excluded from any "
        "par. 66 Abs. 3 scope",
    )
    subject: str
    body: str
    resolved_tokens: int = Field(ge=0)
    distinct_tokens: int = Field(ge=0)
    token_kinds: dict[str, int] = Field(default_factory=dict)
    response_window_days: int | None = None
    rechtsfolgenhinweis: bool = False
    source_event_id: str
    drafting_version: str
    created_at: datetime

    def summary(self) -> dict[str, Any]:
        """Value-free projection: what the journal and any log line may see."""
        return {
            "draft_id": self.draft_id,
            "kind": self.kind,
            "template_id": self.template_id,
            "procedure_id": self.procedure_id,
            "tier": self.tier,
            "requirement_ids": list(self.requirement_ids),
            "amtsermittlung_ids": list(self.amtsermittlung_ids),
            "resolved_tokens": self.resolved_tokens,
            "distinct_tokens": self.distinct_tokens,
            "token_kinds": dict(sorted(self.token_kinds.items())),
            "response_window_days": self.response_window_days,
            "rechtsfolgenhinweis": self.rechtsfolgenhinweis,
            "drafting_version": self.drafting_version,
            # Length, never text - the part-07 rule applied to a surface that
            # carries far more than a receipt did.
            "body_chars": len(self.body),
        }


def draft_id_for(source_event_id: str, template_id: str) -> str:
    """The stable id of the draft one tier decision owes.

    A function of the two things that identify it and of nothing else - no
    counter, no uuid, no clock - exactly like ``notification_id_for``. That is
    what makes a replay re-derive the same id, find it already stored, and
    write nothing.
    """
    return f"{source_event_id}-{template_id}"


@runtime_checkable
class DraftStore(Protocol):
    """The storage contract every draft backend implements."""

    def save(self, record: DraftRecord) -> bool:
        """Store one draft; False when its draft_id is already there."""
        ...

    def records(self, case_id: str) -> list[DraftRecord]:
        """Every draft of a case, oldest first (empty list if unknown)."""
        ...

    def case_ids(self) -> list[str]:
        """All case ids this store holds, sorted."""
        ...


class InMemoryDraftStore:
    """Process-local draft store; the default for tests, eval runs and dev."""

    def __init__(self) -> None:
        self._records: dict[str, list[DraftRecord]] = {}

    def save(self, record: DraftRecord) -> bool:
        records = self._records.setdefault(record.case_id, [])
        if any(known.draft_id == record.draft_id for known in records):
            return False
        records.append(record)
        return True

    def records(self, case_id: str) -> list[DraftRecord]:
        return list(self._records.get(case_id, []))

    def case_ids(self) -> list[str]:
        return sorted(self._records)

    def count(self) -> int:
        """How many drafts this store holds, across every case."""
        return sum(len(records) for records in self._records.values())


class JsonlDraftStore:
    """File-backed draft store: one append-only JSONL file per case.

    PLAINTEXT, and the same deliberate limitation ``JsonlVaultStore`` documents
    for itself: this is a dev and test backend holding synthetic data, and a
    home-rolled file cipher would look like protection without being any. A
    production deployment puts drafts where it puts the vault.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, record: DraftRecord) -> bool:
        if any(
            known.draft_id == record.draft_id for known in self.records(record.case_id)
        ):
            return False
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        with self._path(record.case_id).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True

    def records(self, case_id: str) -> list[DraftRecord]:
        path = self._path(case_id)
        if not path.is_file():
            return []
        return [
            DraftRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def case_ids(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.jsonl"))

    def _path(self, case_id: str) -> Path:
        if not _SAFE_CASE_ID.match(case_id):
            raise ValueError(
                f"case_id {case_id!r} is not filesystem-safe; allowed: "
                "letters, digits, dot, underscore, hyphen (max 120 chars)"
            )
        return self.directory / f"{case_id}.jsonl"


def default_draft_store() -> DraftStore:
    """In-memory store, or a JSONL one when the env var points somewhere.

    Mirrors ``default_journal``, ``default_vault`` and ``default_outbox``: four
    stores with the same lifecycle should not need four conventions.
    """
    directory = os.environ.get(DRAFTS_DIR_ENV)
    if directory:
        return JsonlDraftStore(directory)
    return InMemoryDraftStore()
