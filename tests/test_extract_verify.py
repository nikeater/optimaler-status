"""The double lock (P-8): a quote AND an offset, checked against each other.

This is the file that decides whether a language model may be called in this
system at all. Every proposal - from the deterministic replay extractor, from a
live model, from anything a later part adds - has to survive both locks before
it becomes evidence, and the verifier cannot tell which extractor produced it.

The properties at the bottom are the load-bearing part: they say that no
proposal whose offset is wrong can be accepted under an exact policy, that an
accepted record's span always slices out text the quote actually matches, and
that a discard never becomes a value. The examples above them say what each
individual failure mode looks like when it happens.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.config_loader import ConfigBundle, ExtractionConfig, MatchPolicy
from engine.extract import (
    FailureKind,
    Proposal,
    match_score,
    value_in_quote,
    verify_proposal,
    verify_proposals,
)
from schemas.extraction import MatchMode
from tests.factories import make_text_layer

LETTER = (
    "Sehr geehrte Damen und Herren, ich beantrage die Regelaltersrente. "
    "Die Rentenart lautet regelaltersrente und der Rentenbeginn ist der "
    "2026-11-01. Meine Versicherungsnummer lautet [[PII|VSNR|QRSTVWXZ2345]]."
)
BORN_DIGITAL = make_text_layer(("part-text-0", "born_digital", LETTER))
SCAN = make_text_layer(("part-text-0", "ocr", LETTER))
KNOWN = frozenset({"rentenart", "rentenbeginn", "versicherungsnummer"})


def proposal(
    field: str = "rentenart",
    *,
    value: str = "regelaltersrente",
    quote: str | None = None,
    offset: int | None = None,
    part_id: str = "part-text-0",
    extractor_id: str = "replay:v4",
) -> Proposal:
    """A proposal that is true about ``LETTER`` unless a test breaks it."""
    resolved_quote = quote if quote is not None else f"Rentenart lautet {value}"
    return Proposal(
        field=field,
        value=value,
        quote=resolved_quote,
        part_id=part_id,
        offset=LETTER.find(resolved_quote) if offset is None else offset,
        extractor_id=extractor_id,
    )


@pytest.fixture
def extraction(config: ConfigBundle) -> ExtractionConfig:
    return config.extraction


# ------------------------------------------------------ both locks hold ---


def test_an_exact_proposal_becomes_a_record_with_its_span(
    extraction: ExtractionConfig,
) -> None:
    outcome = verify_proposal(proposal(), BORN_DIGITAL, config=extraction)
    assert outcome.accepted
    record = outcome.record
    assert record is not None
    assert record.field == "rentenart"
    assert record.value == "regelaltersrente"
    assert record.match_mode is MatchMode.EXACT
    # An exact record carries no score: it is 1.0 by definition, and saying so
    # twice would invite the two numbers to disagree.
    assert record.match_score is None
    assert record.confidence == extraction.confidence.exact
    assert record.extractor_id == "replay:v4"
    assert record.span is not None
    assert LETTER[record.span.start : record.span.end] == proposal().quote


def test_an_ocr_proposal_survives_one_wrong_character_and_carries_its_score(
    extraction: ExtractionConfig,
) -> None:
    """rn read as m is what a scan does; refusing it would mean refusing post."""
    text = LETTER.replace("Rentenart lautet", "Rentenart Iautet")
    layer = make_text_layer(("part-text-0", "ocr", text))
    claim = Proposal(
        field="rentenart",
        value="regelaltersrente",
        quote="Rentenart lautet regelaltersrente",
        part_id="part-text-0",
        offset=text.find("Rentenart Iautet"),
        extractor_id="replay:v4",
    )
    outcome = verify_proposal(claim, layer, config=extraction)
    assert outcome.accepted
    record = outcome.record
    assert record is not None
    assert record.match_mode is MatchMode.FUZZY
    assert record.match_score is not None
    assert (
        record.match_score
        >= extraction.policy_for(layer.parts[0].source_type).min_score
    )
    assert record.confidence >= extraction.confidence.fuzzy_floor


def test_a_placeholder_in_prose_is_a_correct_value(
    extraction: ExtractionConfig,
) -> None:
    """The letter now SAYS the placeholder; quoting it is right (ADR-019)."""
    token = "[[PII|VSNR|QRSTVWXZ2345]]"
    outcome = verify_proposal(
        Proposal(
            field="versicherungsnummer",
            value=token,
            quote=f"Versicherungsnummer lautet {token}",
            part_id="part-text-0",
            offset=LETTER.find(f"Versicherungsnummer lautet {token}"),
            extractor_id="llm:test",
        ),
        BORN_DIGITAL,
        config=extraction,
        known_fields=KNOWN,
    )
    assert outcome.accepted
    assert outcome.record is not None
    assert outcome.record.value == token


# ------------------------------------------------- one of them does not ---


def test_an_offset_that_is_off_by_one_is_discarded_never_repaired(
    extraction: ExtractionConfig,
) -> None:
    """Searching the neighbourhood would collapse two locks into one."""
    claim = proposal(offset=LETTER.find("Rentenart lautet") + 1)
    outcome = verify_proposal(claim, BORN_DIGITAL, config=extraction)
    assert not outcome.accepted
    assert outcome.failure is FailureKind.QUOTE_MISMATCH
    assert outcome.record is None


def test_a_quote_that_does_not_contain_its_value_is_a_summary_not_a_span(
    extraction: ExtractionConfig,
) -> None:
    claim = proposal(
        value="altersrente_langjaehrig", quote="Rentenart lautet regelaltersrente"
    )
    outcome = verify_proposal(claim, BORN_DIGITAL, config=extraction)
    assert outcome.failure is FailureKind.VALUE_NOT_IN_QUOTE
    # Lock one HELD - the text really does say that - and the proposal is still
    # discarded, which is the whole point of there being two.
    assert outcome.score == 1.0


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (proposal(value="  ", quote="Rentenart lautet"), FailureKind.EMPTY_VALUE),
        (proposal(quote=" "), FailureKind.EMPTY_VALUE),
        (proposal(part_id="part-text-9"), FailureKind.UNKNOWN_PART),
        (proposal(offset=-3), FailureKind.OFFSET_OUT_OF_RANGE),
        (proposal(offset=len(LETTER) - 2), FailureKind.OFFSET_OUT_OF_RANGE),
    ],
)
def test_every_other_failure_mode_is_a_discard(
    claim: Proposal, expected: FailureKind, extraction: ExtractionConfig
) -> None:
    outcome = verify_proposal(claim, BORN_DIGITAL, config=extraction)
    assert not outcome.accepted
    assert outcome.failure is expected


def test_a_field_the_procedure_does_not_declare_has_nowhere_to_go(
    extraction: ExtractionConfig,
) -> None:
    outcome = verify_proposal(
        proposal(field="lieblingsfarbe"),
        BORN_DIGITAL,
        config=extraction,
        known_fields=KNOWN,
    )
    assert outcome.failure is FailureKind.UNKNOWN_FIELD
    # An EMPTY set of known fields is "no procedure was derived", not "do not
    # ask": with nothing configured, nothing can be extracted.
    assert (
        verify_proposal(
            proposal(), BORN_DIGITAL, config=extraction, known_fields=frozenset()
        ).failure
        is FailureKind.UNKNOWN_FIELD
    )


def test_a_missing_layer_is_an_unknown_part(extraction: ExtractionConfig) -> None:
    assert (
        verify_proposal(proposal(), None, config=extraction).failure
        is FailureKind.UNKNOWN_PART
    )


def test_an_ocr_quote_that_drifts_too_far_is_still_a_discard(
    extraction: ExtractionConfig,
) -> None:
    claim = Proposal(
        field="rentenart",
        value="regelaltersrente",
        quote="Rentenart lautet regelaltersrente",
        part_id="part-text-0",
        # Pointed at a stretch of the letter that says something else entirely.
        offset=0,
        extractor_id="replay:v4",
    )
    outcome = verify_proposal(claim, SCAN, config=extraction)
    assert outcome.failure is FailureKind.QUOTE_MISMATCH
    assert (
        0.0
        <= outcome.score
        < extraction.policy_for(SCAN.parts[0].source_type).min_score
    )


# --------------------------------------------------------- batch rules ---


def test_the_first_accepted_proposal_for_a_field_wins(
    extraction: ExtractionConfig,
) -> None:
    outcomes = verify_proposals(
        (proposal(), proposal(quote="Rentenart lautet regelaltersrente")),
        BORN_DIGITAL,
        config=extraction,
        known_fields=KNOWN,
    )
    assert [outcome.accepted for outcome in outcomes] == [True, False]
    assert outcomes[1].failure is FailureKind.DUPLICATE_FIELD


def test_a_field_the_mapper_already_filled_is_a_visible_duplicate(
    extraction: ExtractionConfig,
) -> None:
    """Reading a JSON key is not an inference and prose is; the key wins, and
    the disagreement shows up in the histogram instead of vanishing."""
    outcomes = verify_proposals(
        (proposal(),),
        BORN_DIGITAL,
        config=extraction,
        known_fields=KNOWN,
        taken_fields=frozenset({"rentenart"}),
    )
    assert outcomes[0].failure is FailureKind.DUPLICATE_FIELD


def test_a_verification_describes_itself_without_quoting_anything(
    extraction: ExtractionConfig,
) -> None:
    """A rejected proposal is the text LEAST worth trusting, so the audit trail
    records its shape and never its content."""
    outcome = verify_proposal(proposal(offset=3), BORN_DIGITAL, config=extraction)
    described = outcome.describe()
    assert described == {
        "field": "rentenart",
        "part_id": "part-text-0",
        "offset": 3,
        "quote_length": len(proposal().quote),
        "extractor_id": "replay:v4",
        "accepted": False,
        "failure": "quote_mismatch",
        "score": described["score"],
    }
    rendered = str(described)
    assert "regelaltersrente" not in rendered
    assert "Rentenart" not in rendered


# ------------------------------------------------------- the two helpers ---


def test_match_score_short_circuits_on_identity_and_on_emptiness() -> None:
    assert match_score("abc", "abc") == 1.0
    assert match_score("", "abc") == 0.0
    assert match_score("abc", "") == 0.0
    assert 0.0 < match_score("regelaltersrente", "regelalterrsente") < 1.0


def test_value_in_quote_folds_whitespace_and_case_only() -> None:
    exact = MatchPolicy(mode="exact", min_score=1.0)
    fuzzy = MatchPolicy(mode="fuzzy", min_score=0.86)
    assert value_in_quote("regelaltersrente", "Rentenart: Regelaltersrente", exact)
    assert value_in_quote("Muster Weg", "Anschrift: Muster\n Weg", exact)
    assert not value_in_quote("", "Rentenart", exact)
    # A digit, an accent and a hyphen all still have to be there.
    assert not value_in_quote("17170459B012", "Nummer 1717O459BO12", exact)
    assert value_in_quote("17170459B012", "Nummer 1717O459B012", fuzzy)


# ------------------------------------------------------------ properties ---

TEXT = st.text(alphabet="abcdefg ", min_size=1, max_size=40)


@given(
    text=TEXT,
    offset=st.integers(min_value=-5, max_value=45),
    quote=st.text(alphabet="abcdefg ", min_size=1, max_size=10),
    value=st.text(alphabet="abcdefg", min_size=1, max_size=6),
)
def test_an_accepted_record_always_slices_out_what_it_claimed(
    config: ConfigBundle, text: str, offset: int, quote: str, value: str
) -> None:
    """Whatever an extractor says, an accepted record's span holds up."""
    layer = make_text_layer(("part-text-0", "born_digital", text))
    claim = Proposal(
        field="rentenart",
        value=value,
        quote=quote,
        part_id="part-text-0",
        offset=offset,
        extractor_id="fuzz",
    )
    outcome = verify_proposal(claim, layer, config=config.extraction)
    if not outcome.accepted:
        assert outcome.record is None
        assert outcome.failure is not None
        return
    record = outcome.record
    assert record is not None
    assert record.span is not None
    # Lock one held: the text at the offset IS the quote (exact policy).
    assert text[record.span.start : record.span.end] == quote
    # Lock two held: the quote carries the value.
    assert value.casefold() in " ".join(quote.split()).casefold()


@given(text=TEXT, shift=st.integers(min_value=1, max_value=8))
def test_a_shifted_offset_is_never_accepted_under_an_exact_policy(
    config: ConfigBundle, text: str, shift: int
) -> None:
    """The one thing the offset lock exists to make impossible."""
    layer = make_text_layer(("part-text-0", "born_digital", text))
    quote = text[:4] or text
    claim = Proposal(
        field="rentenart",
        value=quote.strip() or quote,
        quote=quote,
        part_id="part-text-0",
        offset=shift,
        extractor_id="fuzz",
    )
    outcome = verify_proposal(claim, layer, config=config.extraction)
    if outcome.accepted:
        # Only possible when the same characters really do stand there too,
        # which is a true statement about the text and not a repair.
        assert text[shift : shift + len(quote)] == quote
