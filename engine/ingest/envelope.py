"""FIT-Connect payload adapter: fixture JSON to Envelope, plus RECEIVED.

The envelope is the one shape every later stage sees, so the adapter is where
channel quirks stop. The submission's ``data`` object becomes a structured
ContentPart; from part 05 on, ``bodyText`` and any attachment carrying extracted
``text`` become free-text ContentParts with ``redacted_text`` set and a
``sourceType`` that decides how spans are matched later (born-digital exact,
OCR bounded-fuzzy).

The e-mail and scan channels ride this same submission shape on purpose. A real
IMAP/MIME adapter and a real FIT-Connect event-log client are part 07 work
(backlog P-14): what part 05 needs is the ENVELOPE shape a text item produces,
and inventing a second inbound format to get it would have meant writing the
adapter twice. A channel adapter that speaks MIME will produce exactly this
dictionary.

Part 04 moved the privacy boundary in front of the envelope. Identity-classed
payload paths are sealed BEFORE the Envelope object is constructed, so the
contract's documented invariant - "carries ONLY redacted content; nothing
un-redacted passes this point" - is true rather than vacuously true. Two
hard-codes part 01 flagged as "the single place that must change" are gone with
it: ``vault_ref`` is a minted opaque handle that cannot be derived from the
submission id, and ``redaction_verified`` is exactly what the post-redaction
sweep computed.

What ingest hands on besides the envelope is the transient WITNESS: the
in-memory ``placeholder -> value`` mapping that lets the deterministic plane
keep validating real values (ADR-017). It rides on :class:`IngestResult`, not on
the envelope, because the envelope is serialized and the witness must never be.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engine.journal.store import JournalStore, emit
from engine.redact import (
    Detector,
    IdentityFieldsPolicy,
    InMemoryVaultStore,
    TokenSource,
    VaultStore,
    Witness,
    default_policy,
    redact_payload,
)
from schemas.common import Channel, SourceType, VersionStamp
from schemas.envelope import ContentPart, Envelope, RawRef
from schemas.events import EventType

STRUCTURED_PART_ID = "part-structured-0"

#: Free-text parts are numbered in arrival order: body first, then attachments.
TEXT_PART_PREFIX = "part-text-"

#: Channels whose text is machine-read rather than machine-written. A scanned
#: letter is OCR unless the submission says otherwise; an e-mail body is not.
OCR_CHANNELS = frozenset({Channel.SCAN})


class FitConnectSubmission(BaseModel):
    """The subset of a FIT-Connect submission the adapter reads.

    Tolerant on purpose (``extra="ignore"``): real submissions carry metadata
    the engine has no business interpreting. Both camelCase (wire) and
    snake_case (fixtures, tests) spellings are accepted.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    submission_id: str = Field(alias="submissionId")
    destination_id: str | None = Field(default=None, alias="destinationId")
    procedure_hint: str | None = Field(default=None, alias="procedureHint")
    channel: Channel = Channel.FIT_CONNECT
    submitted_at: datetime | None = Field(default=None, alias="submittedAt")
    data: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    body_text: str | None = Field(
        default=None,
        alias="bodyText",
        description="Free text of the item itself: an e-mail body, the covering "
        "letter of a scan. Raw at this point; sealed before the envelope exists",
    )
    body_source_type: SourceType | None = Field(
        default=None,
        alias="bodySourceType",
        description="Overrides the channel default for the body part",
    )


@dataclass(frozen=True)
class RawTextPart:
    """One free-text part before the boundary has seen it."""

    part_id: str
    source_type: SourceType
    text: str


@dataclass(frozen=True)
class IngestResult:
    """What ingest produced, in two very different lifetimes.

    ``envelope`` is durable and serialized everywhere. ``witness`` is not: it
    exists for this request, is handed straight to the completeness checker and
    is never written, journaled or returned by the API. Everything else here is
    value-free metadata about the seal.
    """

    envelope: Envelope
    witness: Witness
    vault_ref: str
    sealed_count: int
    redaction_verified: bool
    auto_sealed_paths: tuple[str, ...] = ()
    text_sealed_counts: dict[str, int] = dc_field(default_factory=dict)

    @property
    def text_sealed_count(self) -> int:
        """How many identity spans were sealed out of prose."""
        return sum(self.text_sealed_counts.values())

    def summary(self) -> dict[str, Any]:
        """Value-free seal metadata, for the RECEIVED payload and for tests."""
        return {
            "vault_ref": self.vault_ref,
            "sealed_count": self.sealed_count,
            "redaction_verified": self.redaction_verified,
            "auto_sealed_paths": list(self.auto_sealed_paths),
            "text_sealed_counts": dict(sorted(self.text_sealed_counts.items())),
        }


