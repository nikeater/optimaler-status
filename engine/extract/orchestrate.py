"""One ExtractionSet from two very different readers.

    structured payload  -> engine.extract.mapper   -> MatchMode.STRUCTURED
    normalized text     -> replay / live extractor -> proposals
                                                   -> engine.extract.verify
                                                   -> MatchMode.EXACT / FUZZY

Both halves land in the same :class:`schemas.extraction.ExtractionSet`, and every
value in it has provenance: a payload path behind a STRUCTURED record, a verified
span behind the other two. ``discarded_count`` counts both kinds of loss - a
field map entry whose path was absent, and a proposal that failed the double lock
- because the decision table reads it as one thing: how much this item could not
be established, and therefore how much oversight it needs.

**Precedence.** A field the schema mapper filled is not overwritten by a text
proposal. Reading a JSON key is not an inference and prose is; when the two
disagree the deterministic reading wins, and the text proposal is recorded as a
``duplicate_field`` discard so the disagreement shows up in the histogram
instead of vanishing. No item in the corpus mixes the two today, which is
exactly why the rule has to be written down now rather than discovered later.

**The EXTRACTED event** carries the verification statistics (P-12): per part,
how many proposals were made, how many became records, and the histogram of
failure kinds. Counts and kinds only - never a value, never a quote, not even a
rejected one. A rejected proposal is precisely the text least worth trusting,
and an audit trail that quoted it would be putting unverified content into the
one artifact that is kept longest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from engine.config_loader import ExtractionConfig, ProcedureConfig
from engine.extract.mapper import EXTRACTOR_ID as MAPPER_ID
from engine.extract.mapper import map_payload
from engine.extract.proposal import Proposal
from engine.extract.replay import FixtureEntry, ReplayStats, replay_proposals
from engine.extract.verify import Verification, verify_proposals
from engine.ingest.envelope import structured_payload
from engine.journal.store import JournalStore, emit
from engine.textlayer import text_parts
from schemas.common import VersionStamp
from schemas.envelope import Envelope
from schemas.events import EventType
from schemas.extraction import ExtractionRecord, ExtractionSet
from schemas.textlayer import TextLayer


class TextExtractor(Protocol):
    """What the orchestration needs from any reader of prose.

    One method, and it may not raise: an extractor that cannot answer returns no
    proposals, which the rest of the system already knows how to handle.
    """

    @property
    def extractor_id(self) -> str:
        """Provenance stamped on the records this extractor's proposals become."""
        ...

    def propose(
        self, *, part_id: str, text: str, fields: Mapping[str, str]
    ) -> tuple[Proposal, ...]:
        """Proposals over one normalized part; () when there are none."""
        ...


@dataclass(frozen=True)
class ExtractionOutcome:
    """The set, plus everything that was said about how it came to be."""

    extractions: ExtractionSet
    verifications: tuple[Verification, ...] = ()
    replay: ReplayStats = field(default_factory=ReplayStats)
    mapper_discarded: tuple[str, ...] = ()

    @property
    def verified_count(self) -> int:
        return sum(1 for outcome in self.verifications if outcome.accepted)

    @property
    def text_discarded_count(self) -> int:
        return sum(1 for outcome in self.verifications if not outcome.accepted)

    def failure_counts(self) -> dict[str, int]:
        """Histogram of why proposals were discarded."""
        return dict(
            sorted(
                Counter(
                    outcome.failure.value
                    for outcome in self.verifications
                    if outcome.failure is not None
                ).items()
            )
        )

    def stats(self) -> dict[str, Any]:
        """Value-free verification statistics for the journal and the eval."""
        return {
            "proposals": len(self.verifications),
            "verified": self.verified_count,
            "discarded": self.text_discarded_count,
            "failures": self.failure_counts(),
            "replay": self.replay.to_dict(),
            "by_part": self._by_part(),
        }

    def _by_part(self) -> list[dict[str, Any]]:
        parts: dict[str, list[Verification]] = {}
        for outcome in self.verifications:
            parts.setdefault(outcome.proposal.part_id, []).append(outcome)
        return [
            {
                "part_id": part_id,
                "proposals": len(group),
                "verified": sum(1 for outcome in group if outcome.accepted),
                "discarded": sum(1 for outcome in group if not outcome.accepted),
            }
            for part_id, group in sorted(parts.items())
        ]


def field_descriptions(procedure: ProcedureConfig | None) -> dict[str, str]:
    """Field id -> the requirement wording that defines it.

    The prompt is built from the SAME sentences the completeness checker and the
    Nachforderung wording are built from. One definition of what a field means
    per procedure; a second one written for a model would drift on the first
    fachliche correction nobody thought to copy over.
    """
    if procedure is None:
        return {}
    known = {entry.field for entry in procedure.field_map}
    return {
        requirement.requirement_id: requirement.description
        for requirement in procedure.requirements.requirements
        if requirement.kind == "field" and requirement.requirement_id in known
    }


