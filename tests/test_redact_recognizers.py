"""German recognizers, the two profiles, and the union that merges them."""

from __future__ import annotations

from itertools import pairwise

import pytest

from engine.redact import Evidence, Kind, Profile, merge, verify_detector
from engine.redact.detector import Detector, redact_detector, redact_recognizers
from engine.redact.ner import (
    ENTITY_KINDS,
    NER_RECOGNIZER_ID,
    SPACY_MODEL,
    NerMember,
    PresidioNerMember,
    available,
    reset_cache,
    unavailable_reason,
)
from engine.redact.recall import DETERMINISTIC_GATE_KINDS
from engine.redact.recognizers import (
    Detection,
    iban_check_digits,
    iban_checksum_ok,
    steuer_id_check_digit,
    steuer_id_checksum_ok,
    vsnr_birthdate_plausible,
    vsnr_check_digit,
    vsnr_checksum_ok,
)

# Published example of the DRV Pruefziffer algorithm.
VALID_VSNR = "65170839J003"
# Published BZSt example of the ISO 7064 MOD 11,10 check.
VALID_STEUER_ID = "86095742719"
VALID_IBAN = "DE02120300000000202051"


def redact() -> Detector:
    """Deterministic REDACT union, never the optional model member."""
    return Detector(Profile.REDACT)


def kinds(detector: Detector, text: str) -> list[Kind]:
    return [hit.kind for hit in detector.scan(text)]


# ------------------------------------------------------------- checksums ---


def test_the_published_check_digit_examples_verify() -> None:
    assert vsnr_checksum_ok(VALID_VSNR)
    assert steuer_id_checksum_ok(VALID_STEUER_ID)
    assert iban_checksum_ok(VALID_IBAN)


@pytest.mark.parametrize(
    "value",
    ["65170839J004", "65170839J013", "6517083J0031", "nonsense", "", "65170839j003"],
)
def test_a_wrong_versicherungsnummer_fails_the_check_digit(value: str) -> None:
    assert vsnr_checksum_ok(value) is False


def test_the_check_digit_helpers_produce_valid_identifiers() -> None:
    assert vsnr_checksum_ok("65170839J00" + vsnr_check_digit("65170839J00"))
    assert steuer_id_checksum_ok("8609574271" + steuer_id_check_digit("8609574271"))
    bban = "120300000000202051"
    assert iban_checksum_ok("DE" + iban_check_digits("DE", bban) + bban)


@pytest.mark.parametrize(
    "value", ["06095742719", "8609574271", "86095742710", "860957427x9"]
)
def test_a_wrong_steuer_id_fails(value: str) -> None:
    assert steuer_id_checksum_ok(value) is False


@pytest.mark.parametrize("value", ["DE02120300000000202052", "DE0212030000", "XX"])
def test_a_wrong_iban_fails(value: str) -> None:
    assert iban_checksum_ok(value) is False


def test_an_iban_with_spaces_still_verifies() -> None:
    assert iban_checksum_ok("DE02 1203 0000 0000 2020 51")


def test_the_embedded_birthdate_is_checked_separately() -> None:
    assert vsnr_birthdate_plausible(VALID_VSNR)
    assert vsnr_birthdate_plausible("65993999J003") is False
    assert vsnr_birthdate_plausible("65") is False
    assert vsnr_birthdate_plausible("65AB0839J003") is False


# ---------------------------------------------------------- the profiles ---


def test_redact_finds_a_mistyped_versicherungsnummer_and_verify_does_not() -> None:
    """The whole point of the two profiles, in one assertion pair.

    A typo does not make a Versicherungsnummer less identifying, so REDACT must
    seal it. VERIFY decides whether to refuse a whole submission, so it insists
    on the checksum.
    """
    mistyped = "65170839J004"
    assert Kind.VSNR in kinds(redact(), f"Versicherungsnummer {mistyped}")
    assert kinds(verify_detector(), f"Versicherungsnummer {mistyped}") == []
    assert Kind.VSNR in kinds(verify_detector(), f"Versicherungsnummer {VALID_VSNR}")


def test_verify_ignores_an_eleven_digit_run_that_fails_the_steuer_check() -> None:
    text = "Die interne Zaehlernummer 12345678901 gehoert zum Postbuch."
    assert kinds(verify_detector(), text) == []
    # ... and REDACT deliberately over-redacts it, because a mistyped Steuer-ID
    # is still a Steuer-ID. Over-redaction costs utility, not privacy.
    assert kinds(redact(), text) == [Kind.STID]


