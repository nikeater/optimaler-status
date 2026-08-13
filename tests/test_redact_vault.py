"""Placeholders, the policy loader, the vault backends and the seal/verify pass."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from engine.redact import (
    ALPHABET,
    TOKEN_LENGTH,
    IdentityFieldsPolicy,
    InMemoryVaultStore,
    JsonlVaultStore,
    Kind,
    PlaceholderError,
    PlaceholderRegistry,
    PolicyError,
    RedactionRefusedError,
    Reveal,
    SealedEntry,
    SecretsTokenSource,
    SeededTokenSource,
    VaultRecord,
    VaultStore,
    Witness,
    check_witnessless_seals,
    contains_placeholder,
    find_placeholders,
    format_placeholder,
    load_policy,
    parse_placeholder,
    redact_payload,
    scalar_text,
    seal_payload,
    verify_payload,
    verify_texts,
)
from engine.redact.placeholders import VAULT_REF_LENGTH
from engine.redact.policy import default_policy
from engine.redact.seal import path_steps, placeholder_tokens, seal_leaf
from engine.redact.vault import (
    DEV_BACKEND_NOTICE,
    DuplicateVaultRecordError,
    UnknownVaultRefError,
    build_record,
)

FIXED = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
CANARY_VSNR = "65170839J003"


@pytest.fixture
def policy() -> IdentityFieldsPolicy:
    return load_policy()


@pytest.fixture
def registry() -> PlaceholderRegistry:
    return PlaceholderRegistry(SeededTokenSource(11))


# ------------------------------------------------------------ placeholders ---


def test_the_alphabet_has_no_vowels_and_no_lookalikes() -> None:
    assert not set(ALPHABET) & set("AEIOU01ILO")
    assert len(set(ALPHABET)) == len(ALPHABET) == 27


def test_a_placeholder_round_trips_through_the_one_parse_definition() -> None:
    text = format_placeholder(Kind.VSNR, "BCDFGHJKMNPQ")
    assert text == "[[PII|VSNR|BCDFGHJKMNPQ]]"
    parsed = parse_placeholder(text)
    assert parsed is not None
    assert (parsed.kind, parsed.token) == (Kind.VSNR, "BCDFGHJKMNPQ")


def test_parsing_requires_the_whole_string_to_be_one_placeholder() -> None:
    """Witness resolution may only fire on an exact match; prose is part 05."""
    embedded = "Wert [[PII|VSNR|BCDFGHJKMNPQ]] im Feld"
    assert parse_placeholder(embedded) is None
    assert contains_placeholder(embedded) is True
    assert len(find_placeholders(embedded)) == 1


@pytest.mark.parametrize(
    "text",
    [
        "[[PII|VSNR|BCDFGHJKMNP]]",  # eleven characters
        "[[PII|VSNR|BCDFGHJKMNPA]]",  # A is not in the alphabet
        "[[PII|VSNR|bcdfghjkmnpq]]",  # lower case
        "[[VSNR|BCDFGHJKMNPQ]]",
        "Guten Tag, anbei der Antrag.",
        "",
    ],
)
def test_text_that_only_looks_like_a_placeholder_does_not_parse(text: str) -> None:
    assert parse_placeholder(text) is None


def test_surrounding_whitespace_is_tolerated_on_parse() -> None:
    assert parse_placeholder("  [[PII|ADDR|BCDFGHJKMNPQ]] ") is not None


def test_a_registry_never_issues_the_same_token_twice(
    registry: PlaceholderRegistry,
) -> None:
    tokens = {registry.mint(Kind.VSNR).token for _ in range(200)}
    assert len(tokens) == 200
    assert registry.issued == tokens


def test_a_token_the_document_already_contains_is_redrawn() -> None:
    source = SeededTokenSource(3)
    first = SeededTokenSource(3).token()
    registry = PlaceholderRegistry(source, reserved=f"Antrag Nummer {first}")
    assert registry.mint(Kind.TEXT).token != first


def test_an_exhausted_token_source_raises_rather_than_reusing() -> None:
    class Constant:
        def token(self, length: int = TOKEN_LENGTH) -> str:
            return "BCDFGHJKMNPQ"[:length]

    registry = PlaceholderRegistry(Constant())
    registry.mint(Kind.VSNR)
    with pytest.raises(PlaceholderError, match="could not draw"):
        registry.mint(Kind.VSNR)


def test_the_avoid_hook_can_reject_a_token(registry: PlaceholderRegistry) -> None:
    rejected: list[str] = []

    def avoid(token: str) -> bool:
        if len(rejected) < 2:
            rejected.append(token)
            return True
        return False

    minted = registry.mint(Kind.TEXT, avoid=avoid)
    assert minted.token not in rejected


def test_a_vault_ref_is_long_and_not_derivable(registry: PlaceholderRegistry) -> None:
    ref = registry.vault_ref()
    assert ref.startswith("vault-")
    assert len(ref) == len("vault-") + VAULT_REF_LENGTH
    assert set(ref[len("vault-") :]) <= set(ALPHABET)


def test_the_secrets_source_produces_alphabet_tokens() -> None:
    token = SecretsTokenSource().token()
    assert len(token) == TOKEN_LENGTH
    assert set(token) <= set(ALPHABET)


def test_a_placeholder_repr_does_not_print_its_token() -> None:
    placeholder = parse_placeholder("[[PII|VSNR|BCDFGHJKMNPQ]]")
    assert placeholder is not None
    assert "BCDFGHJKMNPQ" not in repr(placeholder)
    assert placeholder == parse_placeholder(str(placeholder))
    assert (placeholder == "not a placeholder") is False
    assert len({placeholder, parse_placeholder(str(placeholder))}) == 1


# ------------------------------------------------------------------ policy ---


def test_the_shipped_policy_declares_the_identity_paths(
    policy: IdentityFieldsPolicy,
) -> None:
    assert policy.policy_id == "identity_fields_v1"
    assert set(policy.paths) == {
        "antragsteller.name",
        "antragsteller.versicherungsnummer",
        "antragsteller.geburtsdatum",
        "antragsteller.anschrift",
        "auftraggeber.firmenname",
        "auftraggeber.anschrift",
        "auftraggeber.betriebsnummer",
    }
    address = policy.field_at("antragsteller.anschrift")
    assert address is not None
    assert address.subtree is True
    assert address.witness is False
    assert address.reveal is Reveal.NEVER
    assert policy.covering("antragsteller.anschrift.plz") is address
    assert policy.covering("antrag.rentenart") is None
    assert policy.field_at("antrag.rentenart") is None


def test_the_default_policy_is_the_configured_one(
    policy: IdentityFieldsPolicy,
) -> None:
    assert default_policy().policy_id == policy.policy_id


def test_sealed_field_ids_are_derived_per_procedure(
    policy: IdentityFieldsPolicy,
) -> None:
    field_paths = {
        "geburtsdatum": "antragsteller.geburtsdatum",
        "rentenart": "antrag.rentenart",
        "auftraggeber_name": "auftraggeber.firmenname",
    }
    assert policy.sealed_field_ids(field_paths) == {"geburtsdatum", "auftraggeber_name"}
    assert policy.value_free_field_ids(field_paths) == {
        "geburtsdatum",
        "auftraggeber_name",
    }
    assert "antragsteller.anschrift" not in policy.witness_paths()


def test_a_subtree_seal_may_not_claim_a_witness_entry(tmp_path: Path) -> None:
    """There is no scalar for a validator to compute on, so the promise is void."""
    document = {
        "policy_id": "broken",
        "version": "broken_v1",
        "fields": [
            {
                "path": "antragsteller.anschrift",
                "kind": "ADDR",
                "subtree": True,
                "witness": True,
            }
        ],
    }
    with pytest.raises(PolicyError, match="subtree seal cannot participate"):
        _write_policy(tmp_path, document)


def test_a_policy_with_nested_identity_paths_is_refused(tmp_path: Path) -> None:
    document = {
        "policy_id": "broken",
        "version": "broken_v1",
        "fields": [
            {
                "path": "antragsteller",
                "kind": "TEXT",
                "subtree": True,
                "witness": False,
            },
            {"path": "antragsteller.geburtsdatum", "kind": "GEBDAT"},
        ],
    }
    with pytest.raises(PolicyError, match="sits under"):
        _write_policy(tmp_path, document)


def test_a_duplicate_identity_path_is_refused(tmp_path: Path) -> None:
    document = {
        "policy_id": "broken",
        "version": "broken_v1",
        "fields": [
            {"path": "antragsteller.geburtsdatum", "kind": "GEBDAT"},
            {"path": "antragsteller.geburtsdatum", "kind": "TEXT"},
        ],
    }
    with pytest.raises(PolicyError, match="duplicate identity path"):
        _write_policy(tmp_path, document)


def test_an_empty_path_is_refused(tmp_path: Path) -> None:
    document = {
        "policy_id": "broken",
        "version": "broken_v1",
        "fields": [{"path": "  ", "kind": "TEXT"}],
    }
    with pytest.raises(PolicyError, match="needs a path"):
        _write_policy(tmp_path, document)


def test_a_missing_or_ambiguous_policy_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "redaction").mkdir()
    with pytest.raises(PolicyError, match="expected exactly one"):
        load_policy(tmp_path)
    (tmp_path / "redaction" / "a.yaml").write_text("- not a mapping", encoding="utf-8")
    with pytest.raises(PolicyError, match="must contain a YAML mapping"):
        load_policy(tmp_path)


def test_the_witnessless_seal_check_names_the_offending_path(
    policy: IdentityFieldsPolicy,
) -> None:
    """Sealing without a witness plus a field_map would validate a token."""
    assert check_witnessless_seals(policy, ["antragsteller.geburtsdatum"]) == []
    problems = check_witnessless_seals(policy, ["antragsteller.anschrift"])
    assert len(problems) == 1
    assert "without a witness entry" in problems[0]


def _write_policy(tmp_path: Path, document: dict[str, Any]) -> IdentityFieldsPolicy:
    directory = tmp_path / "redaction"
    directory.mkdir(exist_ok=True)
    (directory / "policy.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    return load_policy(tmp_path)


# -------------------------------------------------------------------- seal ---


PAYLOAD: dict[str, Any] = {
    "antragsteller": {
        "versicherungsnummer": " 17170459B012 ",
        "geburtsdatum": "1959-04-17",
        "anschrift": {"strasse": "Kirchgasse", "plz": "24103", "ort": "Beispielstadt"},
    },
    "auftraggeber": {
        "firmenname": "Nordlicht Systemhaus GmbH",
        "betriebsnummer": 45678901,
    },
    "antrag": {"rentenart": "regelaltersrente", "notizen": ["nichts besonderes"]},
}


def test_sealing_replaces_values_and_leaves_the_rest_alone(
    policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    outcome = seal_payload(PAYLOAD, policy=policy, registry=registry)
    # Five of the seven policy paths are present in this payload; the two that
    # are not (antragsteller.name, auftraggeber.anschrift) stay unsealed, which
    # is the presence invariant.
    assert outcome.sealed_count == 5
    assert set(outcome.sealed_paths) < set(policy.paths)
    assert outcome.payload["antrag"] == PAYLOAD["antrag"]
    serialized = json.dumps(outcome.payload, ensure_ascii=False)
    for secret in ("17170459B012", "1959-04-17", "Kirchgasse", "Nordlicht", "45678901"):
        assert secret not in serialized


def test_sealing_does_not_mutate_the_caller_payload(
    policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    before = json.dumps(PAYLOAD, sort_keys=True)
    seal_payload(PAYLOAD, policy=policy, registry=registry)
    assert json.dumps(PAYLOAD, sort_keys=True) == before


def test_the_witness_hands_over_what_the_mapper_would_have_produced(
    policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    """Whitespace stripped, exactly like engine.extract.mapper."""
    outcome = seal_payload(PAYLOAD, policy=policy, registry=registry)
    sealed = outcome.payload["antragsteller"]["versicherungsnummer"]
    assert outcome.witness.resolve(sealed) == "17170459B012"
    assert outcome.witness.knows(sealed) is True
    assert outcome.witness.knows("[[PII|VSNR|BCDFGHJKMNPQ]]") is False
    number = outcome.payload["auftraggeber"]["betriebsnummer"]
    assert outcome.witness.resolve(number) == "45678901"


def test_the_witness_carries_no_entry_for_a_subtree(
    policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    outcome = seal_payload(PAYLOAD, policy=policy, registry=registry)
    address = outcome.payload["antragsteller"]["anschrift"]
    assert isinstance(address, str)
    assert outcome.witness.resolve(address) is None
    assert len(outcome.witness) == 4
    assert bool(outcome.witness) is True
    assert len(outcome.witness.tokens) == 4


def test_two_witnesses_merge_without_exposing_their_contents() -> None:
    left = Witness({"[[PII|VSNR|BCDFGHJKMNPQ]]": "a"})
    right = Witness({"[[PII|ADDR|BCDFGHJKMNPR]]": "b"})
    merged = left.merged(right)
    assert len(merged) == 2
    assert merged.resolve("[[PII|ADDR|BCDFGHJKMNPR]]") == "b"
    assert "a" not in repr(merged)
    assert not Witness()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"antragsteller": {}},
        {"antragsteller": {"geburtsdatum": None}},
        {"antragsteller": {"geburtsdatum": "   "}},
        {"antragsteller": {"anschrift": {}}},
        {"antragsteller": "eine Zeichenkette statt eines Objekts"},
        {"antragsteller": {"geburtsdatum": ["1959-04-17"]}},
    ],
)
def test_nothing_is_sealed_where_there_is_no_value(
    payload: dict[str, Any], policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    """Presence must survive sealing: absent stays absent, blank stays blank."""
    outcome = seal_payload(payload, policy=policy, registry=registry)
    assert outcome.sealed_count == 0
    assert outcome.payload == payload


def test_sealing_a_sealed_payload_seals_nothing_new(
    policy: IdentityFieldsPolicy,
) -> None:
    once = seal_payload(
        PAYLOAD, policy=policy, registry=PlaceholderRegistry(SeededTokenSource(1))
    )
    twice = seal_payload(
        once.payload, policy=policy, registry=PlaceholderRegistry(SeededTokenSource(2))
    )
    assert twice.sealed_count == 0
    assert twice.payload == once.payload


def test_a_subtree_path_that_carries_a_plain_string_is_still_sealed(
    policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    outcome = seal_payload(
        {"antragsteller": {"anschrift": "Kirchgasse 2, 24103 Beispielstadt"}},
        policy=policy,
        registry=registry,
    )
    assert outcome.sealed_count == 1
    assert parse_placeholder(outcome.payload["antragsteller"]["anschrift"]) is not None


def test_seal_leaf_handles_list_elements_and_missing_paths(
    registry: PlaceholderRegistry,
) -> None:
    payload = {"antrag": {"notizen": ["ein Hinweis", "noch einer"]}}
    entry = seal_leaf(payload, "antrag.notizen[1]", kind=Kind.TEXT, registry=registry)
    assert entry is not None
    assert payload["antrag"]["notizen"][0] == "ein Hinweis"
    assert parse_placeholder(payload["antrag"]["notizen"][1]) is not None
    for missing in ("antrag.gibtsnicht", "gibtsnicht.x", "antrag.notizen[9]", ""):
        assert seal_leaf(payload, missing, kind=Kind.TEXT, registry=registry) is None
    # Already a placeholder: idempotent, nothing minted twice.
    assert (
        seal_leaf(payload, "antrag.notizen[1]", kind=Kind.TEXT, registry=registry)
        is None
    )


def test_path_steps_splits_keys_and_indices() -> None:
    assert path_steps("a.b[0].c") == ["a", "b", 0, "c"]
    assert path_steps("[2]") == [2]


def test_placeholder_tokens_finds_every_sealed_leaf(
    policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    outcome = seal_payload(PAYLOAD, policy=policy, registry=registry)
    assert len(placeholder_tokens(outcome.payload)) == 5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ({}, None),
        ([], None),
        ("  ", None),
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (" text ", "text"),
    ],
)
def test_scalar_text_is_the_one_rendering_rule(
    value: object, expected: str | None
) -> None:
    assert scalar_text(value) == expected


# ------------------------------------------------------------------ verify ---


def test_a_clean_working_copy_verifies(
    policy: IdentityFieldsPolicy, registry: PlaceholderRegistry
) -> None:
    outcome = seal_payload(PAYLOAD, policy=policy, registry=registry)
    report = verify_payload(outcome.payload)
    assert report.clean is True
    assert report.scanned_leaves > 0
    assert str(report).startswith("clean")
    assert report.to_dict()["findings"] == []


def test_a_finding_names_the_path_and_never_the_text() -> None:
    payload = {"antrag": {"letzte_taetigkeit": f"war {CANARY_VSNR} bei der Firma"}}
    report = verify_payload(payload)
    assert report.clean is False
    finding = report.findings[0]
    assert finding.path == "antrag.letzte_taetigkeit"
    assert finding.kind is Kind.VSNR
    assert finding.length == len(CANARY_VSNR)
    assert CANARY_VSNR not in str(finding)
    assert CANARY_VSNR not in json.dumps(report.to_dict())
    assert CANARY_VSNR not in str(report)
    assert report.paths == ("antrag.letzte_taetigkeit",)


def test_free_text_is_verified_through_the_same_sweep() -> None:
    report = verify_texts({"part-0": f"Nummer {CANARY_VSNR}"})
    assert report.clean is False
    assert report.findings[0].path == "part-0"
    assert verify_texts({"part-0": "nichts besonderes"}).clean is True


# ---------------------------------------------------------------- boundary ---


def test_the_boundary_seals_verifies_and_records(
    policy: IdentityFieldsPolicy,
) -> None:
    outcome = redact_payload(
        PAYLOAD,
        policy=policy,
        case_id="case-x",
        created_at=FIXED,
        token_source=SeededTokenSource(5),
    )
    assert outcome.verified is True
    assert outcome.sealed_count == 5
    assert outcome.auto_sealed_paths == ()
    assert outcome.record.case_id == "case-x"
    assert outcome.record.created_at == FIXED
    summary = outcome.summary()
    assert summary["redaction_verified"] is True
    assert summary["vault_ref"] == outcome.vault_ref


def test_residue_at_a_non_sealed_path_is_auto_sealed_and_reverified(
    policy: IdentityFieldsPolicy,
) -> None:
    """A Versicherungsnummer typed into a free-text field: seal MORE, not less."""
    payload = {
        **PAYLOAD,
        "antrag": {"letzte_taetigkeit": f"zuletzt unter {CANARY_VSNR} gefuehrt"},
    }
    outcome = redact_payload(
        payload,
        policy=policy,
        case_id="case-sweep",
        created_at=FIXED,
        token_source=SeededTokenSource(6),
    )
    assert outcome.auto_sealed_paths == ("antrag.letzte_taetigkeit",)
    assert outcome.verified is True
    sealed = outcome.payload["antrag"]["letzte_taetigkeit"]
    placeholder = parse_placeholder(sealed)
    assert placeholder is not None
    # The WHOLE leaf is sealed, and as TEXT: a free-text field that happened to
    # contain a Versicherungsnummer is text, not a Versicherungsnummer.
    assert placeholder.kind is Kind.TEXT
    assert outcome.witness.resolve(sealed) == f"zuletzt unter {CANARY_VSNR} gefuehrt"
    assert CANARY_VSNR not in json.dumps(outcome.payload, ensure_ascii=False)


def test_residue_that_survives_the_sweep_refuses_the_submission(
    policy: IdentityFieldsPolicy,
) -> None:
    """Never forward unverified, and never leave half a case behind."""

    class NeverClean:
        """A sweeper that keeps finding something, whatever we seal."""

        def scan(self, text: str) -> tuple[Any, ...]:
            from engine.redact.recognizers import Detection

            return (Detection(start=0, end=3, kind=Kind.VSNR, recognizer_id="stub"),)

    with pytest.raises(RedactionRefusedError) as raised:
        redact_payload(
            PAYLOAD,
            policy=policy,
            case_id="case-refused",
            created_at=FIXED,
            token_source=SeededTokenSource(7),
            detector=NeverClean(),  # type: ignore[arg-type]
        )
    error = raised.value
    assert error.report.clean is False
    assert "17170459B012" not in f"{error} {error!r}"
    assert error.as_payload()["error"] == "redaction_unverified"
    assert error.as_payload()["findings"][0]["kind"] == "VSNR"


# ------------------------------------------------------------------- vault ---


def _record(ref: str = "vault-TEST") -> VaultRecord:
    return build_record(
        vault_ref=ref,
        case_id="case-x",
        created_at=FIXED,
        entries=[
            SealedEntry(
                kind=Kind.VSNR,
                token="BCDFGHJKMNPQ",
                value_json=json.dumps("17170459B012"),
                path="antragsteller.versicherungsnummer",
            )
        ],
    )


@pytest.fixture(params=["memory", "jsonl"])
def vault(request: pytest.FixtureRequest, tmp_path: Path) -> VaultStore:
    """Every backend runs the same conformance tests (ADR-008's discipline)."""
    if request.param == "memory":
        return InMemoryVaultStore()
    return JsonlVaultStore(tmp_path / "vault")


def test_a_sealed_record_is_readable_at_render_time(vault: VaultStore) -> None:
    record = _record()
    assert vault.seal(record) is record
    assert vault.exists("vault-TEST") is True
    fetched = vault.fetch("vault-TEST")
    assert fetched.case_id == "case-x"
    assert fetched.created_at == FIXED
    assert fetched.tokens == {"BCDFGHJKMNPQ"}
    entry = fetched.entry_for("BCDFGHJKMNPQ")
    assert entry is not None
    assert entry.value() == "17170459B012"
    assert entry.placeholder == "[[PII|VSNR|BCDFGHJKMNPQ]]"
    assert fetched.entry_for("nichts") is None


def test_a_vault_ref_may_be_sealed_only_once(vault: VaultStore) -> None:
    vault.seal(_record())
    with pytest.raises(DuplicateVaultRecordError):
        vault.seal(_record())


def test_an_unknown_ref_is_an_error_not_an_empty_record(vault: VaultStore) -> None:
    assert vault.exists("vault-GIBTSNICHT") is False
    with pytest.raises(UnknownVaultRefError):
        vault.fetch("vault-GIBTSNICHT")


def test_every_backend_satisfies_the_protocol(vault: VaultStore) -> None:
    assert isinstance(vault, VaultStore)


def test_the_record_summary_is_value_free() -> None:
    summary = _record().summary()
    assert summary == {
        "vault_ref": "vault-TEST",
        "entry_count": 1,
        "kinds": {"VSNR": 1},
    }
    assert "17170459B012" not in json.dumps(summary)


def test_the_file_backend_refuses_unsafe_refs(tmp_path: Path) -> None:
    store = JsonlVaultStore(tmp_path / "vault")
    with pytest.raises(ValueError, match="not filesystem-safe"):
        store.seal(_record("../escape"))


def test_the_file_backend_says_it_is_a_dev_backend(tmp_path: Path) -> None:
    """Plaintext by design, and the file it writes says so."""
    store = JsonlVaultStore(tmp_path / "vault")
    store.seal(_record())
    written = (tmp_path / "vault" / "vault-TEST.json").read_text(encoding="utf-8")
    assert DEV_BACKEND_NOTICE in written
    assert "encryption at rest" in written
    assert store.refs() == ["vault-TEST"]


def test_the_memory_backend_lists_its_refs() -> None:
    store = InMemoryVaultStore()
    store.seal(_record("vault-A"))
    store.seal(_record("vault-B"))
    assert store.refs() == ["vault-A", "vault-B"]


def test_a_record_round_trips_through_its_dict_form() -> None:
    record = build_record(
        vault_ref="vault-TEST",
        case_id="case-x",
        created_at=FIXED,
        entries=[
            SealedEntry(
                kind=Kind.ADDR,
                token="BCDFGHJKMNPQ",
                value_json=json.dumps({"plz": "24103"}),
                path="antragsteller.anschrift",
                part_id="part-structured-0",
                span=(3, 9),
            )
        ],
    )
    restored = VaultRecord.from_dict(record.to_dict())
    assert restored == record
    assert restored.entries[0].span == (3, 9)