def extract_all(
    envelope: Envelope,
    layer: TextLayer | None,
    procedure: ProcedureConfig | None,
    *,
    config: ExtractionConfig,
    journal: JournalStore,
    versions: VersionStamp,
    fixture: Sequence[FixtureEntry] = (),
    live: TextExtractor | None = None,
    procedure_id: str | None = None,
    now: datetime | None = None,
) -> ExtractionOutcome:
    """Map the structured part, verify the text proposals, journal the lot."""
    field_map = procedure.field_map if procedure is not None else []
    records, mapper_discarded = map_payload(structured_payload(envelope), field_map)
    # Always a set, empty included. With no derived procedure NOTHING is a known
    # field, so every text proposal is discarded as `unknown_field` rather than
    # accepted: a record for a field id nobody configured would enter the
    # `extraction.*` namespace that routing rules read, and an item without a
    # procedure is already on its way to tier 3 anyway.
    known_fields = frozenset(entry.field for entry in field_map)

    proposals, replay = replay_proposals(
        fixture, layer, extractor_id=config.replay.extractor_id
    )
    proposals += _live_proposals(live, envelope, layer, procedure)
    verifications = verify_proposals(
        proposals,
        layer,
        config=config,
        known_fields=known_fields,
        taken_fields=frozenset(record.field for record in records),
    )
    text_records: list[ExtractionRecord] = [
        outcome.record for outcome in verifications if outcome.record is not None
    ]
    discarded = len(mapper_discarded) + sum(
        1 for outcome in verifications if not outcome.accepted
    )
    # A field the mapper could not fill and the text could: it is no longer a
    # loss, so it stops counting as one. Without this the same field would push
    # toward tier 3 while its value sat in the evidence record.
    recovered = sum(
        1 for record in text_records if record.field in set(mapper_discarded)
    )
    extractions = ExtractionSet(
        envelope_id=envelope.envelope_id,
        case_id=envelope.case_id,
        procedure_id=procedure_id,
        records=records + text_records,
        discarded_count=max(0, discarded - recovered),
        created_at=now or datetime.now(UTC),
        versions=versions,
    )
    outcome = ExtractionOutcome(
        extractions=extractions,
        verifications=verifications,
        replay=replay,
        mapper_discarded=tuple(mapper_discarded),
    )
    _journal(outcome, envelope, journal=journal, versions=versions, now=now)
    return outcome


def _live_proposals(
    live: TextExtractor | None,
    envelope: Envelope,
    layer: TextLayer | None,
    procedure: ProcedureConfig | None,
) -> tuple[Proposal, ...]:
    if live is None or layer is None:
        return ()
    fields = field_descriptions(procedure)
    if not fields:
        return ()
    proposals: list[Proposal] = []
    for part in layer.parts:
        proposals.extend(
            live.propose(part_id=part.part_id, text=part.normalized_text, fields=fields)
        )
    return tuple(proposals)


def _journal(
    outcome: ExtractionOutcome,
    envelope: Envelope,
    *,
    journal: JournalStore,
    versions: VersionStamp,
    now: datetime | None,
) -> None:
    extractions = outcome.extractions
    extractor_ids = sorted(
        {record.extractor_id for record in extractions.records} | {MAPPER_ID}
    )
    emit(
        journal,
        case_id=envelope.case_id,
        event_type=EventType.EXTRACTED,
        versions=versions,
        occurred_at=now,
        payload={
            "envelope_id": envelope.envelope_id,
            "procedure_id": extractions.procedure_id,
            "extractor_ids": extractor_ids,
            "fields": [record.field for record in extractions.records],
            "record_count": len(extractions.records),
            "discarded_count": extractions.discarded_count,
            "discarded_fields": list(outcome.mapper_discarded),
            "text_part_ids": [part.part_id for part in text_parts(envelope)],
            "verification": outcome.stats(),
            # COORDINATES, never values (part 10). The review UI has to show a
            # caseworker where each field came from - that is the double lock
            # of ADR-020 becoming visible to the human it exists for - and a
            # half-open character range in a named part is not personal data,
            # while the quote it slices out could be. The quote stays in the
            # working copy, which the journal deliberately does not carry.
            "spans": [
                {
                    "field": record.field,
                    "part_id": record.span.part_id if record.span else None,
                    "start": record.span.start if record.span else None,
                    "end": record.span.end if record.span else None,
                    "match_mode": record.match_mode.value,
                    "match_score": record.match_score,
                    "confidence": record.confidence,
                    "extractor_id": record.extractor_id,
                }
                for record in extractions.records
            ],
        },
    )
