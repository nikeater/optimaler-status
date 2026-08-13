"""Vault re-hydration: the one place a sealed value comes back into text.

This module is the FIRST and ONLY production caller of ``VaultStore.fetch``
(ADR-002, ADR-018, ADR-023). Parts 04 to 07 never dereferenced the vault; the
deterministic plane computes on the request-scoped witness instead, which is a
different object with a different lifetime. Here, at outbound template
rendering, the sealed values come back - and every one of them is checked.

The contract, in the order it is enforced:

1. **Placeholder-shaped text that is not a valid placeholder is a hard error.**
   A forged, damaged or truncated token means somebody or something wrote the
   reserved syntax by hand, and the draft stops.
2. **Every valid placeholder must resolve in the fetched record.** An unknown
   token is a hard error - which is what makes an invented placeholder unable to
   resolve to somebody else's data (``engine/redact/placeholders.py``).
3. **The kind must match.** ``[[PII|NAME|<token of a VSNR entry>]]`` is a hard
   error too: a token identifies an entry, and a text that disagrees with the
   vault about what stands there has been edited.
4. **Values re-hydrate from the RAW as-received form.** The vault stores
   ``" \\t17170459B012  "`` because that is what arrived (ADR-018). Display
   normalizes the whitespace; the round-trip check then compares the display
   against the RAW value and fails if a single non-whitespace character was
   lost - which is the check that catches a formatter that drops a field.
5. **After substitution, zero placeholder syntax survives.** A final scan over
   the whole output, not over the parts that were touched.

Any failure raises :class:`RehydrationError` and NOTHING is returned: there is
no partial output, no "best effort" text and no token left visible in a letter.
A draft that could not be re-hydrated is a draft that does not exist.

Two shapes the vault holds, both handled here rather than in a template:

* prose entries carry ``part_id`` and ``span`` and no ``path``, and one value
  mentioned twice in one letter carries two different tokens (part-05 finding),
  so resolution is per TOKEN and never per value;
* ``ADDR`` entries are whole JSON objects rather than strings, so they go
  through :func:`format_address` - one address formatter, so two templates
  cannot disagree about what an address looks like.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from engine.redact.placeholders import PLACEHOLDER_RE, PLACEHOLDER_SHAPED_RE
from engine.redact.vault import SealedEntry, VaultRecord, VaultStore

#: The literal opener of the reserved syntax. Every occurrence of it in a draft
#: has to be the start of a VALID placeholder; see
#: :func:`_placeholder_shaped_offset` for why the part-04 pattern is not enough
#: on this path.
_OPENER_RE = re.compile(r"\[\[\s*PII", re.IGNORECASE)

#: Address keys in the order a German letter prints them. Anything the subtree
#: carries beyond these is appended rather than dropped - and the round-trip
#: check would fail the draft if it were dropped, which is the point of having
#: the check rather than trusting this list to stay complete.
ADDRESS_ORDER = (
    "co",
    "strasse",
    "hausnummer",
    "adresszusatz",
    "zusatz",
    "postfach",
    "plz",
    "ort",
    "bundesland",
    "land",
)

#: Which of those keys share a line, in the order the lines are printed.
ADDRESS_LINES: tuple[tuple[str, ...], ...] = (
    ("co",),
    ("strasse", "hausnummer", "adresszusatz", "zusatz"),
    ("postfach",),
    ("plz", "ort"),
    ("bundesland",),
    ("land",),
)


class RehydrationError(RuntimeError):
    """Raised when a text could not be re-hydrated. The draft does not exist.

    Deliberately not a subclass of anything the API maps to a 4xx body with
    detail: the message names kinds, tokens and positions, never values.
    """


@dataclass(frozen=True)
class RehydrationResult:
    """One re-hydrated text and what it took to produce it.

    ``text`` is PII-BEARING by design - that is the whole purpose of this
    module - and belongs in the draft store and nowhere else. The counts are
    value-free and are what the DRAFTED journal event records.
    """

    text: str
    resolved_tokens: int
    distinct_tokens: int
    kinds: dict[str, int]

    def summary(self) -> dict[str, Any]:
        """Value-free description, safe for a journal payload or a log line."""
        return {
            "resolved_tokens": self.resolved_tokens,
            "distinct_tokens": self.distinct_tokens,
            "kinds": dict(sorted(self.kinds.items())),
        }


class Rehydrator:
    """Fetches one vault record and re-hydrates texts against it.

    A class rather than a function so that "the vault is read once per draft,
    at render time" is visible in the call graph: :meth:`record` is the only
    ``fetch`` in the production code base, and everything else in this package
    takes the fetched :class:`VaultRecord` as a parameter.
    """

    def __init__(self, vault: VaultStore) -> None:
        self._vault = vault

    def record(self, vault_ref: str) -> VaultRecord:
        """Fetch the sealed record. THE render-time vault read (ADR-002).

        A missing record is a hard error like any other re-hydration failure:
        a draft whose identity cannot be resolved may not be written with the
        identity left out.
        """
        try:
            return self._vault.fetch(vault_ref)
        except Exception as error:  # any backend failure blocks the draft
            raise RehydrationError(
                f"the sealed record {vault_ref} could not be read, so no draft "
                f"can be re-hydrated for this case ({type(error).__name__})"
            ) from error

    def render(self, text: str, *, record: VaultRecord) -> RehydrationResult:
        """Re-hydrate one text against an already-fetched record."""
        return rehydrate(text, record=record)


def rehydrate(text: str, *, record: VaultRecord) -> RehydrationResult:
    """Resolve every placeholder in ``text``; anything unresolved is an error.

    Pure: it reads the record it is given and never reaches for a store, which
    is what keeps the "one fetch per draft" property checkable.
    """
    _refuse_placeholder_shaped(text)
    resolved: dict[str, str] = {}
    kinds: dict[str, int] = {}
    occurrences = 0
    for match in PLACEHOLDER_RE.finditer(text):
        token = match.group("token")
        kind = match.group("kind")
        occurrences += 1
        kinds[kind] = kinds.get(kind, 0) + 1
        if token in resolved:
            continue
        entry = record.entry_for(token)
        if entry is None:
            raise RehydrationError(
                f"the draft references a {kind} placeholder that the sealed "
                f"record {record.vault_ref} does not contain. An unknown token "
                f"cannot be resolved to somebody else's data, so the draft is "
                f"blocked (token ...{token[-4:]})"
            )
        if entry.kind.value != kind:
            raise RehydrationError(
                f"the draft calls token ...{token[-4:]} a {kind}, the sealed "
                f"record calls it a {entry.kind.value}; a text that disagrees "
                f"with the vault about what stands there has been edited"
            )
        resolved[token] = _display(entry, vault_ref=record.vault_ref)
    rendered = PLACEHOLDER_RE.sub(lambda hit: resolved[hit.group("token")], text)
    _assert_no_placeholder_survives(rendered)
    return RehydrationResult(
        text=rendered,
        resolved_tokens=occurrences,
        distinct_tokens=len(resolved),
        kinds=kinds,
    )


def placeholders_by_path(record: VaultRecord) -> dict[str, str]:
    """``payload path -> placeholder`` for every entry sealed out of a payload.

    This is how a letter addresses an applicant without anybody handling the
    value: the template gets the placeholder that stands at
    ``antragsteller.anschrift``, and this module resolves it afterwards. Prose
    entries carry no path (part-05 finding) and are deliberately absent -
    inventing one would point at nothing.
    """
    return {
        entry.path: entry.placeholder
        for entry in record.entries
        if entry.path is not None
    }


def format_address(value: Mapping[str, Any]) -> str:
    """The ONE address formatter: a sealed ADDR subtree as letter lines.

    Known keys print in German postal order; anything else the subtree carries
    is appended in key order rather than dropped, because a re-hydration that
    silently loses a field is exactly what the round-trip check exists to
    catch - and it would catch this one.
    """
    remaining = {key: value[key] for key in value}
    lines: list[str] = []
    for group in ADDRESS_LINES:
        parts = [
            text
            for key in group
            if (text := _collapse_text(remaining.pop(key, None))) is not None
        ]
        if parts:
            lines.append(" ".join(parts))
    lines.extend(
        text
        for key in sorted(remaining)
        if (text := _collapse_text(remaining[key])) is not None
    )
    return "\n".join(lines)


def format_value(entry: SealedEntry) -> str:
    """One sealed entry as display text: whitespace normalized, nothing lost."""
    raw = entry.value()
    if isinstance(raw, Mapping):
        return format_address(raw)
    if isinstance(raw, list):
        return "\n".join(
            text for item in raw if (text := _collapse_text(item)) is not None
        )
    return _collapse_text(raw) or ""


def round_trip_ok(entry: SealedEntry, display: str) -> bool:
    """Does ``display`` still carry everything the RAW sealed value carried?

    Compared against the raw form, never against a normalized copy of it
    (ADR-018's open thread for this part). Whitespace is what a display is
    allowed to change; nothing else is.

    * a scalar has to survive character for character once whitespace is
      removed - a truncated Versicherungsnummer fails;
    * an object's leaves have to account for the WHOLE display: each one is
      consumed from it, longest first, and what remains has to be empty. A
      substring test would have passed an address whose house number ``1``
      happens to occur inside its postcode ``10115``, which is exactly the bug
      this check exists to catch, and it would also have permitted a formatter
      that invented text.

    Longest-first consumption can in principle reject a display that is in fact
    complete, for values whose leaves overlap pathologically. That direction is
    the safe one: it blocks a draft rather than posting one.
    """
    raw = entry.value()
    packed = _packed(display)
    if isinstance(raw, Mapping | list):
        remaining = packed
        for leaf in sorted(_leaves(raw), key=len, reverse=True):
            index = remaining.find(leaf)
            if index < 0:
                return False
            remaining = remaining[:index] + remaining[index + len(leaf) :]
        return remaining == ""
    return _packed(_scalar_text(raw)) == packed


def _display(entry: SealedEntry, *, vault_ref: str) -> str:
    """Format one entry and prove the formatting lost nothing."""
    display = format_value(entry)
    if not display:
        raise RehydrationError(
            f"the sealed {entry.kind.value} entry of {vault_ref} renders as an "
            f"empty string; a draft may not silently drop an identity value"
        )
    if not round_trip_ok(entry, display):
        raise RehydrationError(
            f"the round-trip check failed for a {entry.kind.value} entry of "
            f"{vault_ref}: the rendered form does not carry everything the "
            f"sealed value carries. The draft is blocked rather than shipped "
            f"with a value that was silently changed"
        )
    return display


def _refuse_placeholder_shaped(text: str) -> None:
    """Refuse anything imitating the reserved syntax before substituting.

    Checked BEFORE the substitution rather than only after it, so a malformed
    token is reported as what it is instead of as "a placeholder survived".
    """
    offset = _placeholder_shaped_offset(text)
    if offset is not None:
        raise RehydrationError(
            f"the draft carries placeholder-shaped text at offset {offset} that "
            f"is not a valid placeholder. A forged or damaged token resolves to "
            f"nothing, and a letter may not go out with one in it"
        )


def _placeholder_shaped_offset(text: str) -> int | None:
    """Offset of the first imitation of the reserved syntax, or None.

    Deliberately STRICTER than ``PLACEHOLDER_SHAPED_RE``, which needs a closing
    ``]]`` to fire: a token truncated on the right (``[[PII|VSNR|ABCD...]``)
    matches neither that pattern nor the real one, and would have travelled into
    a letter untouched. Here every opener has to be the start of a valid
    placeholder, which leaves no shape in between.
    """
    for hit in _OPENER_RE.finditer(text):
        if PLACEHOLDER_RE.match(text, hit.start()) is None:
            return hit.start()
    match = PLACEHOLDER_SHAPED_RE.search(text)
    if match is not None and PLACEHOLDER_RE.fullmatch(match.group(0)) is None:
        return match.start()  # pragma: no cover - the opener scan catches these
    return None


def _assert_no_placeholder_survives(text: str) -> None:
    """The final scan of ruling 2: zero placeholder syntax in the output.

    Reachable for a reason worth knowing: substitution is total, so a surviving
    token would be a bug here - but a SEALED VALUE may itself imitate the
    reserved syntax (part 04 auto-seals exactly that residue), and re-hydrating
    it would put placeholder-shaped text back into the letter. Both cases end
    the same way, because a reader cannot tell them apart and neither may be
    posted.
    """
    if (
        PLACEHOLDER_RE.search(text) is not None
        or _placeholder_shaped_offset(text) is not None
    ):
        raise RehydrationError(
            "placeholder syntax survives in the re-hydrated text: either a "
            "token was not substituted or a sealed value imitates the reserved "
            "syntax. The draft is discarded rather than posted with it"
        )


def _leaves(value: object) -> list[str]:
    """Every scalar leaf of a sealed object, packed for comparison."""
    if isinstance(value, Mapping):
        return [leaf for child in value.values() for leaf in _leaves(child)]
    if isinstance(value, list | tuple):
        return [leaf for child in value for leaf in _leaves(child)]
    packed = _packed(_scalar_text(value))
    return [packed] if packed else []


def _collapse_text(value: object) -> str | None:
    """A scalar as display text with its whitespace collapsed, or None."""
    if value is None or isinstance(value, Mapping | list | tuple | set):
        return None
    text = " ".join(_scalar_text(value).split())
    return text or None


def _scalar_text(value: object) -> str:
    """The raw string form of a scalar, as received.

    ``json.dumps`` for anything that is not already a string, so a number stays
    the number that arrived rather than becoming Python's repr of it.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _packed(text: str) -> str:
    """The text with every whitespace character removed.

    The comparison form for the round-trip check: whitespace is what the
    display is allowed to change, and everything else is not.
    """
    return "".join(text.split())
