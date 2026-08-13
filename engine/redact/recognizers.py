"""Deterministic German PII recognizers, in two profiles.

Two profiles, because the two jobs have opposite error costs (ADR-017):

``Profile.REDACT`` - **recall first.** Used to decide what to seal. A format hit
counts even when the checksum fails, because a Versicherungsnummer with a typo
in it is still a Versicherungsnummer: it identifies a person to anyone who reads
it, and refusing to redact it because the Pruefziffer is off would be exactly
backwards.

``Profile.VERIFY`` - **precision first.** Used by the post-redaction sweep over
the structured working copy, where a hit means "seal this leaf and, if that does
not help, refuse the submission". Only checksum-VALIDATED VSNR, Steuer-ID and
IBAN, plus e-mail addresses and anything imitating the reserved placeholder
syntax. Deliberately NOT bare dates and NOT bare eight-digit numbers: a
Rentenbeginn and a Betrag in Cent are legitimate payload content, and a gate
that fires on them gets switched off within a week, which is a worse outcome
than the false negatives it would have caught.

Every recognizer cites the public specification it implements. None of them was
tuned on a real submission; the material is public form documentation and public
check-digit algorithms.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum, StrEnum

from engine.redact.placeholders import (
    PLACEHOLDER_RE,
    PLACEHOLDER_SHAPED_RE,
    Kind,
)


class Profile(StrEnum):
    """Which job a scan is doing."""

    REDACT = "redact"
    VERIFY = "verify"


class Evidence(IntEnum):
    """How much a hit knows about itself. Higher wins a disagreement.

    The union contains one probabilistic member and a dozen deterministic ones,
    and when they disagree about the same characters the deterministic answer is
    the better one - that is the entire reason for building a union instead of
    trusting the model. Presidio's own documentation says its recall is not
    guaranteed; a mod-97 check on an IBAN is not a guess.
    """

    MODEL = 0
    PATTERN = 1
    CHECKSUM = 2


@dataclass(frozen=True, order=True)
class Detection:
    """One recognizer hit: where, what, and who found it.

    Deliberately does NOT carry the matched text. A finding that quotes what it
    found is itself a leak, and this object travels into reports and logs.
    """

    start: int
    end: int
    kind: Kind
    recognizer_id: str
    validated: bool = False
    evidence: Evidence = Evidence.PATTERN

    @property
    def length(self) -> int:
        """How many characters the hit covers."""
        return self.end - self.start

    def overlaps(self, other: Detection) -> bool:
        """Whether two hits share at least one character position."""
        return self.start < other.end and other.start < self.end

    def contains(self, start: int, end: int) -> bool:
        """Whether this hit fully covers the range ``[start, end)``."""
        return self.start <= start and self.end >= end


@dataclass(frozen=True)
class Recognizer:
    """A regular expression, a kind, and where its checksum is binding."""

    recognizer_id: str
    kind: Kind
    pattern: re.Pattern[str]
    profiles: frozenset[Profile]
    group: int | str = 0
    validator: Callable[[str], bool] | None = None
    #: Profiles in which a failing validator SUPPRESSES the hit. A checksummed
    #: recognizer lists VERIFY here and not REDACT, which is the whole point of
    #: the two-profile split.
    gated_in: frozenset[Profile] = field(default_factory=frozenset)
    #: Whether a passing validator is a CHECK DIGIT (proof) or something weaker.
    #: Only the first kind earns :attr:`Evidence.CHECKSUM` on a hit.
    checksummed: bool = False

    def scan(self, text: str, profile: Profile) -> Iterator[Detection]:
        """Yield hits of this recognizer in ``text`` for ``profile``."""
        if profile not in self.profiles:
            return
        for match in self.pattern.finditer(text):
            start, end = match.span(self.group)
            if start < 0:
                continue  # pragma: no cover - optional group that did not match
            valid = self.validator is None or self.validator(match.group(self.group))
            if not valid and profile in self.gated_in:
                continue
            yield Detection(
                start=start,
                end=end,
                kind=self.kind,
                recognizer_id=self.recognizer_id,
                validated=valid,
                # A format hit whose check digit FAILED is still a hit in the
                # REDACT profile, but it has not proved anything, so it does not
                # get to outrank a model on the strength of its class.
                evidence=(
                    Evidence.CHECKSUM
                    if self.checksummed and valid
                    else Evidence.PATTERN
                ),
            )


# --------------------------------------------------------------- checksums ---

#: Weights of the DRV Pruefziffer, applied to the twelve digits that remain
#: after the name letter is expanded into its two-digit alphabet position.
VSNR_WEIGHTS = (2, 1, 2, 5, 7, 1, 2, 1, 2, 1, 2, 1)

VSNR_PATTERN = re.compile(r"(?<![0-9A-Za-z])[0-9]{8}[A-Z][0-9]{3}(?![0-9A-Za-z])")


def vsnr_checksum_ok(value: str) -> bool:
    """DRV Versicherungsnummer check digit (public algorithm).

    Aufbau: Bereichsnummer (2) + Geburtsdatum TTMMJJ (6) + Anfangsbuchstabe des
    Geburtsnamens (1) + Seriennummer (2) + Pruefziffer (1). The letter is
    replaced by its two-digit position in the alphabet (A=01 .. Z=26), the
    resulting twelve digits are multiplied by :data:`VSNR_WEIGHTS`, the digit
    sum of every product is added up, and the last digit of that total is the
    Pruefziffer.

    Verified against the published example ``65170839J003``.
    """
    text = value.strip()
    if re.fullmatch(r"[0-9]{8}[A-Z][0-9]{3}", text) is None:
        return False
    letter_position = ord(text[8]) - ord("A") + 1
    digits = [int(char) for char in text[:8]]
    digits.extend(int(char) for char in f"{letter_position:02d}")
    digits.extend(int(char) for char in text[9:11])
    total = sum(
        _digit_sum(digit * weight)
        for digit, weight in zip(digits, VSNR_WEIGHTS, strict=True)
    )
    return total % 10 == int(text[11])


def vsnr_check_digit(first_eleven: str) -> str:
    """The Pruefziffer that completes an eleven-character Versicherungsnummer.

    Used by the seeded PII golden set to produce checksum-VALID synthetic
    numbers; the production path only ever verifies.
    """
    for candidate in "0123456789":
        if vsnr_checksum_ok(first_eleven + candidate):
            return candidate
    raise ValueError(  # pragma: no cover - a digit always exists
        f"no check digit completes {first_eleven!r}"
    )


def vsnr_birthdate_plausible(value: str) -> bool:
    """Whether positions 3 to 8 of a Versicherungsnummer are a real date.

    The two-digit year is read as 19YY, exactly as
    ``engine.evidence.completeness`` does: the century is not encoded and
    guessing one would invent information.
    """
    text = value.strip()
    if len(text) < 8:
        return False
    day, month, year = text[2:4], text[4:6], text[6:8]
    if not (day.isdigit() and month.isdigit() and year.isdigit()):
        return False
    try:
        date(1900 + int(year), int(month), int(day))
    except ValueError:
        return False
    return True


def vsnr_verifiable(value: str) -> bool:
    """The VERIFY-profile gate: Pruefziffer AND a real embedded birth date.

    Both halves, because VERIFY is the precision-first profile: a hit here
    seals a leaf and, failing that, refuses the whole submission. The REDACT
    profile uses neither - there a format hit is enough, since a mistyped
    Versicherungsnummer identifies a person just as well as a correct one.
    """
    return vsnr_checksum_ok(value) and vsnr_birthdate_plausible(value)


STEUER_ID_PATTERN = re.compile(r"(?<![0-9])[1-9][0-9]{10}(?![0-9])")


def steuer_id_checksum_ok(value: str) -> bool:
    """Steuerliche Identifikationsnummer, ISO 7064 MOD 11,10 (BZSt).

    Eleven digits, leading digit non-zero, last digit the check digit. Verified
    against the BZSt example ``86095742719``.

    The BZSt additionally constrains digit repetition inside the first ten
    digits. That rule is deliberately NOT enforced here: this recognizer decides
    whether something gets redacted, and a rule that is slightly wrong in the
    strict direction would leave a real Steuer-ID in the working copy. Over-
    redaction costs utility; under-redaction costs a person's data.
    """
    text = value.strip()
    if re.fullmatch(r"[1-9][0-9]{10}", text) is None:
        return False
    product = 10
    for char in text[:10]:
        total = (int(char) + product) % 10
        if total == 0:
            total = 10
        product = (total * 2) % 11
    return (11 - product) % 10 == int(text[10])


def steuer_id_check_digit(first_ten: str) -> str:
    """The MOD 11,10 check digit for the first ten digits of a Steuer-ID."""
    product = 10
    for char in first_ten:
        total = (int(char) + product) % 10
        if total == 0:
            total = 10
        product = (total * 2) % 11
    return str((11 - product) % 10)


IBAN_DE_PATTERN = re.compile(r"(?<![A-Z0-9])DE[0-9]{20}(?![A-Z0-9])")
IBAN_ANY_PATTERN = re.compile(
    r"(?<![A-Z0-9])[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}(?![A-Z0-9])"
)


def iban_checksum_ok(value: str) -> bool:
    """IBAN mod-97 check (ISO 13616 / ISO 7064 MOD 97-10)."""
    text = re.sub(r"\s+", "", value.strip()).upper()
    if re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", text) is None:
        return False
    rearranged = text[4:] + text[:4]
    digits = "".join(
        str(ord(char) - ord("A") + 10) if char.isalpha() else char
        for char in rearranged
    )
    return int(digits) % 97 == 1


def iban_check_digits(country: str, bban: str) -> str:
    """The two check digits that make ``country`` + ``bban`` a valid IBAN."""
    rearranged = bban + country + "00"
    digits = "".join(
        str(ord(char) - ord("A") + 10) if char.isalpha() else char
        for char in rearranged
    )
    return f"{98 - int(digits) % 97:02d}"


def _digit_sum(value: int) -> int:
    return sum(int(char) for char in str(value))


# ------------------------------------------------- context-bound recognizers ---

#: A bare eight-digit number is a Betrag, a Rechnungsnummer or a date without
#: separators far more often than it is a Betriebsnummer, so this recognizer
#: only fires behind an explicit label.
BETRIEBSNUMMER_PATTERN = re.compile(
    r"(?:Betriebsnummer|BetrNr\.?|BNR|Betriebs-Nr\.?)\s*:?\s*(?P<value>[0-9]{8})"
    r"(?![0-9])",
    re.IGNORECASE,
)

#: German administrative and court file numbers. Two shapes: the labelled form
#: ("Aktenzeichen: ...") and the unlabelled court form ("S 12 R 3456/24").
AKTENZEICHEN_LABELLED = re.compile(
    r"(?i:Aktenzeichen|Az\.|Gesch(?:ae|ä)ftszeichen|Vorgangsnummer|Antragsnummer)"
    r"\s*:?\s*(?P<value>[A-Z0-9][A-Za-z0-9]*"
    # Continuation tokens are uppercase letter groups or digit-led groups, so
    # the match stops at the first ordinary German word ("... 4711/26 vom 3.
    # Mai") instead of swallowing the rest of the sentence.
    r"(?:[ ./-](?:[A-Z]{1,3}|[0-9][A-Za-z0-9/]*)){0,6})"
)
#: Sozialgerichts- and Behoerden-Aktenzeichen: an optional Registerzeichen
#: ("S" Sozialgericht, "L" Landessozialgericht, "B" Bundessozialgericht), the
#: Kammer, the Sachgebiet, the running number and the year, plus the trailing
#: Senatszusatz a BSG number carries ("B 12 R 3/25 R"). Matching only the
#: "12 R 3/25" core would redact the middle of a file number and leave both
#: ends in the working copy.
AKTENZEICHEN_COURT = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z]{1,2}\s)?[0-9]{1,4}\s?[A-Z]{1,3}\s?"
    r"[0-9]{1,5}/[0-9]{2,4}(?:\s[A-Z]{1,2})?"
    r"(?![0-9])"
)

#: The trailing guard stops a partial match without rejecting a sentence-final
#: address: in "... bitte an a.b@muster.de." the last period is punctuation, not
#: part of the domain, so only a dot FOLLOWED by a label may continue the match.
EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9-])(?!\.[A-Za-z0-9])"
)

#: German phone numbers: +49 or a 0 prefix, then an area group and a subscriber
#: group. The digit lookarounds keep it off ISO dates and off the digit runs
#: inside an IBAN.
TELEFON_PATTERN = re.compile(
    r"(?<![0-9+])(?:\+49[ /()-]{0,2}|0)[0-9]{2,5}[ /()-]{0,3}[0-9]{3,9}"
    r"(?:[ /-][0-9]{1,6})?(?![0-9])"
)

#: Strassenname + Hausnummer. The suffix list is what carries the precision;
#: "Kirchgasse 2" is an address, "Beratung 2" is not.
STRASSE_PATTERN = re.compile(
    r"(?<![A-Za-zÄÖÜäöüß])[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]{2,30}"
    r"(?i:stra(?:ss|ß)e|str\.|weg|gasse|allee|platz|ring|damm|ufer|steig|pfad"
    r"|chaussee)"
    r"\s+[0-9]{1,4}\s?[a-zA-Z]?(?![0-9])"
)

#: PLZ + Ort. Five digits with nothing numeric on either side, then a
#: capitalized place name.
PLZ_ORT_PATTERN = re.compile(
    r"(?<![0-9])[0-9]{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]+"
    r"(?:[ -][A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]+)?"
)

#: Birth date only where the context says it is one. A bare date is procedural
#: content in this domain (Rentenbeginn, Eingangsdatum, Taetigkeitsbeginn) and
#: sealing every date would empty the payload of everything a rule reads.
GEBURTSDATUM_PATTERN = re.compile(
    r"(?:geboren\s+am|Geburtsdatum|Geburtstag|geb\.)"
    # A short digit-free run of filler, so "Geburtsdatum: ", "geboren am " and
    # "Als Geburtsdatum wurde " all reach the date, while a sentence that
    # mentions a Geburtsdatum and a Rentenbeginn far apart does not join them.
    r"[^0-9\n]{0,12}"
    r"(?P<value>[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
    re.IGNORECASE,
)

#: Organisations by legal form. A German legal-form suffix is close to a
#: guarantee, which is what makes this deterministic rather than a guess.
ORGANISATION_PATTERN = re.compile(
    r"(?<![A-Za-zÄÖÜäöüß])[A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.-]*"
    # "&" is a continuation token in its own right: "Meyer & Sohn GmbH" is one
    # organisation, and a pattern that started at "Sohn" would redact half a
    # company name and leave the other half in the working copy.
    r"(?:[ -](?:&|[A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß&.-]*)){0,5}"
    # gGmbH before GmbH: a gemeinnuetzige GmbH is written as one word, and the
    # longer alternative has to be offered first or only "GmbH" would match and
    # the leading "g" would stay in the working copy.
    r"\s(?:gGmbH|GmbH(?:\s?&\s?Co\.?\s?KG)?|AG|KG|OHG|GbR"
    r"|UG\s?\(haftungsbeschr(?:ae|ä)nkt\)"
    r"|UG|e\.\s?V\.|e\.\s?K\.|mbH|SE|PartG(?:mbB)?)(?![A-Za-zÄÖÜäöüß])"
)

#: Person names behind a salutation. Bare names are the NER member's job
#: (engine/redact/ner.py); this catches the labelled ones without a model.
NAME_PATTERN = re.compile(
    r"(?<![A-Za-zÄÖÜäöüß])(?:Herrn?|Frau|Familie)\s+(?P<value>(?:Dr\.\s+|Prof\.\s+)*"
    r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+){1,3})"
)


def _placeholder_collision(value: str) -> bool:
    """Whether placeholder-shaped text is NOT a valid placeholder."""
    return PLACEHOLDER_RE.fullmatch(value) is None


BOTH = frozenset({Profile.REDACT, Profile.VERIFY})
REDACT_ONLY = frozenset({Profile.REDACT})
VERIFY_ONLY = frozenset({Profile.VERIFY})


#: The recognizer union. Order is irrelevant: the detector merges by span
#: length, not by declaration order.
RECOGNIZERS: tuple[Recognizer, ...] = (
    Recognizer(
        recognizer_id="vsnr",
        kind=Kind.VSNR,
        pattern=VSNR_PATTERN,
        profiles=BOTH,
        validator=vsnr_verifiable,
        gated_in=VERIFY_ONLY,
        checksummed=True,
    ),
    Recognizer(
        recognizer_id="steuer_id",
        kind=Kind.STID,
        pattern=STEUER_ID_PATTERN,
        profiles=BOTH,
        validator=steuer_id_checksum_ok,
        gated_in=VERIFY_ONLY,
        checksummed=True,
    ),
    Recognizer(
        recognizer_id="iban_de",
        kind=Kind.IBAN,
        pattern=IBAN_DE_PATTERN,
        profiles=BOTH,
        validator=iban_checksum_ok,
        gated_in=VERIFY_ONLY,
        checksummed=True,
    ),
    Recognizer(
        recognizer_id="iban_any",
        kind=Kind.IBAN,
        pattern=IBAN_ANY_PATTERN,
        profiles=BOTH,
        validator=iban_checksum_ok,
        # The generic shape matches far too much (any two letters, two digits
        # and eleven alphanumerics) to be trusted on format alone, so unlike the
        # DE form it is checksum-gated in BOTH profiles.
        gated_in=BOTH,
        checksummed=True,
    ),
    Recognizer(
        recognizer_id="betriebsnummer",
        kind=Kind.BNR,
        pattern=BETRIEBSNUMMER_PATTERN,
        profiles=REDACT_ONLY,
        group="value",
    ),
    Recognizer(
        recognizer_id="aktenzeichen_labelled",
        kind=Kind.AKTZ,
        pattern=AKTENZEICHEN_LABELLED,
        profiles=REDACT_ONLY,
        group="value",
    ),
    Recognizer(
        recognizer_id="aktenzeichen_court",
        kind=Kind.AKTZ,
        pattern=AKTENZEICHEN_COURT,
        profiles=REDACT_ONLY,
    ),
    Recognizer(
        recognizer_id="email",
        kind=Kind.EMAIL,
        pattern=EMAIL_PATTERN,
        profiles=BOTH,
    ),
    Recognizer(
        recognizer_id="telefon",
        kind=Kind.TEL,
        pattern=TELEFON_PATTERN,
        profiles=REDACT_ONLY,
    ),
    Recognizer(
        recognizer_id="strasse_hausnummer",
        kind=Kind.ADDR,
        pattern=STRASSE_PATTERN,
        profiles=REDACT_ONLY,
    ),
    Recognizer(
        recognizer_id="plz_ort",
        kind=Kind.ADDR,
        pattern=PLZ_ORT_PATTERN,
        profiles=REDACT_ONLY,
    ),
    Recognizer(
        recognizer_id="geburtsdatum_kontext",
        kind=Kind.GEBDAT,
        pattern=GEBURTSDATUM_PATTERN,
        profiles=REDACT_ONLY,
        group="value",
    ),
    Recognizer(
        recognizer_id="organisation_rechtsform",
        kind=Kind.ORG,
        pattern=ORGANISATION_PATTERN,
        profiles=REDACT_ONLY,
    ),
    Recognizer(
        recognizer_id="name_anrede",
        kind=Kind.NAME,
        pattern=NAME_PATTERN,
        profiles=REDACT_ONLY,
        group="value",
    ),
    Recognizer(
        recognizer_id="placeholder_collision",
        kind=Kind.TEXT,
        pattern=PLACEHOLDER_SHAPED_RE,
        profiles=VERIFY_ONLY,
        validator=_placeholder_collision,
        # Inverted on purpose: the "validator" returns True when the text is a
        # COLLISION, so gating it in VERIFY suppresses the well-formed
        # placeholders (which are exactly what the working copy is supposed to
        # contain) and reports everything that only imitates the syntax.
        gated_in=VERIFY_ONLY,
    ),
)


def recognizers_for(profile: Profile) -> tuple[Recognizer, ...]:
    """Every recognizer that participates in ``profile``."""
    return tuple(
        recognizer for recognizer in RECOGNIZERS if profile in recognizer.profiles
    )


def deterministic_kinds(profile: Profile = Profile.REDACT) -> frozenset[Kind]:
    """Kinds the deterministic union can find without the optional NER extra."""
    return frozenset(recognizer.kind for recognizer in recognizers_for(profile))


def inventory(profile: Profile = Profile.REDACT) -> list[dict[str, object]]:
    """A reportable description of the union, for the eval report."""
    return [
        {
            "recognizer_id": recognizer.recognizer_id,
            "kind": recognizer.kind.value,
            "checksum_gated": sorted(item.value for item in recognizer.gated_in),
        }
        for recognizer in _sorted(recognizers_for(profile))
    ]


def _sorted(items: Sequence[Recognizer]) -> list[Recognizer]:
    return sorted(items, key=lambda recognizer: recognizer.recognizer_id)
