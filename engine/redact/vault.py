"""The sealed identity vault: protocol plus two dev backends.

Deliberately the same shape as ``engine.journal.store`` (ADR-008), for the same
reason: PostgreSQL is the production store, it arrives with the compose profile
in a later part, and nothing in the pipeline may be coupled to which backend it
is writing to. What is enforced here rather than promised:

* a ``vault_ref`` may be sealed exactly once (append-only; correcting a record
  means a new one, never an overwrite),
* the file backend refuses references that are not filesystem-safe,
* :meth:`VaultStore.fetch` is documented, in one place, as render-time only.

**Reading the vault is part 08's job and nobody else's.** ADR-002 puts
re-hydration strictly at outbound template rendering, round-trip checked, and
parts 04 to 07 never call ``fetch`` outside tests. The deterministic plane gets
what it needs from the request-scoped witness (:mod:`engine.redact.seal`), which
is not a vault dereference and never touches this module.

**No home-rolled crypto.** ``JsonlVaultStore`` writes plaintext JSON and says so
in its docstring, in ``docs/vault-dpia-input.md`` and in the file it writes: it
is a dev and test backend holding synthetic data. Encryption at rest is a
property of the production PostgreSQL deployment, and inventing a file cipher
here would produce something that looks protected and is not - the worse of the
two failure modes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from engine.redact.placeholders import Kind

_SAFE_VAULT_REF = re.compile(r"^[A-Za-z0-9._-]{1,120}$")

#: Written into every JSONL vault file so nobody mistakes it for a store that
#: protects anything.
DEV_BACKEND_NOTICE = (
    "dev backend, plaintext, synthetic data only - encryption at rest is a "
    "property of the production PostgreSQL vault (docs/vault-dpia-input.md)"
)


class VaultError(RuntimeError):
    """Base class for vault problems."""


class DuplicateVaultRecordError(VaultError):
    """Raised when a vault_ref is sealed a second time."""


class UnknownVaultRefError(VaultError):
    """Raised when a vault_ref has no record."""


@dataclass(frozen=True)
class SealedEntry:
    """One value that left the working copy.

    ``value_json`` holds the value AS RECEIVED (JSON-encoded, so a number stays
    a number and the address subtree stays an object). Part 08 normalizes for
    rendering; the vault stores the truth, not a cleaned-up version of it.
    """

    kind: Kind
    token: str
    value_json: str
    path: str | None = None
    part_id: str | None = None
    span: tuple[int, int] | None = None

    @property
    def placeholder(self) -> str:
        """The placeholder that stands in this entry's place."""
        return f"[[PII|{self.kind.value}|{self.token}]]"

    def value(self) -> Any:
        """The sealed value, decoded. Render-time only, like ``fetch``."""
        return json.loads(self.value_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "token": self.token,
            "value_json": self.value_json,
            "path": self.path,
            "part_id": self.part_id,
            "span": list(self.span) if self.span is not None else None,
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> SealedEntry:
        span = document.get("span")
        return cls(
            kind=Kind(document["kind"]),
            token=str(document["token"]),
            value_json=str(document["value_json"]),
            path=document.get("path"),
            part_id=document.get("part_id"),
            span=(int(span[0]), int(span[1])) if span else None,
        )


@dataclass(frozen=True)
class VaultRecord:
    """Everything one case sealed, under one opaque handle."""

    vault_ref: str
    case_id: str
    created_at: datetime
    entries: tuple[SealedEntry, ...] = ()

    @property
    def tokens(self) -> frozenset[str]:
        """Every token this record can resolve."""
        return frozenset(entry.token for entry in self.entries)

    def entry_for(self, token: str) -> SealedEntry | None:
        """The entry a token resolves to, or None."""
        for entry in self.entries:
            if entry.token == token:
                return entry
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "vault_ref": self.vault_ref,
            "case_id": self.case_id,
            "created_at": self.created_at.isoformat(),
            "notice": DEV_BACKEND_NOTICE,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> VaultRecord:
        return cls(
            vault_ref=str(document["vault_ref"]),
            case_id=str(document["case_id"]),
            created_at=datetime.fromisoformat(str(document["created_at"])),
            entries=tuple(
                SealedEntry.from_dict(entry) for entry in document.get("entries", [])
            ),
        )

    def summary(self) -> dict[str, Any]:
        """Value-free description, safe for a journal payload or a log line."""
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.kind.value] = counts.get(entry.kind.value, 0) + 1
        return {
            "vault_ref": self.vault_ref,
            "entry_count": len(self.entries),
            "kinds": dict(sorted(counts.items())),
        }


