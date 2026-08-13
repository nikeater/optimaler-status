"""Extraction: values with provenance, or no value at all.

Three readers and one verifier. The schema mapper reads a JSON key and gets
``MatchMode.STRUCTURED`` with confidence 1.0, because there is nothing
probabilistic about reading a key. The replay extractor and the live LLM client
read prose and get nothing at all until :mod:`engine.extract.verify` has checked
their quote and their offset against the normalized layer, independently (P-8).

The verifier cannot tell which extractor produced a proposal, and that is the
property the whole design rests on: it is the reason a language model may be
called here at all.
"""

from engine.extract.llm import (
    PROPOSAL_SCHEMA,
    LiveExtractionError,
    LiveExtractor,
    LiveSettings,
    chunk_text,
    parse_answer,
    settings_from_policy,
)
from engine.extract.mapper import EXTRACTOR_ID, map_payload, resolve_path
from engine.extract.orchestrate import (
    ExtractionOutcome,
    TextExtractor,
    extract_all,
    field_descriptions,
)
from engine.extract.proposal import Proposal
from engine.extract.replay import (
    FIXTURE_KEY,
    FixtureEntry,
    ReplayStats,
    fixture_from_payload,
    replay_proposals,
)
from engine.extract.selection import (
    EXTRACTOR_ENV,
    EXTRACTOR_MODEL_ENV,
    EXTRACTOR_URL_ENV,
    LIVE,
    MODES,
    REPLAY,
    ExtractorPosture,
    ExtractorSelectionError,
    build_extractor,
)
from engine.extract.verify import (
    FailureKind,
    Verification,
    match_score,
    value_in_quote,
    verify_proposal,
    verify_proposals,
)

__all__ = [
    "EXTRACTOR_ENV",
    "EXTRACTOR_ID",
    "EXTRACTOR_MODEL_ENV",
    "EXTRACTOR_URL_ENV",
    "FIXTURE_KEY",
    "LIVE",
    "MODES",
    "PROPOSAL_SCHEMA",
    "REPLAY",
    "ExtractionOutcome",
    "ExtractorPosture",
    "ExtractorSelectionError",
    "FailureKind",
    "FixtureEntry",
    "LiveExtractionError",
    "LiveExtractor",
    "LiveSettings",
    "Proposal",
    "ReplayStats",
    "TextExtractor",
    "Verification",
    "build_extractor",
    "chunk_text",
    "extract_all",
    "field_descriptions",
    "fixture_from_payload",
    "map_payload",
    "match_score",
    "parse_answer",
    "replay_proposals",
    "resolve_path",
    "settings_from_policy",
    "value_in_quote",
    "verify_proposal",
    "verify_proposals",
]
