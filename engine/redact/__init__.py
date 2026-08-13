"""The privacy boundary: seal identity at ingest, verify what passes.

Two planes meet here. Everything upstream of :func:`redact_payload` may hold raw
identity data; nothing downstream of it does. The envelope's documented
invariant - "carries ONLY redacted content" - is computed by this package rather
than asserted by a comment (ADR-002, ADR-017, ADR-018).

Entry points, in the order a submission meets them:

* :func:`engine.redact.policy.load_policy` - which paths are identity-classed,
* :func:`redact_payload` - seal, verify, auto-seal once, or refuse,
* :class:`Witness` - the request-scoped resolution the deterministic plane uses
  instead of dereferencing the vault,
* :class:`VaultStore` - durable sealed storage, read at render time only.

``engine.redact`` imports cleanly WITHOUT the optional ``[redact]`` extra. The
deterministic recognizers are the floor; Presidio and spaCy join the union when
they are installed (:mod:`engine.redact.ner`) and the recall metric reports
which of the two numbers you are looking at.
"""

from engine.redact.boundary import (
    AUTOSEAL_KIND,
    TEXT_PATH_PREFIX,
    RedactionOutcome,
    RedactionRefusedError,
    redact_payload,
)
from engine.redact.detector import Detector, merge, redact_detector, verify_detector
from engine.redact.placeholders import (
    ALPHABET,
    PLACEHOLDER_RE,
    TOKEN_LENGTH,
    Kind,
    Placeholder,
    PlaceholderError,
    PlaceholderRegistry,
    SecretsTokenSource,
    SeededTokenSource,
    TokenSource,
    contains_placeholder,
    find_placeholders,
    format_placeholder,
    parse_placeholder,
)
from engine.redact.policy import (
    IdentityField,
    IdentityFieldsPolicy,
    PolicyError,
    Reveal,
    check_witnessless_seals,
    default_policy,
    load_policy,
)
from engine.redact.recognizers import (
    RECOGNIZERS,
    Detection,
    Evidence,
    Profile,
    Recognizer,
    iban_checksum_ok,
    steuer_id_checksum_ok,
    vsnr_checksum_ok,
)
from engine.redact.seal import (
    EMPTY_WITNESS,
    SealOutcome,
    Witness,
    scalar_text,
    seal_leaf,
    seal_payload,
    walk_strings,
)
from engine.redact.text import (
    SealedText,
    seal_text,
    seal_texts,
    text_seal_detector,
)
from engine.redact.vault import (
    DuplicateVaultRecordError,
    InMemoryVaultStore,
    JsonlVaultStore,
    SealedEntry,
    UnknownVaultRefError,
    VaultRecord,
    VaultStore,
)
from engine.redact.verify import (
    Finding,
    VerificationReport,
    mask_placeholders,
    merge_reports,
    sweep_texts,
    verify_payload,
    verify_texts,
)

__all__ = [
    "ALPHABET",
    "AUTOSEAL_KIND",
    "EMPTY_WITNESS",
    "PLACEHOLDER_RE",
    "RECOGNIZERS",
    "TEXT_PATH_PREFIX",
    "TOKEN_LENGTH",
    "Detection",
    "Detector",
    "DuplicateVaultRecordError",
    "Evidence",
    "Finding",
    "IdentityField",
    "IdentityFieldsPolicy",
    "InMemoryVaultStore",
    "JsonlVaultStore",
    "Kind",
    "Placeholder",
    "PlaceholderError",
    "PlaceholderRegistry",
    "PolicyError",
    "Profile",
    "Recognizer",
    "RedactionOutcome",
    "RedactionRefusedError",
    "Reveal",
    "SealOutcome",
    "SealedEntry",
    "SealedText",
    "SecretsTokenSource",
    "SeededTokenSource",
    "TokenSource",
    "UnknownVaultRefError",
    "VaultRecord",
    "VaultStore",
    "VerificationReport",
    "Witness",
    "check_witnessless_seals",
    "contains_placeholder",
    "default_policy",
    "find_placeholders",
    "format_placeholder",
    "iban_checksum_ok",
    "load_policy",
    "mask_placeholders",
    "merge",
    "merge_reports",
    "parse_placeholder",
    "redact_detector",
    "redact_payload",
    "scalar_text",
    "seal_leaf",
    "seal_payload",
    "seal_text",
    "seal_texts",
    "steuer_id_checksum_ok",
    "sweep_texts",
    "text_seal_detector",
    "verify_detector",
    "verify_payload",
    "verify_texts",
    "vsnr_checksum_ok",
    "walk_strings",
]
