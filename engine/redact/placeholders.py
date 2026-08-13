"""The reserved placeholder syntax the working copy carries.

Format::

    [[PII|<KIND>|<TOKEN>]]

``KIND`` is one of :class:`Kind`; ``TOKEN`` is 12 characters drawn uniformly
from ``BCDFGHJKMNPQRSTVWXZ23456789``. The alphabet has no vowels (so no token
ever spells a word, in German or in any other language a reader might see
meaning in) and none of ``0 1 I L O``, the characters people and OCR confuse.
27 characters over 12 positions is ``log2(27**12) = 57.1`` bits.

Two collision questions this module has to answer, because ADR-002 rests on
both:

*Can a model invent a valid placeholder?* Only by guessing 57 bits. A generated
token is not derived from anything the model sees - not from the case id, not
from the value, not from a counter - so there is no structure to learn. And the
re-hydrator of part 08 treats an unknown placeholder as a hard error that blocks
output, so an invented one cannot resolve to somebody else's data; it stops the
draft.

*Can document text collide with a placeholder?* The bracket syntax is reserved
and does not occur in German administrative prose. That is an argument, not a
guarantee, so :class:`PlaceholderRegistry` also re-draws a token that literally
occurs in the source content of the case it is minting for. The remaining risk
is a document that contains the full reserved syntax around a token we then
draw, which is 57 bits *and* a bracket sequence.

The parse regular expression below is the single definition: substitution,
verification and witness resolution all go through it, so "what is a
placeholder" cannot drift between the code that writes them and the code that
reads them.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Container, Iterator
from enum import StrEnum
from random import Random
from typing import Protocol, runtime_checkable

#: No vowels (no token spells a word), no 0/1/I/L/O (no lookalikes).
ALPHABET = "BCDFGHJKMNPQRSTVWXZ23456789"

#: 27**12 is 57.1 bits of entropy per token.
TOKEN_LENGTH = 12

#: Vault handles use the same alphabet and are deliberately longer.
VAULT_REF_LENGTH = 26
VAULT_REF_PREFIX = "vault-"

#: Guard against a pathological token source (a stub that returns a constant).
MAX_DRAWS = 64


class Kind(StrEnum):
    """What a sealed value is, as far as the working copy is concerned.

    The kind travels in the clear on purpose: a caseworker reading a working
    copy has to be able to tell "a Versicherungsnummer stood here" from "an
    address stood here" without dereferencing anything.
    """

    VSNR = "VSNR"
    GEBDAT = "GEBDAT"
    ADDR = "ADDR"
    NAME = "NAME"
    ORG = "ORG"
    BNR = "BNR"
    IBAN = "IBAN"
    STID = "STID"
    AKTZ = "AKTZ"
    EMAIL = "EMAIL"
    TEL = "TEL"
    TEXT = "TEXT"


#: The one definition of the syntax. Everything that reads or writes a
#: placeholder goes through this pattern or through the helpers below.
PLACEHOLDER_RE = re.compile(
    r"\[\[PII\|(?P<kind>[A-Z]+)\|(?P<token>["
    + ALPHABET
    + r"]{"
    + str(TOKEN_LENGTH)
    + r"})\]\]"
)

#: Placeholder-shaped text that is NOT a valid placeholder: a forged or damaged
#: token, or a kind this build does not know. The verification pass treats a hit
#: as residue, because something is imitating the reserved syntax.
PLACEHOLDER_SHAPED_RE = re.compile(r"\[\[\s*PII\s*\|[^\]]{0,64}\]\]")


class PlaceholderError(RuntimeError):
    """Raised when a placeholder cannot be minted or parsed."""


class Placeholder:
    """One minted placeholder: a kind and a token.

    Not a dataclass, because the default ``repr`` of a dataclass prints its
    fields and this object travels through exception paths. The token is not
    secret (it is in the working copy) but printing it by accident is still the
    class of habit this package is here to break.
    """

    __slots__ = ("kind", "token")

    def __init__(self, kind: Kind, token: str) -> None:
        self.kind = kind
        self.token = token

    def __str__(self) -> str:
        return f"[[PII|{self.kind.value}|{self.token}]]"

    def __repr__(self) -> str:
        return f"Placeholder(kind={self.kind.value})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Placeholder):
            return NotImplemented
        return self.kind is other.kind and self.token == other.token

    def __hash__(self) -> int:
        return hash((self.kind, self.token))


def format_placeholder(kind: Kind, token: str) -> str:
    """Render ``kind``/``token`` as the reserved syntax."""
    return str(Placeholder(kind, token))


def parse_placeholder(text: str) -> Placeholder | None:
    """Parse a string that is EXACTLY one placeholder, else None.

    Exactness matters: witness resolution may only fire when the whole field
    value is a placeholder. A value that merely contains one is free text and
    belongs to the text layer (part 05), not to a scalar lookup.
    """
    match = PLACEHOLDER_RE.fullmatch(text.strip())
    if match is None:
        return None
    return _from_match(match)


def find_placeholders(text: str) -> tuple[Placeholder, ...]:
    """Every valid placeholder inside a string, in order of appearance."""
    return tuple(_from_match(match) for match in PLACEHOLDER_RE.finditer(text))


def contains_placeholder(text: str) -> bool:
    """Whether a string contains at least one valid placeholder."""
    return PLACEHOLDER_RE.search(text) is not None


def _from_match(match: re.Match[str]) -> Placeholder:
    try:
        kind = Kind(match.group("kind"))
    except ValueError as error:  # pragma: no cover - unreachable via the regex
        raise PlaceholderError(
            f"unknown placeholder kind: {match.group('kind')}"
        ) from error
    return Placeholder(kind, match.group("token"))


@runtime_checkable
class TokenSource(Protocol):
    """Where placeholder tokens come from.

    Two implementations ship: :class:`SecretsTokenSource` for anything that
    touches real data, and :class:`SeededTokenSource` for tests and for any
    artifact that has to be byte-stable across runs.
    """

    def token(self, length: int = TOKEN_LENGTH) -> str:
        """Draw one token of ``length`` characters from :data:`ALPHABET`."""
        ...


class SecretsTokenSource:
    """Production source: ``secrets.choice`` over the reserved alphabet."""

    def token(self, length: int = TOKEN_LENGTH) -> str:
        return "".join(secrets.choice(ALPHABET) for _ in range(length))


class SeededTokenSource:
    """Reproducible source for tests and golden artifacts.

    Never use this where real identity data is sealed: the token sequence is a
    function of the seed, so anyone with the seed can predict every placeholder
    a case will carry.
    """

    def __init__(self, seed: int = 42) -> None:
        # Not cryptographic, and that is the point: reproducibility is the
        # whole feature. Never used where real identity data is sealed.
        self._random = Random(seed)

    def token(self, length: int = TOKEN_LENGTH) -> str:
        return "".join(self._random.choice(ALPHABET) for _ in range(length))


class PlaceholderRegistry:
    """Mints placeholders for one case and guarantees they are unique.

    ``reserved`` is the source content of the case. A token that literally
    occurs in it is re-drawn: substituting a placeholder whose token the
    document already contained would make the round-trip ambiguous.
    """

    def __init__(
        self,
        source: TokenSource | None = None,
        *,
        reserved: str | Container[str] = "",
    ) -> None:
        self._source = source if source is not None else SecretsTokenSource()
        self._reserved = reserved
        self._issued: set[str] = set()

    @property
    def issued(self) -> frozenset[str]:
        """Every token this registry has handed out."""
        return frozenset(self._issued)

    def mint(
        self, kind: Kind, *, avoid: Callable[[str], bool] | None = None
    ) -> Placeholder:
        """Draw a fresh, unused, non-colliding token for ``kind``."""
        for _ in range(MAX_DRAWS):
            token = self._source.token()
            if self._usable(token) and not (avoid is not None and avoid(token)):
                self._issued.add(token)
                return Placeholder(kind, token)
        raise PlaceholderError(
            f"could not draw a free placeholder token after {MAX_DRAWS} attempts; "
            "the token source is not producing fresh values"
        )

    def vault_ref(self) -> str:
        """Mint a vault handle: not derivable from anything about the case."""
        for _ in range(MAX_DRAWS):
            token = self._source.token(VAULT_REF_LENGTH)
            if len(token) == VAULT_REF_LENGTH and set(token) <= set(ALPHABET):
                return VAULT_REF_PREFIX + token
        raise PlaceholderError(  # pragma: no cover - only a broken TokenSource
            "token source did not produce a usable vault reference"
        )

    def _usable(self, token: str) -> bool:
        if len(token) != TOKEN_LENGTH or not set(token) <= set(ALPHABET):
            return False
        if token in self._issued:
            return False
        return token not in self._reserved


def iter_kinds() -> Iterator[Kind]:
    """Every placeholder kind, in declaration order."""
    return iter(Kind)