def test_verify_does_not_fire_on_procedural_dates_or_amounts() -> None:
    """A gate that shouts about a Rentenbeginn is a gate somebody switches off."""
    payload_text = (
        "Rentenbeginn 2026-11-01, Eingang am 06.08.2026, Betrag 12345678 Cent, "
        "Betriebsnummer 45678901, Anschrift Kirchgasse 2, 24103 Beispielstadt."
    )
    assert verify_detector().scan(payload_text) == ()


def test_verify_flags_emails_and_checksummed_identifiers() -> None:
    text = (
        f"Kontakt poststelle@muster-beispiel.de, Konto {VALID_IBAN}, "
        f"Steuer-ID {VALID_STEUER_ID}"
    )
    assert set(kinds(verify_detector(), text)) == {Kind.EMAIL, Kind.IBAN, Kind.STID}


def test_verify_flags_text_that_only_imitates_the_placeholder_syntax() -> None:
    """A forged placeholder is residue: something is copying reserved syntax."""
    forged = "[[PII|VSNR|nichtechttoken]]"
    assert kinds(verify_detector(), forged) == [Kind.TEXT]
    # A well-formed placeholder is exactly what the working copy should carry.
    assert verify_detector().scan("[[PII|VSNR|BCDFGHJKMNPQ]]") == ()


def test_a_generic_foreign_iban_is_checksum_gated_in_both_profiles() -> None:
    """The generic shape matches too much to be trusted on format alone."""
    assert kinds(redact(), "Konto XX99ABCDEFGHIJK12345") == []
    assert Kind.IBAN in kinds(redact(), "Konto AT611904300234573201")


# ----------------------------------------------------- individual patterns ---


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Betriebsnummer 45678901 des Auftraggebers.", Kind.BNR),
        ("Aktenzeichen: S 12 R 4711/26", Kind.AKTZ),
        ("Rueckfragen an a.b@muster-beispiel.de.", Kind.EMAIL),
        ("Tel. 0431 4567890", Kind.TEL),
        ("Wohnhaft Kirchgasse 2 im Erdgeschoss.", Kind.ADDR),
        ("Anschrift: 24103 Beispielstadt", Kind.ADDR),
        ("geboren am 17.04.1959", Kind.GEBDAT),
        ("Auftraggeber ist die Meyer & Sohn GmbH.", Kind.ORG),
        ("Der Antrag wurde von Herrn Ansgar Vollbrecht unterschrieben.", Kind.NAME),
    ],
)
def test_each_deterministic_recognizer_fires_on_its_shape(
    text: str, kind: Kind
) -> None:
    assert kind in kinds(redact(), text)


def test_a_bare_eight_digit_number_is_not_a_betriebsnummer() -> None:
    """Context-labelled only: a bare eight-digit run is a Betrag far more often."""
    assert kinds(redact(), "Die Nachzahlung betraegt 45678901 Cent.") == []


def test_an_organisation_keeps_its_ampersand_and_its_legal_form() -> None:
    text = "Der Vertrag besteht mit der Meyer & Sohn GmbH seit 2024."
    hit = next(hit for hit in redact().scan(text) if hit.kind is Kind.ORG)
    assert text[hit.start : hit.end] == "Meyer & Sohn GmbH"


def test_a_court_aktenzeichen_keeps_its_registerzeichen() -> None:
    text = "Wir beziehen uns auf das Verfahren B 12 R 3/25 R vor dem Sozialgericht."
    hit = next(hit for hit in redact().scan(text) if hit.kind is Kind.AKTZ)
    assert text[hit.start : hit.end] == "B 12 R 3/25 R"


def test_a_sentence_final_email_keeps_its_domain_and_drops_the_full_stop() -> None:
    text = "Rueckfragen bitte an i.wegener@nordlicht-beispiel.de."
    hit = next(hit for hit in redact().scan(text) if hit.kind is Kind.EMAIL)
    assert text[hit.start : hit.end] == "i.wegener@nordlicht-beispiel.de"


def test_a_date_without_birth_context_is_not_a_birthdate() -> None:
    assert kinds(redact(), "Der Antrag ging am 06.08.2026 ein.") == []


# ------------------------------------------------------------ the union ---


