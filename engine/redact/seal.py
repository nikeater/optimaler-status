"""Split a structured payload into a working copy, a vault record and a witness.

Three artifacts leave this module and they have three different lifetimes:

``payload``  the working copy. Placeholders where identity used to be. This is
             what the envelope carries, what every later stage reads, what the
             journal records and what a model would ever see.
``entries``  the sealed values, on their way into the vault. Durable. Read only
             at outbound template rendering (part 08).
``witness``  ``placeholder -> scalar value``, in memory, this request only.
             Never serialized, never journaled, never on the envelope, never in
             the vault API, gone when the request ends.

The witness is the part that needs justifying. Sealing at ingest is the only
place the raw values legitimately exist, and the deterministic plane still has
to compute on them: a Versicherungsnummer has to be checked against the birth
date it encodes, a date has to be a real calendar date within its bounds. Doing
that against a random token would report "valid" for everything, which is the
one failure mode a completeness checker must not have; doing it by reading the
vault would break ADR-002's render-time-only rule. So ingest, which holds the
values for the instant of sealing anyway, hands the run an in-memory mapping and
nothing else ever sees it (ADR-017).

Two invariants that the gold set depends on and that the tests pin:

* **presence is preserved.** A path that is absent, null or blank is NOT sealed.
  Sealing it would replace "no answer" with a placeholder string, and every
  ``op: ne / value: null`` predicate and every MISSING gap in the system would
  change meaning.
* **the witness hands over exactly what the mapper would have produced.** The
  schema mapper strips whitespace and renders booleans; if the witness returned
  the raw string instead, a Versicherungsnummer submitted as ``" 17170459B012 "``
  would suddenly fail its format pattern. :func:`scalar_text` is the single
  definition, imported by ``engine.extract.mapper`` so the two cannot drift.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from engine.redact.placeholders import (
    Kind,
    Placeholder,
    PlaceholderRegistry,
    parse_placeholder,
)
from engine.redact.policy import IdentityField, IdentityFieldsPolicy
from engine.redact.vault import SealedEntry

#: Anything the sealer can write a placeholder into: an object's key or a list
#: element. Payloads are JSON, so those are the only two shapes there are.
type MutableContainer = dict[str, Any] | list[Any]


class Witness:
    """Transient ``placeholder -> value`` map for the deterministic plane.

    Not a dataclass and not a Mapping: both would give it a ``repr`` that prints
    its contents, and this object exists inside the one process boundary where
    raw identity values are still in memory. It answers exactly one question -
    "what does this placeholder stand for, right now, in this request" - and
    tells you nothing else, including in a traceback.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(values or {})

    def resolve(self, value: str) -> str | None:
        """The value behind a placeholder string, or None when unknown."""
        return self._values.get(value.strip())

    def knows(self, value: str) -> bool:
        """Whether this witness can resolve ``value``."""
        return value.strip() in self._values

    def merged(self, other: Witness) -> Witness:
        """One witness carrying both mappings.

        The only combiner there is, and it lives on the type rather than in the
        caller so that nothing outside this class ever enumerates the values.
        """
        combined = Witness(self._values)
        combined._values.update(other._values)
        return combined

    @property
    def tokens(self) -> frozenset[str]:
        """The tokens this witness carries, WITHOUT their values."""
        return frozenset(
            placeholder.token
            for key in self._values
            if (placeholder := parse_placeholder(key)) is not None
        )

    def __len__(self) -> int:
        return len(self._values)

    def __bool__(self) -> bool:
        return bool(self._values)

    def __repr__(self) -> str:
        return f"<Witness {len(self._values)} entries>"

    __str__ = __repr__


EMPTY_WITNESS = Witness()


@dataclass(frozen=True)
class SealOutcome:
    """What sealing one payload produced."""

    payload: dict[str, Any]
    entries: tuple[SealedEntry, ...] = ()
    witness: Witness = field(default_factory=Witness)
    sealed_paths: tuple[str, ...] = ()

    @property
    def sealed_count(self) -> int:
        """How many values left the working copy."""
        return len(self.entries)


def scalar_text(value: object) -> str | None:
    """Render a payload value as a field value; None means "not usable".

    The single definition of what a scalar payload leaf looks like as a string.
    ``engine.extract.mapper`` imports it, and so does the witness, because a
    validator must see the same string whether the value was sealed or not.
    """
    if value is None or isinstance(value, Mapping | list | tuple | set):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def seal_payload(
    payload: Mapping[str, Any],
    *,
    policy: IdentityFieldsPolicy,
    registry: PlaceholderRegistry,
) -> SealOutcome:
    """Seal every identity-classed path the policy declares.

    Idempotent: a payload that already carries a placeholder at a sealed path is
    left alone, so re-running this over a working copy seals nothing new.
    """
    working = _deep_copy(payload)
    entries: list[SealedEntry] = []
    witness: dict[str, str] = {}
    sealed: list[str] = []
    for entry in policy.fields:
        sealed_entry = _seal_one(working, entry, registry, witness)
        if sealed_entry is None:
            continue
        entries.append(sealed_entry)
        sealed.append(entry.path)
    return SealOutcome(
        payload=working,
        entries=tuple(entries),
        witness=Witness(witness),
        sealed_paths=tuple(sealed),
    )