@runtime_checkable
class VaultStore(Protocol):
    """The storage contract every vault backend implements."""

    def seal(self, record: VaultRecord) -> VaultRecord:
        """Store one record; raise when its vault_ref already exists."""
        ...

    def fetch(self, vault_ref: str) -> VaultRecord:
        """Read a sealed record.

        RENDER TIME ONLY (ADR-002, ADR-017). The only production caller is the
        outbound template renderer of part 08, which round-trip checks every
        placeholder it resolves and treats an unknown one as a hard error that
        blocks the draft. Nothing in the evidence plane, the decision plane, the
        journal, the API or the eval harness calls this.
        """
        ...

    def exists(self, vault_ref: str) -> bool:
        """Whether a record is stored under this handle."""
        ...


class InMemoryVaultStore:
    """Process-local vault: the default for tests, eval runs and dev."""

    def __init__(self) -> None:
        self._records: dict[str, VaultRecord] = {}

    def seal(self, record: VaultRecord) -> VaultRecord:
        if record.vault_ref in self._records:
            raise DuplicateVaultRecordError(
                f"vault_ref {record.vault_ref} is already sealed"
            )
        self._records[record.vault_ref] = record
        return record

    def fetch(self, vault_ref: str) -> VaultRecord:
        """Read a sealed record. Render-time only; see :class:`VaultStore`."""
        try:
            return self._records[vault_ref]
        except KeyError as error:
            raise UnknownVaultRefError(f"unknown vault_ref: {vault_ref}") from error

    def exists(self, vault_ref: str) -> bool:
        return vault_ref in self._records

    def refs(self) -> list[str]:
        """Every handle this store knows; a test and dev convenience."""
        return sorted(self._records)


class JsonlVaultStore:
    """File-backed vault: one JSON file per vault_ref.

    PLAINTEXT, and that is a deliberate, documented limitation rather than an
    oversight: this backend holds synthetic data in development. Production
    storage is the encrypted-at-rest PostgreSQL vault that arrives with the
    deploy part; a home-rolled file cipher would look like protection without
    being any.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def seal(self, record: VaultRecord) -> VaultRecord:
        path = self._path(record.vault_ref)
        if path.exists():
            raise DuplicateVaultRecordError(
                f"vault_ref {record.vault_ref} is already sealed"
            )
        path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return record

    def fetch(self, vault_ref: str) -> VaultRecord:
        """Read a sealed record. Render-time only; see :class:`VaultStore`."""
        path = self._path(vault_ref)
        if not path.is_file():
            raise UnknownVaultRefError(f"unknown vault_ref: {vault_ref}")
        document: Any = json.loads(path.read_text(encoding="utf-8"))
        return VaultRecord.from_dict(document)

    def exists(self, vault_ref: str) -> bool:
        return self._path(vault_ref).is_file()

    def refs(self) -> list[str]:
        """Every handle on disk."""
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def _path(self, vault_ref: str) -> Path:
        if not _SAFE_VAULT_REF.match(vault_ref):
            raise ValueError(
                f"vault_ref {vault_ref!r} is not filesystem-safe; allowed: "
                "letters, digits, dot, underscore, hyphen (max 120 chars)"
            )
        return self.directory / f"{vault_ref}.json"


def build_record(
    *,
    vault_ref: str,
    case_id: str,
    created_at: datetime,
    entries: Sequence[SealedEntry] | Iterable[SealedEntry],
) -> VaultRecord:
    """Assemble a record; the one constructor the seal path uses."""
    return VaultRecord(
        vault_ref=vault_ref,
        case_id=case_id,
        created_at=created_at,
        entries=tuple(entries),
    )