def build_ingest(
    payload: Mapping[str, Any],
    *,
    versions: VersionStamp,
    vault: VaultStore | None = None,
    policy: IdentityFieldsPolicy | None = None,
    token_source: TokenSource | None = None,
    now: datetime | None = None,
    text_detector: Detector | None = None,
) -> IngestResult:
    """Seal one inbound submission and build its envelope.

    Args:
        payload: the raw submission as received.
        versions: config provenance stamped onto the envelope.
        vault: where the sealed record goes. Defaults to a request-scoped
            in-memory store, which is right for tests and eval runs and never
            right for production - ``api/app.py`` always passes one. A
            discarded vault costs nothing today and makes part 08's
            re-hydration fail loudly rather than silently, which is the
            defensive direction.
        policy: identity-fields policy; defaults to the configured one.
        token_source: placeholder token source; defaults to ``secrets``.
        now: fixed clock for tests.
        text_detector: union that seals free text. Defaults to the
            deterministic REDACT union, which is what every gate path uses;
            production passes one with the optional NER member.

    Raises:
        RedactionRefusedError: when the working copy could not be verified
            clean. Raised before any journal event exists.
    """
    submission = FitConnectSubmission.model_validate(dict(payload))
    created_at = submission.submitted_at or now or datetime.now(UTC)
    raw_parts = text_parts_of(submission)
    outcome = redact_payload(
        submission.data,
        policy=policy if policy is not None else default_policy(),
        case_id=case_id_for(submission.submission_id),
        created_at=created_at,
        token_source=token_source,
        texts={part.part_id: part.text for part in raw_parts},
        text_detector=text_detector,
    )
    store = vault if vault is not None else InMemoryVaultStore()
    store.seal(outcome.record)

    raw_refs = [
        RawRef(
            ref_id=submission.submission_id,
            media_type="application/json",
            # The digest is taken over the submission AS RECEIVED: a RawRef
            # points at the original artifact, and a hash of the redacted copy
            # would identify the wrong thing. A SHA-256 is not a value.
            sha256=_sha256(submission.data),
        )
    ]
    raw_refs.extend(_attachment_refs(submission))
    parts = [
        ContentPart(
            part_id=STRUCTURED_PART_ID,
            source_type=SourceType.BORN_DIGITAL,
            media_type="application/json",
            redacted_text=None,
            structured_payload=outcome.payload,
        )
    ]
    parts.extend(
        ContentPart(
            part_id=raw.part_id,
            source_type=raw.source_type,
            media_type="text/plain",
            # The REDACTED text, and only ever that: the raw letter stays behind
            # raw_refs and in the vault. This is the first ContentPart in the
            # project whose redacted_text is not None (ADR-019).
            redacted_text=outcome.texts.get(raw.part_id, ""),
            structured_payload=None,
        )
        for raw in raw_parts
    )
    envelope = Envelope(
        envelope_id=f"env-{submission.submission_id}",
        case_id=case_id_for(submission.submission_id),
        channel=submission.channel,
        procedure_hint=submission.procedure_hint,
        raw_refs=raw_refs,
        vault_ref=outcome.vault_ref,
        parts=parts,
        redaction_verified=outcome.verified,
        created_at=created_at,
        versions=versions,
    )
    return IngestResult(
        envelope=envelope,
        witness=outcome.witness,
        vault_ref=outcome.vault_ref,
        sealed_count=outcome.sealed_count,
        redaction_verified=outcome.verified,
        auto_sealed_paths=outcome.auto_sealed_paths,
        text_sealed_counts=dict(outcome.text_sealed_counts),
    )


def text_parts_of(submission: FitConnectSubmission) -> tuple[RawTextPart, ...]:
    """Every free-text part of a submission, body first, in arrival order.

    Blank text produces no part: an e-mail with an empty body and one attachment
    is one text part, not two, and a ``TextLayerPart`` over "" would carry an
    offset map that covers nothing and a normalized text nothing can be found in.
    """
    parts: list[RawTextPart] = []
    if submission.body_text and submission.body_text.strip():
        parts.append(
            RawTextPart(
                part_id=f"{TEXT_PART_PREFIX}{len(parts)}",
                source_type=_source_type(
                    submission.body_source_type, submission.channel
                ),
                text=submission.body_text,
            )
        )
    for attachment in submission.attachments:
        text = attachment.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        parts.append(
            RawTextPart(
                part_id=f"{TEXT_PART_PREFIX}{len(parts)}",
                source_type=_source_type(
                    _attachment_source_type(attachment), submission.channel
                ),
                text=text,
            )
        )
    return tuple(parts)


