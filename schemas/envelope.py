"""Internal envelope: the one shape every channel adapter must produce.

The envelope carries ONLY redacted content. Sealed identity lives in the
vault and is referenced by vault_ref; nothing un-redacted passes this point.
"""

from __future__ import annotations

from pydantic import Field

from .common import Channel, SourceType, Stamped, StrictModel


class RawRef(StrictModel):
    """Pointer to an original inbound artifact kept outside the model path."""

    ref_id: str
    media_type: str = Field(description="e.g. application/json, application/pdf")
    filename: str | None = None
    sha256: str | None = None


class ContentPart(StrictModel):
    """One redacted, model-visible unit of content (body, attachment text)."""

    part_id: str
    source_type: SourceType
    media_type: str = "text/plain"
    redacted_text: str | None = Field(
        default=None, description="Redacted free text; None for structured parts"
    )
    structured_payload: dict[str, object] | None = Field(
        default=None,
        description="Redacted structured payload (FIM/XOEV-shaped JSON); "
        "None for free-text parts",
    )


class Envelope(Stamped):
    """The normalized inbound item after ingest + redaction."""

    envelope_id: str
    case_id: str = Field(description="Journal aggregate id")
    channel: Channel
    procedure_hint: str | None = Field(
        default=None, description="Procedure id claimed by the channel, if any"
    )
    raw_refs: list[RawRef] = Field(default_factory=list)
    vault_ref: str = Field(
        description="Opaque handle to the sealed identity record; the vault "
        "is only dereferenced at outbound template rendering"
    )
    parts: list[ContentPart] = Field(min_length=1)
    redaction_verified: bool = Field(
        description="True only after the post-redaction verification pass "
        "(second detector over redacted text) found nothing"
    )
