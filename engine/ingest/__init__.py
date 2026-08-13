"""Channel adapters: turn an inbound item into the one internal Envelope."""

from engine.ingest.envelope import (
    TEXT_PART_PREFIX,
    FitConnectSubmission,
    IngestResult,
    RawTextPart,
    build_envelope,
    build_ingest,
    case_id_for,
    ingest_submission,
    structured_payload,
    text_parts_of,
)

__all__ = [
    "TEXT_PART_PREFIX",
    "FitConnectSubmission",
    "IngestResult",
    "RawTextPart",
    "build_envelope",
    "build_ingest",
    "case_id_for",
    "ingest_submission",
    "structured_payload",
    "text_parts_of",
]
