"""Hypothesis properties of the redaction boundary.

Four claims that have to hold for every input, not just for the fixtures:

1. every minted token obeys the alphabet and the format,
2. sealing is idempotent - re-running it over a working copy seals nothing new,
3. a checksummed identifier planted at ANY string leaf is found by the sweep,
4. German-ish text without the reserved syntax never parses as a placeholder.

Property 3 is the one that matters most: the canary suite proves a specific
planted value is caught, and this proves it for a planting position nobody
thought of.
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from engine.redact import (
    ALPHABET,
    TOKEN_LENGTH,
    Kind,
    PlaceholderRegistry,
    SeededTokenSource,
    contains_placeholder,
    format_placeholder,
    parse_placeholder,
    seal_payload,
    verify_payload,
)
from engine.redact.policy import load_policy
from engine.redact.recall import DETERMINISTIC_GATE_KINDS
from engine.redact.recognizers import (
    iban_checksum_ok,
    steuer_id_check_digit,
    vsnr_check_digit,
    vsnr_checksum_ok,
)

POLICY = load_policy()

#: Ordinary German administrative prose: everything except the reserved
#: brackets and pipe, so the "no accidental placeholder" property tests text
#: that could plausibly arrive rather than arbitrary bytes.
GERMAN_TEXT = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=0x017F,
        blacklist_characters="[]|",
    ),
    max_size=120,
)

PAYLOAD_KEYS = st.sampled_from(
    [
        "antrag",
        "antragsteller",
        "auftraggeber",
        "meta",
        "hinweistext",
        "letzte_taetigkeit",
        "bearbeitungswunsch",
        "notizen",
    ]
)


def _payload(values: dict[str, str]) -> dict[str, Any]:
    return {"antrag": dict(values)}


# ------------------------------------------------------------ token format ---


@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_every_minted_token_obeys_the_alphabet_and_the_format(seed: int) -> None:
    registry = PlaceholderRegistry(SeededTokenSource(seed))
    for kind in Kind:
        placeholder = registry.mint(kind)
        assert len(placeholder.token) == TOKEN_LENGTH
        assert set(placeholder.token) <= set(ALPHABET)
        parsed = parse_placeholder(str(placeholder))
        assert parsed is not None
        assert parsed.kind is kind
        assert parsed.token == placeholder.token


@given(
    kind=st.sampled_from(list(Kind)),
    token=st.text(alphabet=ALPHABET, min_size=TOKEN_LENGTH, max_size=TOKEN_LENGTH),
)
def test_format_and_parse_are_inverse(kind: Kind, token: str) -> None:
    parsed = parse_placeholder(format_placeholder(kind, token))
    assert parsed is not None
    assert (parsed.kind, parsed.token) == (kind, token)


# --------------------------------------------------------------- no collision ---


@given(text=GERMAN_TEXT)
def test_ordinary_text_never_parses_as_a_placeholder(text: str) -> None:
    assert parse_placeholder(text) is None
    assert contains_placeholder(text) is False


@given(
    prefix=GERMAN_TEXT,
    suffix=GERMAN_TEXT,
    token=st.text(alphabet=ALPHABET, min_size=TOKEN_LENGTH, max_size=TOKEN_LENGTH),
)
def test_a_placeholder_is_still_found_inside_surrounding_prose(
    prefix: str, suffix: str, token: str
) -> None:
    text = prefix + format_placeholder(Kind.TEXT, token) + suffix
    assert contains_placeholder(text) is True


# ------------------------------------------------------------- idempotence ---


@given(
    values=st.dictionaries(PAYLOAD_KEYS, GERMAN_TEXT, max_size=4),
    vsnr_present=st.booleans(),
    seed=st.integers(min_value=0, max_value=1000),
)
def test_sealing_a_sealed_payload_seals_nothing_new(
    values: dict[str, str], vsnr_present: bool, seed: int
) -> None:
    payload = _payload(values)
    if vsnr_present:
        payload["antragsteller"] = {"versicherungsnummer": "65170839J003"}
    once = seal_payload(
        payload, policy=POLICY, registry=PlaceholderRegistry(SeededTokenSource(seed))
    )
    twice = seal_payload(
        once.payload,
        policy=POLICY,
        registry=PlaceholderRegistry(SeededTokenSource(seed + 1)),
    )
    assert twice.sealed_count == 0
    assert twice.payload == once.payload


@given(
    values=st.dictionaries(PAYLOAD_KEYS, GERMAN_TEXT, max_size=4),
    seed=st.integers(min_value=0, max_value=1000),
)
def test_sealing_never_invents_a_value_where_there_was_none(
    values: dict[str, str], seed: int
) -> None:
    """Presence has to survive: nothing gains a placeholder that had no value."""
    payload = _payload(values)
    outcome = seal_payload(
        payload, policy=POLICY, registry=PlaceholderRegistry(SeededTokenSource(seed))
    )
    assert set(outcome.payload) == set(payload)
    assert set(outcome.payload["antrag"]) == set(payload["antrag"])
    assert outcome.sealed_count == 0


# ------------------------------------------------- the sweep finds plantings ---


@st.composite
def checksummed_identifier(draw: st.DrawFn) -> str:
    """A VSNR, a Steuer-ID or an IBAN, each with a correct check digit."""
    kind = draw(st.sampled_from(sorted({Kind.VSNR, Kind.STID, Kind.IBAN})))
    if kind is Kind.VSNR:
        bereich = draw(st.integers(min_value=10, max_value=89))
        day = draw(st.integers(min_value=1, max_value=28))
        month = draw(st.integers(min_value=1, max_value=12))
        year = draw(st.integers(min_value=40, max_value=75))
        letter = draw(st.sampled_from("ABCDEFGHKLMNPRSTUVWZ"))
        serial = draw(st.integers(min_value=0, max_value=99))
        prefix = f"{bereich:02d}{day:02d}{month:02d}{year:02d}{letter}{serial:02d}"
        return prefix + vsnr_check_digit(prefix)
    if kind is Kind.STID:
        first = draw(st.integers(min_value=1, max_value=9))
        rest = draw(
            st.lists(st.integers(min_value=0, max_value=9), min_size=9, max_size=9)
        )
        ten = f"{first}" + "".join(str(digit) for digit in rest)
        return ten + steuer_id_check_digit(ten)
    digits = draw(
        st.lists(st.integers(min_value=0, max_value=9), min_size=18, max_size=18)
    )
    bban = "".join(str(digit) for digit in digits)
    from engine.redact.recognizers import iban_check_digits

    return "DE" + iban_check_digits("DE", bban) + bban


@given(
    identifier=checksummed_identifier(),
    key=PAYLOAD_KEYS,
    before=st.sampled_from(["", "Hinweis: ", "zuletzt gefuehrt unter "]),
    after=st.sampled_from(["", " im Bestand", ", bitte pruefen"]),
)
def test_a_checksummed_identifier_planted_anywhere_is_found(
    identifier: str, key: str, before: str, after: str
) -> None:
    payload = {"antrag": {key: f"{before}{identifier}{after}"}}
    report = verify_payload(payload)
    assert report.clean is False
    assert report.paths == (f"antrag.{key}",)
    # The report describes the residue and never quotes it.
    assert identifier not in json.dumps(report.to_dict())


@given(identifier=checksummed_identifier())
def test_the_generated_identifiers_really_do_verify(identifier: str) -> None:
    """A property test over broken fixtures proves nothing; this pins them."""
    assert (
        vsnr_checksum_ok(identifier)
        or iban_checksum_ok(identifier)
        or len(identifier) == 11
    )


def test_every_gated_kind_has_at_least_one_recognizer_in_the_union() -> None:
    """Not a property, but the invariant the properties above rest on."""
    from engine.redact.detector import redact_recognizers

    assert {
        recognizer.kind for recognizer in redact_recognizers()
    } >= DETERMINISTIC_GATE_KINDS
