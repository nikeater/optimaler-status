"""Which payload paths are identity-classed, and what that implies.

One file (``config/redaction/identity_fields_v1.yaml``) is the single source of
truth for three separate questions that must never be answered differently:

* **what gets sealed** at ingest (:mod:`engine.redact.seal`),
* **which sealed values the deterministic plane may still compute on** through
  the transient witness (``witness: true``),
* **whose observed value may appear in a validation problem string**
  (``reveal: never``), read by :mod:`engine.evidence.completeness`.

Splitting those across three lists is how classification drifts: a field gets
added to the seal list, nobody adds it to the visibility list, and the invalid
value it holds goes into the journal in a problem text. Here they are one row.

The loader is deliberately picky. A path that is sealed WITHOUT a witness entry
and is also mapped by a procedure's ``field_map`` is refused, because that
combination silently turns a real validation into a validation of a random
token. No shipped config does this today; the check keeps it that way.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from engine.redact.placeholders import Kind
from schemas.common import StrictModel

CONFIG_DIR_ENV = "EINGANGSLOTSE_CONFIG_DIR"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"
REDACTION_SUBDIR = "redaction"


class PolicyError(ValueError):
    """Raised when the redaction policy on disk is unusable."""


class Reveal(StrEnum):
    """Whether the observed value of a field may be quoted back at a human.

    ``never`` is the only value an identity-classed field may carry today; the
    enum exists so that a future non-identity entry (say a field that is sealed
    for retention reasons but harmless to quote) has somewhere to say so
    explicitly rather than by omission.
    """

    NEVER = "never"
    ALWAYS = "always"


class IdentityField(StrictModel):
    """One identity-classed payload path."""

    path: str = Field(description="Dotted path into the structured payload")
    kind: Kind = Field(description="Placeholder kind the sealed value gets")
    subtree: bool = Field(
        default=False,
        description="Seal the whole sub-object into ONE entry and replace it "
        "with a single placeholder string",
    )
    witness: bool = Field(
        default=True,
        description="Whether the deterministic plane may resolve this value "
        "through the request-scoped witness (never through the vault)",
    )
    reveal: Reveal = Field(
        default=Reveal.NEVER,
        description="Whether the observed value may appear in problem texts",
    )
    note: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> IdentityField:
        if not self.path.strip():
            raise ValueError("an identity field needs a path")
        if self.subtree and self.witness:
            # A subtree seals a whole object into one entry; there is no scalar
            # for a validator to compute on, so a witness entry would be a
            # promise the seal cannot keep.
            raise ValueError(
                f"{self.path}: a subtree seal cannot participate in the witness"
            )
        return self

    def covers(self, path: str) -> bool:
        """Whether ``path`` is this field or (for a subtree) sits under it."""
        if path == self.path:
            return True
        return self.subtree and path.startswith(f"{self.path}.")


class IdentityFieldsPolicy(StrictModel):
    """The whole of ``config/redaction/identity_fields_v1.yaml``."""

    policy_id: str
    version: str
    description: str | None = None
    fields: list[IdentityField] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_and_disjoint(self) -> IdentityFieldsPolicy:
        seen: set[str] = set()
        for field in self.fields:
            if field.path in seen:
                raise ValueError(f"duplicate identity path {field.path!r}")
            seen.add(field.path)
        for field in self.fields:
            for other in self.fields:
                if other is field:
                    continue
                if field.path.startswith(f"{other.path}."):
                    raise ValueError(
                        f"identity path {field.path!r} sits under {other.path!r}; "
                        "nested identity paths make sealing order load-bearing"
                    )
        return self

    @property
    def paths(self) -> tuple[str, ...]:
        """Every identity-classed path, in file order."""
        return tuple(field.path for field in self.fields)

    def field_at(self, path: str) -> IdentityField | None:
        """The entry declaring ``path`` exactly, or None."""
        for field in self.fields:
            if field.path == path:
                return field
        return None

    def covering(self, path: str) -> IdentityField | None:
        """The entry that seals ``path`` (exactly or as part of a subtree)."""
        for field in self.fields:
            if field.covers(path):
                return field
        return None

    def sealed_field_ids(self, field_paths: Mapping[str, str]) -> frozenset[str]:
        """Procedure field ids whose payload path this policy seals.

        ``field_paths`` is a procedure's ``field id -> payload path`` map, so
        the answer is per procedure: the same policy makes ``geburtsdatum``
        sealed in every procedure that reads it from ``antragsteller.*``.
        """
        return frozenset(
            field_id
            for field_id, path in field_paths.items()
            if self.covering(path) is not None
        )

    def value_free_field_ids(self, field_paths: Mapping[str, str]) -> frozenset[str]:
        """Procedure field ids whose observed value may never be quoted."""
        return frozenset(
            field_id
            for field_id, path in field_paths.items()
            if (entry := self.covering(path)) is not None
            and entry.reveal is Reveal.NEVER
        )

    def witness_paths(self) -> tuple[str, ...]:
        """Sealed paths whose value the witness carries."""
        return tuple(field.path for field in self.fields if field.witness)


def load_policy(config_dir: Path | str | None = None) -> IdentityFieldsPolicy:
    """Load and validate the identity-fields policy.

    Args:
        config_dir: config root to read; defaults to ``$EINGANGSLOTSE_CONFIG_DIR``
            or the repo's ``config/``.
    """
    directory = Path(config_dir) if config_dir is not None else _default_config_dir()
    return _load_policy_cached(directory.resolve())


def default_policy() -> IdentityFieldsPolicy:
    """The policy for the currently configured config directory."""
    return load_policy(None)


@lru_cache(maxsize=8)
def _load_policy_cached(directory: Path) -> IdentityFieldsPolicy:
    candidates = sorted((directory / REDACTION_SUBDIR).glob("*.yaml"))
    if len(candidates) != 1:
        raise PolicyError(
            f"expected exactly one redaction policy file in "
            f"{directory / REDACTION_SUBDIR}, found {[c.name for c in candidates]}"
        )
    document: Any = yaml.safe_load(candidates[0].read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PolicyError(f"{candidates[0]} must contain a YAML mapping")
    try:
        return IdentityFieldsPolicy.model_validate(document)
    except ValueError as error:
        raise PolicyError(f"{candidates[0]}: {error}") from error


def check_witnessless_seals(
    policy: IdentityFieldsPolicy, field_paths: Iterable[str]
) -> list[str]:
    """Paths a procedure maps that are sealed without a witness entry.

    An empty list is the healthy answer. A non-empty one means a requirement
    would be validated against a placeholder token instead of against the value
    the applicant sent, which is a silent false "valid" - the one failure mode a
    completeness checker must not have.
    """
    problems: list[str] = []
    for path in field_paths:
        entry = policy.covering(path)
        if entry is not None and not entry.witness:
            problems.append(
                f"{path} is sealed as {entry.kind.value} without a witness entry "
                f"but is mapped by a procedure field_map"
            )
    return problems


def _default_config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    return Path(override) if override else DEFAULT_CONFIG_DIR