def _source_type(declared: SourceType | None, channel: Channel) -> SourceType:
    """What a part says it is, else what its channel implies.

    Load-bearing rather than cosmetic: the source type selects EXACT or
    bounded-FUZZY span matching in the verifier, so calling scanned text
    born-digital would turn every OCR artefact into a discarded proposal, and
    calling e-mail text OCR would license fuzzy matching where exactness is free.
    """
    if declared is not None:
        return declared
    return SourceType.OCR if channel in OCR_CHANNELS else SourceType.BORN_DIGITAL


def _attachment_source_type(attachment: Mapping[str, Any]) -> SourceType | None:
    raw = attachment.get("sourceType") or attachment.get("source_type")
    if raw is None:
        return None
    try:
        return SourceType(str(raw))
    except ValueError:
        # An unknown source type is no reason to drop the text, and no reason to
        # claim "born digital" either: fall through to the channel default,
        # which is the more cautious of the two exactly where it matters.
        return None


def build_envelope(
    payload: Mapping[str, Any],
    *,
    versions: VersionStamp,
    vault: VaultStore | None = None,
    policy: IdentityFieldsPolicy | None = None,
    token_source: TokenSource | None = None,
    now: datetime | None = None,
) -> Envelope:
    """The envelope alone, for callers that do not validate sealed fields.

    Anything that runs the completeness checker needs :func:`build_ingest`
    instead: without the witness, a sealed value validates as a placeholder.
    """
    return build_ingest(
        payload,
        versions=versions,
        vault=vault,
        policy=policy,
        token_source=token_source,
        now=now,
    ).envelope


def ingest_submission(
    payload: Mapping[str, Any],
    *,
    journal: JournalStore,
    versions: VersionStamp,
    vault: VaultStore | None = None,
    policy: IdentityFieldsPolicy | None = None,
    token_source: TokenSource | None = None,
    now: datetime | None = None,
    text_detector: Detector | None = None,
) -> IngestResult:
    """Seal, build the envelope and record the RECEIVED event.

    Order matters: a submission the redaction boundary refuses raises before
    this function writes anything, so a refused item leaves no half-ingested
    case in the journal.
    """
    result = build_ingest(
        payload,
        versions=versions,
        vault=vault,
        policy=policy,
        token_source=token_source,
        now=now,
        text_detector=text_detector,
    )
    envelope = result.envelope
    emit(
        journal,
        case_id=envelope.case_id,
        event_type=EventType.RECEIVED,
        versions=envelope.versions,
        occurred_at=now,
        payload={
            "envelope_id": envelope.envelope_id,
            "channel": envelope.channel.value,
            "procedure_hint": envelope.procedure_hint,
            "part_ids": [part.part_id for part in envelope.parts],
            "raw_ref_ids": [ref.ref_id for ref in envelope.raw_refs],
            "vault_ref": envelope.vault_ref,
            "redaction_verified": envelope.redaction_verified,
            "sealed_count": result.sealed_count,
            "text_sealed_counts": dict(sorted(result.text_sealed_counts.items())),
            "part_source_types": [
                {"part_id": part.part_id, "source_type": part.source_type.value}
                for part in envelope.parts
            ],
            "auto_sealed_paths": list(result.auto_sealed_paths),
            # VersionStamp has no field for the redaction policy and schemas are
            # contracts, so the policy id travels in the payload (ADR-017).
            "redaction_policy_id": (
                policy.policy_id if policy is not None else default_policy().policy_id
            ),
        },
    )
    return result


def case_id_for(submission_id: str) -> str:
    """Journal aggregate id for a submission id."""
    return f"case-{submission_id}"


def structured_payload(envelope: Envelope) -> dict[str, Any]:
    """The first structured payload of an envelope, or an empty mapping."""
    for part in envelope.parts:
        if part.structured_payload is not None:
            return dict(part.structured_payload)
    return {}


def _attachment_refs(submission: FitConnectSubmission) -> list[RawRef]:
    refs: list[RawRef] = []
    for index, attachment in enumerate(submission.attachments):
        ref_id = attachment.get("id") or f"{submission.submission_id}-att-{index}"
        refs.append(
            RawRef(
                ref_id=str(ref_id),
                media_type=str(attachment.get("mediaType", "application/octet-stream")),
                filename=_optional_str(attachment.get("filename")),
                sha256=_optional_str(attachment.get("sha256")),
            )
        )
    return refs


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _sha256(data: Mapping[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