def test_overlapping_hits_are_merged_longest_span_wins() -> None:
    long = Detection(start=0, end=22, kind=Kind.IBAN, recognizer_id="iban_de")
    short = Detection(start=4, end=12, kind=Kind.TEL, recognizer_id="telefon")
    assert merge([short, long]) == (long,)


def test_merging_is_a_function_of_the_set_not_of_the_order() -> None:
    hits = [
        Detection(start=0, end=5, kind=Kind.VSNR, recognizer_id="vsnr"),
        Detection(start=10, end=15, kind=Kind.STID, recognizer_id="steuer_id"),
        Detection(start=0, end=5, kind=Kind.AKTZ, recognizer_id="aktenzeichen_court"),
    ]
    assert merge(hits) == merge(list(reversed(hits)))


def test_merged_output_is_sorted_and_disjoint() -> None:
    text = (
        f"Versicherungsnummer {VALID_VSNR}, Steuer-ID {VALID_STEUER_ID}, "
        f"Konto {VALID_IBAN}, geboren am 17.04.1959"
    )
    hits = redact().scan(text)
    assert [hit.start for hit in hits] == sorted(hit.start for hit in hits)
    for earlier, later in pairwise(hits):
        assert earlier.end <= later.start


def test_an_empty_text_produces_nothing() -> None:
    assert redact().scan("") == ()


def test_the_union_covers_every_gated_kind() -> None:
    """A gated kind with no recognizer would score 1.000 by having no labels."""
    available_kinds = {recognizer.kind for recognizer in redact_recognizers()}
    assert available_kinds >= DETERMINISTIC_GATE_KINDS


def test_the_inventory_describes_what_ran() -> None:
    inventory = verify_detector().inventory()
    assert inventory["profile"] == "verify"
    recognizers = inventory["recognizers"]
    assert isinstance(recognizers, list)
    assert "vsnr" in recognizers
    assert inventory["ner"] is None


# ---------------------------------------------------- the optional member ---


def test_the_package_works_without_the_optional_extra() -> None:
    """Core install: engine.redact imports, and the union simply has one member
    fewer. Whether the extra is here on this machine is reported, not required.
    """
    detector = redact_detector(with_ner=False)
    assert detector.uses_ner is False
    assert detector.scan(f"Versicherungsnummer {VALID_VSNR}")
    if not available():
        assert unavailable_reason() is not None


def test_the_ner_entity_map_only_carries_kinds_we_seal() -> None:
    assert set(ENTITY_KINDS.values()) == {Kind.NAME, Kind.ADDR, Kind.ORG}


def test_a_stub_ner_member_joins_the_union() -> None:
    """The union contract, exercised without needing Presidio installed."""

    class StubNer:
        def scan(self, text: str) -> tuple[Detection, ...]:
            index = text.find("Vollbrecht")
            if index < 0:
                return ()
            return (
                Detection(
                    start=index,
                    end=index + len("Vollbrecht"),
                    kind=Kind.NAME,
                    recognizer_id="stub",
                ),
            )

        def describe(self) -> dict[str, object]:
            return {"recognizer_id": "stub"}

    stub = StubNer()
    assert isinstance(stub, NerMember)
    detector = Detector(Profile.REDACT, ner=stub)
    assert detector.uses_ner is True
    assert Kind.NAME in kinds(detector, "Zustaendig ist Vollbrecht im Referat.")
    assert detector.inventory()["ner"] == {"recognizer_id": "stub"}


# ------------------------------------------- the optional member, in detail ---
#
# The Presidio adapter cannot be exercised on a core install, so it is tested
# against a stand-in analyzer with the same call shape. That covers the mapping
# and the filtering; what it deliberately does not cover is whether Presidio
# itself behaves, which is what the skip-marked recall test is for.


class _Result:
    """The shape presidio_analyzer.RecognizerResult presents to us."""

    def __init__(self, entity_type: str, start: int, end: int) -> None:
        self.entity_type = entity_type
        self.start = start
        self.end = end


