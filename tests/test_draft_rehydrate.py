"""The placeholder round-trip property, and the refusals that make it hold.

**This module carries one of the project's non-negotiable gates** (ADR-002,
ADR-023): every rendered output resolves all placeholders, and an
unknown placeholder is a hard error. It is a Hypothesis property rather than an
example, because the interesting failures are the values nobody would think to
write down - a house number that is a number rather than a string, an address
subtree with a key the formatter has never seen, a value that is nothing but
whitespace, a token that differs from a real one in one character.

The three properties, and what each of them would catch:

1. *Everything resolves and nothing survives.* A formatter that dropped a
   field, a substitution that missed the second mention of a token, a template
   that emitted a placeholder the record does not carry.
2. *A corrupted token always hard-errors.* The property that makes an invented
   placeholder unable to resolve to somebody else's data.
3. *Whatever the vault holds, the rendered form still carries it.* The
   round-trip check, compared against the RAW as-received value rather than
   against a normalized copy of it (ADR-018's open thread for this part).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from engine.draft import (
    RehydrationError,
    Rehydrator,
    format_address,
    format_value,
    placeholders_by_path,
    rehydrate,
    round_trip_ok,
)
from engine.notify import NotificationRenderError, render_text
from engine.redact import (
    ALPHABET,
    TOKEN_LENGTH,
    InMemoryVaultStore,
    Kind,
    SealedEntry,
    VaultRecord,
)

FIXED = datetime(2026, 8, 6, 7, 21, tzinfo=UTC)

#: Prose the letters in this module are made of. Deliberately free of brackets
#: and pipes, so a generated letter can never contain placeholder-shaped text by
#: accident - that case has its own test rather than a random one.
PROSE = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd", "Zs"), whitelist_characters=".,;-\n"
    ),
    max_size=40,
)

tokens = st.text(alphabet=ALPHABET, min_size=TOKEN_LENGTH, max_size=TOKEN_LENGTH)

#: Values that are not blank and do not imitate the reserved syntax. Both
#: exclusions are behaviours with their own tests below: a blank sealed value
#: and a sealed value shaped like a placeholder are hard errors, and mixing them
#: into the general property would only make it test the error path twice.
scalars = st.one_of(
    st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd", "Zs", "Po"),
            blacklist_characters="[]|",
        ),
        min_size=1,
        max_size=30,
    ).filter(lambda text: text.strip() != ""),
    st.integers(min_value=-10_000, max_value=10_000),
    st.booleans(),
)

address_values = st.dictionaries(
    keys=st.sampled_from(
        ["strasse", "hausnummer", "plz", "ort", "land", "adresszusatz", "kanton"]
    ),
    values=scalars,
    min_size=1,
    max_size=5,
)


def build_record(
    entries: list[SealedEntry], vault_ref: str = "vault-TEST"
) -> VaultRecord:
    return VaultRecord(
        vault_ref=vault_ref, case_id="case-x", created_at=FIXED, entries=tuple(entries)
    )


def sealed(kind: Kind, token: str, value: Any, **extra: Any) -> SealedEntry:
    return SealedEntry(
        kind=kind,
        token=token,
        value_json=json.dumps(value, ensure_ascii=False),
        **extra,
    )


@st.composite
def sealed_records(draw: st.DrawFn) -> VaultRecord:
    """A vault record with 1 to 4 entries of mixed kinds."""
    drawn = draw(st.lists(tokens, min_size=1, max_size=4, unique=True))
    entries: list[SealedEntry] = []
    for token in drawn:
        kind = draw(st.sampled_from(list(Kind)))
        value = draw(address_values if kind is Kind.ADDR else scalars)
        entries.append(sealed(kind, token, value))
    return build_record(entries)


@st.composite
def letters(draw: st.DrawFn) -> tuple[VaultRecord, str, int]:
    """A record, a text mentioning its tokens, and how many mentions there are."""
    record = draw(sealed_records())
    mentions: list[str] = []
    body = draw(PROSE)
    for entry in record.entries:
        for _ in range(draw(st.integers(min_value=0, max_value=2))):
            mentions.append(entry.placeholder)
            body += f"\n{draw(PROSE)} {entry.placeholder} {draw(PROSE)}"
    return record, body, len(mentions)


# ------------------------------------------------------- the properties ---


@given(letters())
def test_every_placeholder_resolves_and_none_survives(
    case: tuple[VaultRecord, str, int],
) -> None:
    """Property 1: the round trip, over arbitrary sealed payloads and letters."""
    record, text, mention_count = case
    result = rehydrate(text, record=record)
    assert result.resolved_tokens == mention_count
    assert "[[PII|" not in result.text
    assert "]]" not in result.text
    for entry in record.entries:
        if entry.placeholder in text:
            packed = "".join(format_value(entry).split())
            assert packed in "".join(result.text.split())


@given(letters(), tokens)
def test_an_unknown_token_is_always_a_hard_error(
    case: tuple[VaultRecord, str, int], other: str
) -> None:
    """Property 2: an invented placeholder blocks the draft, always."""
    record, text, mention_count = case
    assume(mention_count > 0)
    assume(other not in record.tokens)
    entry = next(item for item in record.entries if item.placeholder in text)
    corrupted = text.replace(
        entry.placeholder, f"[[PII|{entry.kind.value}|{other}]]", 1
    )
    with pytest.raises(RehydrationError, match="does not contain"):
        rehydrate(corrupted, record=record)


@given(sealed_records())
def test_the_rendered_form_still_carries_the_raw_value(record: VaultRecord) -> None:
    """Property 3: normalize for display, compare against the RAW form."""
    for entry in record.entries:
        assert round_trip_ok(entry, format_value(entry))


# ------------------------------------------------ the shapes part 04/05 left ---


def test_a_value_re_hydrates_from_the_raw_whitespace_as_received() -> None:
    """The vault stores ``" \\t17170459B012  "`` because that is what arrived."""
    entry = sealed(Kind.VSNR, "B" * 12, " \t17170459B012  ", path="a.b")
    record = build_record([entry])
    result = rehydrate(f"Versicherungsnummer: {entry.placeholder}", record=record)
    assert result.text == "Versicherungsnummer: 17170459B012"
    assert round_trip_ok(entry, "17170459B012")


def test_an_address_subtree_is_rendered_by_one_formatter() -> None:
    """ADDR entries are JSON objects, not strings (part-04 finding)."""
    value = {
        "strasse": " Kirchgasse \t",
        "hausnummer": 2,
        "plz": "24103",
        "ort": "Kiel",
    }
    entry = sealed(Kind.ADDR, "C" * 12, value, path="antragsteller.anschrift")
    assert format_address(value) == "Kirchgasse 2\n24103 Kiel"
    assert round_trip_ok(entry, format_address(value))


def test_an_unknown_address_key_is_appended_rather_than_dropped() -> None:
    """A formatter that silently lost a field would fail its own round trip."""
    value = {"strasse": "Hauptweg", "hausnummer": "1", "kanton": "Zug"}
    rendered = format_address(value)
    assert rendered.splitlines() == ["Hauptweg 1", "Zug"]
    assert round_trip_ok(sealed(Kind.ADDR, "D" * 12, value), rendered)


def test_two_mentions_of_one_value_carry_two_tokens_and_both_resolve() -> None:
    """Part-05 finding: re-hydration is per token and never per value."""
    first = sealed(Kind.NAME, "F" * 12, "Zenobia Musterfrau", part_id="part-text-0")
    second = sealed(Kind.NAME, "G" * 12, "Zenobia Musterfrau", part_id="part-text-0")
    record = build_record([first, second])
    result = rehydrate(
        f"{first.placeholder} und nochmals {second.placeholder}", record=record
    )
    assert result.text == "Zenobia Musterfrau und nochmals Zenobia Musterfrau"
    assert result.resolved_tokens == 2
    assert result.distinct_tokens == 2
    # A prose entry carries part_id and span and NO path, so it never turns up
    # in an addressee block. An invented path would point at nothing.
    assert placeholders_by_path(record) == {}


def test_the_same_token_twice_resolves_twice_and_counts_once_as_distinct() -> None:
    entry = sealed(Kind.VSNR, "H" * 12, "17170459B012", path="a.b")
    result = rehydrate(
        f"{entry.placeholder} / {entry.placeholder}", record=build_record([entry])
    )
    assert result.resolved_tokens == 2
    assert result.distinct_tokens == 1
    assert result.kinds == {"VSNR": 2}
    assert result.summary()["kinds"] == {"VSNR": 2}


def test_placeholders_by_path_maps_only_payload_entries() -> None:
    scalar = sealed(Kind.VSNR, "J" * 12, "17170459B012", path="antragsteller.vsnr")
    prose = sealed(Kind.NAME, "K" * 12, "Name", part_id="part-text-0", span=(3, 7))
    record = build_record([scalar, prose])
    assert placeholders_by_path(record) == {"antragsteller.vsnr": scalar.placeholder}


# ----------------------------------------------------------- the refusals ---


def test_a_kind_that_disagrees_with_the_vault_is_a_hard_error() -> None:
    entry = sealed(Kind.VSNR, "M" * 12, "17170459B012")
    with pytest.raises(RehydrationError, match="calls it a VSNR"):
        rehydrate(f"[[PII|NAME|{'M' * 12}]]", record=build_record([entry]))


@pytest.mark.parametrize(
    "damaged",
    [
        "[[PII|VSNR|TOOSHORT]]",
        "[[PII|VSNR|" + "M" * 12 + "]",
        "[[ PII |VSNR|" + "M" * 12 + "]]",
        "[[PII|NOSUCHKIND|" + "M" * 12 + "]]",
    ],
)
def test_placeholder_shaped_text_that_is_not_a_placeholder_blocks_the_draft(
    damaged: str,
) -> None:
    record = build_record([sealed(Kind.VSNR, "M" * 12, "17170459B012")])
    with pytest.raises(RehydrationError):
        rehydrate(f"Vorgang {damaged} Ende", record=record)


def test_a_sealed_value_that_imitates_the_reserved_syntax_blocks_the_draft() -> None:
    """Part 04 auto-seals exactly this residue; re-hydrating it is not allowed.

    A reader cannot tell a re-hydrated ``[[PII|...]]`` from a token that was
    never substituted, so the draft is discarded rather than posted with it.
    """
    entry = sealed(Kind.TEXT, "N" * 12, "frueher [[PII|VSNR|ABCDEFGHJKMN]]")
    with pytest.raises(RehydrationError, match="survives"):
        rehydrate(f"Hinweis: {entry.placeholder}", record=build_record([entry]))


def test_a_blank_sealed_value_blocks_the_draft() -> None:
    entry = sealed(Kind.NAME, "P" * 12, "   \t ")
    with pytest.raises(RehydrationError, match="empty string"):
        rehydrate(entry.placeholder, record=build_record([entry]))


def test_a_formatter_that_loses_a_field_fails_the_round_trip() -> None:
    """The check exists to catch exactly this, so it is tested by hand."""
    value = {"strasse": "Hauptweg", "hausnummer": "1", "plz": "10115", "ort": "Berlin"}
    entry = sealed(Kind.ADDR, "Q" * 12, value)
    assert not round_trip_ok(entry, "Hauptweg\n10115 Berlin")
    assert not round_trip_ok(sealed(Kind.VSNR, "R" * 12, "17170459B012"), "1717045")


def test_an_address_the_formatter_cannot_flatten_blocks_the_draft() -> None:
    """A nested subtree the formatter drops fails its own round trip.

    The formatter prints scalars; an address that carries an object under one
    of its keys would lose that object silently. It does not: the round-trip
    check counts the leaves and the draft stops. A null value alongside it is
    fine - there is nothing to lose.
    """
    value = {
        "strasse": "Hauptweg",
        "hausnummer": "1",
        "zustellung": {"co": "Musterfirma", "abteilung": "Poststelle"},
        "adresszusatz": None,
    }
    entry = sealed(Kind.ADDR, "W" * 12, value)
    assert "Musterfirma" not in format_value(entry)
    assert not round_trip_ok(entry, format_value(entry))
    with pytest.raises(RehydrationError, match="round-trip check failed"):
        rehydrate(entry.placeholder, record=build_record([entry]))


def test_an_unreadable_vault_record_blocks_the_draft() -> None:
    """A missing record is not "draft it without the identity"."""
    rehydrator = Rehydrator(InMemoryVaultStore())
    with pytest.raises(RehydrationError, match="could not be read"):
        rehydrator.record("vault-NOSUCHTHING")


def test_a_list_valued_entry_renders_one_line_per_item() -> None:
    entry = sealed(Kind.TEXT, "S" * 12, ["erste Zeile", "zweite Zeile"])
    record = build_record([entry])
    assert rehydrate(entry.placeholder, record=record).text == (
        "erste Zeile\nzweite Zeile"
    )


def test_the_rehydrator_renders_against_a_fetched_record() -> None:
    entry = sealed(Kind.VSNR, "T" * 12, "17170459B012")
    vault = InMemoryVaultStore()
    vault.seal(build_record([entry], vault_ref="vault-ONE"))
    rehydrator = Rehydrator(vault)
    record = rehydrator.record("vault-ONE")
    assert rehydrator.render(entry.placeholder, record=record).text == "17170459B012"


# ------------------------------------------------------------- the seam ---


def test_the_notification_renderer_still_refuses_what_this_module_produces() -> None:
    """Part 07's seam, asserted from the other side (ruling 1).

    The two renderers are different modules producing different artifact
    classes. This test fails the moment somebody adds a re-hydration flag to
    the notification path.
    """
    entry = sealed(Kind.VSNR, "V" * 12, "17170459B012")
    with pytest.raises(NotificationRenderError, match="redaction placeholder"):
        render_text("Ihre Nummer: {{ value }}", {"value": entry.placeholder}, label="x")