def seal_leaf(
    payload: dict[str, Any],
    path: str,
    *,
    kind: Kind,
    registry: PlaceholderRegistry,
    witness: dict[str, str] | None = None,
) -> SealedEntry | None:
    """Seal one arbitrary leaf in place; used by the auto-seal sweep.

    Returns None when there is nothing to seal (absent, blank, or already a
    placeholder), so the caller can tell "handled" from "could not handle".
    """
    parent, key = _resolve_parent(payload, path)
    if parent is None or key is None:
        return None
    raw = parent[key]  # type: ignore[index]
    text = scalar_text(raw)
    if text is None or parse_placeholder(text) is not None:
        return None
    placeholder = registry.mint(kind)
    parent[key] = str(placeholder)  # type: ignore[index]
    if witness is not None:
        witness[str(placeholder)] = text
    return _entry(placeholder, raw, path)


def placeholder_tokens(payload: Mapping[str, Any]) -> list[Placeholder]:
    """Every placeholder that stands somewhere in a payload."""
    found: list[Placeholder] = []
    for _, value in walk_strings(payload):
        placeholder = parse_placeholder(value)
        if placeholder is not None:
            found.append(placeholder)
    return found


def walk_strings(
    payload: Mapping[str, Any], prefix: str = ""
) -> Iterator[tuple[str, str]]:
    """Yield ``(dotted path, value)`` for every string leaf of a payload.

    Lists are addressed with ``[i]`` so a finding can name exactly which element
    it came from. Nothing here reads a value's meaning; it only walks.
    """
    yield from _walk(payload, prefix)


def _walk(value: object, prefix: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk(child, path)
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            yield from _walk(child, f"{prefix}[{index}]")
    elif isinstance(value, str):
        yield prefix, value


def _seal_one(
    working: dict[str, Any],
    entry: IdentityField,
    registry: PlaceholderRegistry,
    witness: dict[str, str],
) -> SealedEntry | None:
    parent, key = _resolve_parent(working, entry.path)
    if not isinstance(parent, dict) or not isinstance(key, str):
        return None
    raw = parent[key]
    if entry.subtree and isinstance(raw, Mapping):
        # The whole sub-object becomes ONE entry and one placeholder string.
        # An empty object carries no identity, so it stays as it is.
        if not raw:
            return None
        placeholder = registry.mint(entry.kind)
        parent[key] = str(placeholder)
        return _entry(placeholder, raw, entry.path)
    text = scalar_text(raw)
    if text is None:
        # Absent, null or blank stays absent, null or blank. Sealing it would
        # invent a value where the applicant gave none, and every
        # `op: ne / value: null` predicate in the config would change meaning.
        return None
    if parse_placeholder(text) is not None:
        return None  # already sealed; sealing is idempotent
    placeholder = registry.mint(entry.kind)
    parent[key] = str(placeholder)
    if entry.witness:
        witness[str(placeholder)] = text
    return _entry(placeholder, raw, entry.path)


def _entry(placeholder: Placeholder, raw: object, path: str) -> SealedEntry:
    return SealedEntry(
        kind=placeholder.kind,
        token=placeholder.token,
        value_json=json.dumps(raw, ensure_ascii=False, default=str),
        path=path,
    )


_INDEX_RE = re.compile(r"\[(\d+)\]")


def path_steps(path: str) -> list[str | int]:
    """Split a walker path (``a.b[0].c``) into dict keys and list indices."""
    steps: list[str | int] = []
    for segment in path.split("."):
        name = _INDEX_RE.sub("", segment)
        if name:
            steps.append(name)
        steps.extend(int(match.group(1)) for match in _INDEX_RE.finditer(segment))
    return steps


def _resolve_parent(
    payload: dict[str, Any], path: str
) -> tuple[MutableContainer | None, str | int | None]:
    """The container holding ``path``'s leaf, plus the key or index of it."""
    steps = path_steps(path)
    if not steps:
        return None, None
    current: Any = payload
    for step in steps[:-1]:
        if isinstance(step, int):
            if not isinstance(current, list) or not 0 <= step < len(current):
                return None, None
        elif not isinstance(current, dict) or step not in current:
            return None, None
        current = current[step]
    last = steps[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or not 0 <= last < len(current):
            return None, None
        return current, last
    # A leaf that is not there is reported the same way as a parent that is not
    # there: nothing to seal. Both callers treat "no container" as "skip", and
    # an absent path must never become a placeholder.
    if not isinstance(current, dict) or last not in current:
        return None, None
    return current, last


def _deep_copy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the payload so sealing never mutates the caller's object."""
    return {key: _copy_value(value) for key, value in payload.items()}


def _copy_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy_value(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_copy_value(child) for child in value]
    return value