class _Analyzer:
    def __init__(self, results: list[_Result]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def analyze(self, text: str, language: str, entities: list[str]) -> list[_Result]:
        self.calls.append(
            {"text": text, "language": language, "entities": list(entities)}
        )
        return self.results


def test_the_presidio_adapter_maps_entities_onto_placeholder_kinds() -> None:
    analyzer = _Analyzer(
        [
            _Result("PERSON", 0, 6),
            _Result("LOCATION", 10, 20),
            _Result("ORGANIZATION", 25, 30),
            # Anything outside the map is dropped: DATE_TIME would fire on
            # procedural content the structured plane legitimately reads.
            _Result("DATE_TIME", 40, 50),
        ]
    )
    member = PresidioNerMember(analyzer)
    hits = member.scan("Vollbrecht wohnt in Beispielstadt bei der Firma am 01.01.")
    assert [hit.kind for hit in hits] == [Kind.NAME, Kind.ADDR, Kind.ORG]
    assert all(hit.recognizer_id == NER_RECOGNIZER_ID for hit in hits)
    assert analyzer.calls[0]["language"] == "de"
    assert analyzer.calls[0]["entities"] == ["LOCATION", "ORGANIZATION", "PERSON"]


def test_the_presidio_adapter_describes_itself_for_the_report() -> None:
    described = PresidioNerMember(_Analyzer([])).describe()
    assert described["model"] == SPACY_MODEL
    assert described["recognizer_id"] == NER_RECOGNIZER_ID


def test_the_member_cache_can_be_reset() -> None:
    """Tests that patch the import path need a way back to a cold start."""
    before = available()
    reset_cache()
    assert available() is before


# ------------------------------------------- evidence classes in the merge ---
#
# Found by running the recall metric with the [redact] extra installed: spaCy
# tagged `DE53375756206830111642.` as an ORGANIZATION, one character longer than
# the mod-97-verified IBAN underneath it, and a plain longest-span-wins rule
# handed a bank account to a model's guess. Two rules came out of it, and both
# are load-bearing.


def _hit(
    start: int,
    end: int,
    kind: Kind,
    recognizer_id: str,
    evidence: Evidence = Evidence.PATTERN,
) -> Detection:
    return Detection(
        start=start,
        end=end,
        kind=kind,
        recognizer_id=recognizer_id,
        evidence=evidence,
    )


def test_a_checksummed_hit_keeps_its_kind_against_a_longer_model_guess() -> None:
    iban = _hit(0, 22, Kind.IBAN, "iban_de", Evidence.CHECKSUM)
    guess = _hit(0, 23, Kind.ORG, "presidio_spacy_de", Evidence.MODEL)
    merged = merge([guess, iban])
    assert len(merged) == 1
    assert merged[0].kind is Kind.IBAN
    # ... and the extra character the model saw is still covered. Preferring the
    # stronger evidence must never shrink what gets redacted.
    assert (merged[0].start, merged[0].end) == (0, 23)


def test_a_pattern_hit_outranks_a_model_hit_of_the_same_span() -> None:
    """spaCy calls e-mail addresses organisations; the pattern knows better."""
    email = _hit(0, 20, Kind.EMAIL, "email")
    guess = _hit(0, 20, Kind.ORG, "presidio_spacy_de", Evidence.MODEL)
    assert merge([guess, email])[0].kind is Kind.EMAIL


def test_a_model_hit_wins_where_no_deterministic_hit_exists() -> None:
    guess = _hit(5, 15, Kind.NAME, "presidio_spacy_de", Evidence.MODEL)
    assert merge([guess]) == (guess,)


def test_a_format_hit_with_a_failed_checksum_does_not_claim_the_top_class() -> None:
    """It is still redacted; it just has not proved anything about itself."""
    hits = list(
        redact_recognizers()[0].scan("Versicherungsnummer 65170839J004", Profile.REDACT)
    )
    assert hits[0].evidence is Evidence.PATTERN
    valid = list(
        redact_recognizers()[0].scan(
            f"Versicherungsnummer {VALID_VSNR}", Profile.REDACT
        )
    )
    assert valid[0].evidence is Evidence.CHECKSUM


def test_transitively_overlapping_hits_become_one_covering_span() -> None:
    """Coverage is the union; nothing a member saw is dropped in a merge."""
    left = _hit(0, 10, Kind.ADDR, "plz_ort")
    middle = _hit(8, 20, Kind.TEL, "telefon")
    right = _hit(18, 30, Kind.ORG, "organisation_rechtsform")
    merged = merge([right, left, middle])
    assert len(merged) == 1
    assert (merged[0].start, merged[0].end) == (0, 30)


def test_touching_but_not_overlapping_hits_stay_separate() -> None:
    left = _hit(0, 10, Kind.ADDR, "plz_ort")
    right = _hit(10, 20, Kind.TEL, "telefon")
    assert len(merge([left, right])) == 2
