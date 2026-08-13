"""Applicant notifications: the fold, the wording, the outbox and the latency.

The load-bearing test of this module is idempotence. Everything else follows
from it: if replaying a journal could send a second receipt, then the journal
would not be safe to replay, and "notifications are projections of the journal"
(ADR-005) would be a description of a hazard rather than of an architecture.

Every rendered text here is produced with a FIXED clock and a frozen corpus
item, so the two golden files are byte-stable. Nothing in this module reads a
wall clock.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from engine.config_loader import (
    ConfigBundle,
    ConfigError,
    NotificationsConfig,
    NotificationTemplate,
    load_config,
)
from engine.journal.store import InMemoryJournalStore, JsonlJournalStore
from engine.notify import (
    InMemoryOutbox,
    JsonlOutbox,
    LatencySample,
    NotificationRenderError,
    OutboxEntry,
    case_latencies,
    latency_section,
    notification_id_for,
    notified_source_event_ids,
    notify_case,
    notify_journal,
    owed_notifications,
    render_text,
)
from engine.notify import replay as replay_cli
from engine.notify.render import build_context, format_timestamp
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from schemas.events import Event, EventType

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = Path(__file__).parent / "golden"

#: The injected clock. Every timestamp in this module is derived from it, so a
#: golden file changes only when a template changes.
FIXED_RECEIVED = datetime(2026, 3, 17, 8, 30, tzinfo=UTC)
FIXED_NOTIFIED = FIXED_RECEIVED + timedelta(seconds=2)

#: One frozen gold item with a rule that fires, so the case gets BOTH messages.
GOLDEN_ITEM = "ar-0001-regelaltersrente-vollstaendig"


# ------------------------------------------------------------- fixtures ---


@pytest.fixture
def payload(gold_v4_dir: Path) -> dict[str, Any]:
    return json.loads((gold_v4_dir / f"{GOLDEN_ITEM}.json").read_text("utf-8"))


@pytest.fixture
def journal(config: ConfigBundle, payload: dict[str, Any]) -> InMemoryJournalStore:
    """A journal holding one complete case, stamped with the fixed clock."""
    store = InMemoryJournalStore()
    run_pipeline(
        payload,
        config=config,
        journal=store,
        vault=InMemoryVaultStore(),
        now=FIXED_RECEIVED,
    )
    return store


@pytest.fixture
def outbox() -> InMemoryOutbox:
    return InMemoryOutbox()


def _run(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    outbox: InMemoryOutbox,
    *,
    now: datetime | None = FIXED_NOTIFIED,
) -> Any:
    case_id = journal.case_ids()[0]
    return notify_case(
        journal.read(case_id), config=config, journal=journal, outbox=outbox, now=now
    )


def _notifications_document() -> dict[str, Any]:
    path = REPO_ROOT / "config" / "notifications" / "notifications_v1.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _with_template(**overrides: str) -> dict[str, Any]:
    """The shipped config with its first template's fields overridden."""
    document = _notifications_document()
    document["templates"][0].update(overrides)
    return document


# --------------------------------------------------------- golden texts ---


@pytest.mark.parametrize(
    ("template_id", "golden"),
    [
        ("eingangsbestaetigung_v1", "notification_eingangsbestaetigung_v1.txt"),
        ("zuordnung_v1", "notification_zuordnung_v1.txt"),
    ],
)
def test_the_shipped_german_wording_is_byte_stable(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    outbox: InMemoryOutbox,
    template_id: str,
    golden: str,
) -> None:
    """The two texts a citizen actually reads, frozen in a file.

    A golden test on wording is unusual and deliberate: this is the only artifact
    of the whole system a member of the public sees, and a change to it should
    have to be looked at by a human rather than noticed in production.
    """
    _run(config, journal, outbox)
    entry = next(
        item
        for item in outbox.entries(journal.case_ids()[0])
        if item.template_id == template_id
    )
    expected = (GOLDEN_DIR / golden).read_text(encoding="utf-8").rstrip("\n")
    assert entry.body == expected


def test_neither_text_states_a_deadline_a_duty_or_a_request(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """ADR-005 in the rendered output, not only in the template source.

    The loader checks the templates; this checks what came out of them, because
    a context value could in principle carry one of these words in.
    """
    _run(config, journal, outbox)
    for entry in outbox.entries(journal.case_ids()[0]):
        lowered = f"{entry.subject}\n{entry.body}".lower()
        for word in ("mitwirkungspflicht", "frist", "rechtsfolge", "par. 66"):
            assert word not in lowered, f"{entry.template_id} says {word!r}"


def test_both_texts_say_they_are_template_written_and_not_model_written(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """C-13: zero model-generated text on this path, and the text says so."""
    _run(config, journal, outbox)
    for entry in outbox.entries(journal.case_ids()[0]):
        assert "kein Sprachmodell verwendet" in entry.body


def test_the_receipt_carries_the_art_13_14_notice_block(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """C-5's 07 slice: controller, purpose, rights and the DPO contact."""
    _run(config, journal, outbox)
    receipt = next(
        entry
        for entry in outbox.entries(journal.case_ids()[0])
        if entry.template_id == "eingangsbestaetigung_v1"
    )
    for required in (
        "Art. 13 und 14 DSGVO",
        "Verantwortlich:",
        "Datenschutzbeauftragte Person:",
        "Zweck der Verarbeitung:",
        "Ihre Rechte:",
        "Aufsichtsbehoerde:",
    ):
        assert required in receipt.body
    # Every controller-specific item is an unmistakable agency placeholder, so
    # an unfilled one is visible rather than silently missing.
    assert receipt.body.count("[Platzhalter Behoerde:") >= 5


# ------------------------------------------------------------ idempotence ---


def test_running_the_worker_twice_emits_nothing_new(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """Ruling 1, stated as the smallest possible fact."""
    first = _run(config, journal, outbox)
    assert first.count == 2
    before = len(journal.read(journal.case_ids()[0]))

    second = _run(config, journal, outbox, now=None)
    assert second.count == 0
    assert len(journal.read(journal.case_ids()[0])) == before
    assert len(outbox.entries(journal.case_ids()[0])) == 2


def test_replaying_a_whole_journal_is_a_no_op(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    notify_journal(config=config, journal=journal, outbox=outbox, now=FIXED_NOTIFIED)
    counts = [
        sum(
            outcome.count
            for outcome in notify_journal(
                config=config, journal=journal, outbox=outbox, now=None
            )
        )
        for _ in range(3)
    ]
    assert counts == [0, 0, 0]


@given(runs=st.integers(min_value=1, max_value=6))
def test_any_number_of_runs_sends_exactly_two_messages(
    config: ConfigBundle, payload: dict[str, Any], runs: int
) -> None:
    """However often the worker runs, the applicant gets each message once."""
    journal = InMemoryJournalStore()
    run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),
        now=FIXED_RECEIVED,
    )
    outbox = InMemoryOutbox()
    for _ in range(runs):
        notify_journal(config=config, journal=journal, outbox=outbox, now=None)
    case_id = journal.case_ids()[0]
    assert len(outbox.entries(case_id)) == 2
    assert (
        sum(1 for event in journal.read(case_id) if event.type is EventType.NOTIFIED)
        == 2
    )


@given(seed=st.integers(min_value=0, max_value=2**16))
def test_what_a_case_owes_does_not_depend_on_event_order(
    config: ConfigBundle, payload: dict[str, Any], seed: int
) -> None:
    """The fold reads ``sequence``, never the order a store handed events back.

    A JSONL file read after a crash, a database that returns rows unordered, a
    merge of two partial reads: all of them are the same event SET, and a fold
    that owed different notifications for a shuffled list would make replay a
    gamble.
    """
    journal = InMemoryJournalStore()
    run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),
        now=FIXED_RECEIVED,
    )
    events = journal.read(journal.case_ids()[0])
    shuffled = list(events)
    # A deterministic permutation from the drawn seed; Hypothesis shrinks the
    # seed rather than the list, which keeps failures readable.
    for index in range(len(shuffled) - 1, 0, -1):
        swap = (seed * (index + 1)) % (index + 1)
        shuffled[index], shuffled[swap] = shuffled[swap], shuffled[index]

    straight = owed_notifications(events, config=config)
    scrambled = owed_notifications(shuffled, config=config)
    assert [item.template_id for item in straight] == [
        item.template_id for item in scrambled
    ]
    assert [item.source_event_id for item in straight] == [
        item.source_event_id for item in scrambled
    ]


def test_an_outbox_entry_that_survived_a_crash_is_journaled_not_resent(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """Deliver-first, journal-second: the crash window is recoverable.

    Simulated by delivering by hand and then running the worker: the outbox
    refuses the duplicate, the journal gets the event it was missing, and the
    applicant receives one copy.
    """
    case_id = journal.case_ids()[0]
    received = next(
        event for event in journal.read(case_id) if event.type is EventType.RECEIVED
    )
    planted = OutboxEntry(
        notification_id=notification_id_for(
            received.event_id, "eingangsbestaetigung_v1"
        ),
        case_id=case_id,
        channel="fit_connect",
        delivery="status_event",
        template_id="eingangsbestaetigung_v1",
        source_event_id=received.event_id,
        source_event_type="received",
        subject="already delivered",
        body="already delivered",
        created_at=FIXED_RECEIVED,
    )
    assert outbox.deliver(planted) is True

    outcome = _run(config, journal, outbox)
    assert outcome.count == 2  # both journaled
    assert outcome.skipped == 1  # one was already in the outbox
    assert len(outbox.entries(case_id)) == 2
    assert outbox.entries(case_id)[0].body == "already delivered"


def test_the_notification_id_is_a_function_of_its_inputs() -> None:
    assert notification_id_for("evt-1", "tpl") == notification_id_for("evt-1", "tpl")
    assert notification_id_for("evt-1", "tpl") != notification_id_for("evt-2", "tpl")


# ------------------------------------------------- the NOTIFIED contract ---


def test_every_written_event_is_a_notified_with_its_invariants(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """The contract validator in schemas/events.py, exercised through the worker.

    Not a hand-built Event: the point is that the REAL path produces events the
    contract accepts, because the flag is what the Realakt/Verwaltungsakt line
    rests on.
    """
    outcome = _run(config, journal, outbox)
    assert [event.type for event in outcome.events] == [EventType.NOTIFIED] * 2
    for event in outcome.events:
        assert event.informational_only is True
        assert event.template_id in {"eingangsbestaetigung_v1", "zuordnung_v1"}
        assert event.payload["notifications_version"] == "notifications_v1"
        assert event.payload["source_event_type"] in {"received", "routed"}


def test_the_worker_writes_no_other_event_type(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    case_id = journal.case_ids()[0]
    before = [event.type for event in journal.read(case_id)]
    _run(config, journal, outbox)
    added = [event.type for event in journal.read(case_id)][len(before) :]
    assert set(added) == {EventType.NOTIFIED}


def test_the_journal_payload_carries_no_message_text(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """The applicant's copy lives in the outbox; the journal records a dispatch."""
    outcome = _run(config, journal, outbox)
    for event in outcome.events:
        serialized = json.dumps(event.payload, ensure_ascii=False)
        assert "Eingangsbestaetigung" not in serialized
        assert "Zwischenstand" not in serialized
        assert isinstance(event.payload["body_chars"], int)


def test_a_notified_without_a_source_id_degrades_rather_than_raises(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """A malformed payload may not take the projection down (the part-01 rule)."""
    outcome = _run(config, journal, outbox)
    damaged = [
        event.model_copy(update={"payload": {}})
        if event.type is EventType.NOTIFIED
        else event
        for event in journal.read(journal.case_ids()[0])
    ]
    assert notified_source_event_ids(damaged) == frozenset()
    assert len(owed_notifications(damaged, config=config)) == 2
    assert outcome.count == 2


# ------------------------------------------------- PII-free by construction ---


def test_no_placeholder_token_reaches_a_rendered_notification(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """Ruling 2's permanent assertion, using the part-04 definition."""
    from engine.redact.placeholders import PLACEHOLDER_SHAPED_RE, find_placeholders

    _run(config, journal, outbox)
    for entry in outbox.entries(journal.case_ids()[0]):
        blob = f"{entry.subject}\n{entry.body}"
        assert find_placeholders(blob) == ()
        assert PLACEHOLDER_SHAPED_RE.search(blob) is None


def test_the_renderer_refuses_output_that_carries_a_placeholder() -> None:
    """The belt to the suspenders: it has to be able to fail.

    Part 08 adds a re-hydrator next door. This is what makes "notifications are
    not re-hydrated" still true afterwards.
    """
    with pytest.raises(NotificationRenderError, match="redaction placeholder"):
        render_text(
            "Ihre Nummer: {{ value }}",
            {"value": "[[PII|VSNR|QRSTVWXZ2345]]"},
            label="test",
        )
    # ... and placeholder-SHAPED residue is refused too, not only valid tokens.
    with pytest.raises(NotificationRenderError, match="redaction placeholder"):
        render_text("{{ value }}", {"value": "[[PII|VSNR|broken]]"}, label="test")


def test_a_template_naming_something_unknown_fails_loudly() -> None:
    with pytest.raises(NotificationRenderError, match="unknown name"):
        render_text("{{ gibtsnicht }}", {}, label="test")


def test_an_unknown_procedure_renders_no_name_rather_than_the_submissions_text(
    config: ConfigBundle,
) -> None:
    """The rule that keeps applicant-controlled text out of applicant-facing text.

    ``procedureHint`` is whatever the sender typed. It is never echoed: a
    procedure id without an entry in config renders as nothing at all.
    """
    context = build_context(
        case_id="case-x",
        config=config,
        procedure_id="Frau Muster, VSNR 65170839J003",
        channel_id="nonesuch",
        unit_id="nonesuch",
    )
    assert context["procedure_name"] == ""
    assert context["channel_name"] == "unbekannter Eingangsweg"
    assert context["unit_name"] == "noch nicht zugeordnet"


def test_a_known_procedure_channel_and_unit_render_their_public_names(
    config: ConfigBundle,
) -> None:
    context = build_context(
        case_id="case-x",
        config=config,
        procedure_id="altersrente",
        channel_id="email",
        unit_id="Referat_312_Renten",
    )
    assert context["procedure_name"] == "Antrag auf Altersrente"
    assert context["channel_name"] == "E-Mail"
    assert context["unit_name"] == "Referat 312 - Altersrenten"


def test_a_missing_timestamp_renders_as_nothing() -> None:
    assert format_timestamp(None) == ""
    assert format_timestamp(FIXED_RECEIVED) == "17.03.2026, 08:30 Uhr (UTC)"


# ------------------------------------------------------ the config loader ---


@pytest.mark.parametrize(
    ("field", "text", "expected"),
    [
        ("body", "Bitte beachten Sie Ihre Mitwirkungspflicht.", "mitwirkungspflicht"),
        ("body", "Die Frist betraegt einen Monat.", "frist"),
        ("subject", "Rechtsfolge Ihrer Angaben", "rechtsfolge"),
        ("body", "Bitte bis 30.09.2026 einreichen.", "date-shaped"),
        ("body", "Bitte bis 2026-09-30 einreichen.", "date-shaped"),
        ("body", "Unterlagen sind vorzulegen.", "vorzulegen"),
    ],
)
def test_the_loader_refuses_a_template_that_reads_like_a_nachforderung(
    field: str, text: str, expected: str
) -> None:
    """The par. 66 SGB I tripwire (ruling 3).

    Cheap and honest about it: it cannot decide whether a sentence creates a
    legal consequence. It catches the edit that pastes a request sentence into a
    receipt, which is the failure this path has to be protected from.
    """
    with pytest.raises(ValidationError, match="Nachforderung"):
        NotificationsConfig.model_validate(_with_template(**{field: text}))


def test_the_shipped_templates_pass_their_own_tripwire(config: ConfigBundle) -> None:
    assert config.notifications is not None
    assert config.notifications.version == "notifications_v1"
    assert {template.template_id for template in config.notifications.templates} == {
        "eingangsbestaetigung_v1",
        "zuordnung_v1",
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("duplicate_id", "duplicate template_id"),
        ("duplicate_trigger", "same trigger"),
        ("unknown_trigger", "known triggers"),
        ("duplicate_channel", "duplicate channel"),
        ("missing_channel", "no notification channel mapping"),
        ("broken_jinja", "not valid Jinja2"),
        ("unknown_delivery", "delivery"),
    ],
)
def test_the_loader_refuses_a_broken_notifications_file(
    mutation: str, match: str
) -> None:
    document = _notifications_document()
    if mutation == "duplicate_id":
        document["templates"][1]["template_id"] = document["templates"][0][
            "template_id"
        ]
    elif mutation == "duplicate_trigger":
        document["templates"][1]["trigger"] = "received"
    elif mutation == "unknown_trigger":
        document["templates"][0]["trigger"] = "tier_decided"
    elif mutation == "duplicate_channel":
        document["channels"][1]["channel"] = "fit_connect"
    elif mutation == "missing_channel":
        document["channels"] = document["channels"][:2]
    elif mutation == "broken_jinja":
        document["templates"][0]["body"] = "Guten Tag {{ case_id"
    else:
        document["channels"][0]["delivery"] = "carrier_pigeon"
    with pytest.raises(ValidationError, match=match):
        NotificationsConfig.model_validate(document)


def test_the_loader_refuses_a_display_name_for_a_procedure_that_does_not_exist(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", destination)
    path = destination / "notifications" / "notifications_v1.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["procedure_names"]["bauantrag"] = "Bauantrag"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown procedure"):
        load_config(destination)


def test_an_agency_without_a_notifications_directory_sends_nothing(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    """Absent is a choice with a defined meaning, not a degraded state."""
    destination = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", destination)
    shutil.rmtree(destination / "notifications")
    silent = load_config(destination)
    assert silent.notifications is None

    store = InMemoryJournalStore()
    run_pipeline(
        payload,
        config=silent,
        journal=store,
        vault=InMemoryVaultStore(),
        now=FIXED_RECEIVED,
    )
    events = store.read(store.case_ids()[0])
    assert owed_notifications(events, config=silent) == ()
    outcome = notify_case(
        events,
        config=silent,
        journal=store,
        outbox=InMemoryOutbox(),
        now=FIXED_NOTIFIED,
    )
    assert outcome.count == 0


def test_a_template_lookup_for_an_unknown_id_or_trigger_is_none(
    config: ConfigBundle,
) -> None:
    assert config.notifications is not None
    assert config.notifications.template("gibtsnicht") is None
    assert config.notifications.template_for("tier_decided") is None
    assert config.notifications.channel(None) is None
    assert config.notifications.channel("brieftaube") is None


def test_the_channel_mapping_records_the_formality_of_each_channel(
    config: ConfigBundle,
) -> None:
    """C-8: every inbound channel is answered informally, and says which way."""
    assert config.notifications is not None
    mapping = {entry.channel: entry.delivery for entry in config.notifications.channels}
    assert mapping == {
        "fit_connect": "status_event",
        "email": "mail",
        "scan": "postal_stub",
    }


def test_a_template_may_not_be_triggered_by_an_event_that_owes_nothing() -> None:
    with pytest.raises(ValidationError, match="known triggers"):
        NotificationTemplate.model_validate(
            {
                "template_id": "x",
                "trigger": "confirmed",
                "subject": "s",
                "body": "b",
            }
        )


# ------------------------------------------------------------ the outbox ---


def test_both_outbox_backends_behave_the_same(tmp_path: Path) -> None:
    entry = OutboxEntry(
        notification_id="n-1",
        case_id="case-1",
        channel="email",
        delivery="mail",
        template_id="t",
        source_event_id="e-1",
        source_event_type="received",
        subject="s",
        body="b",
        created_at=FIXED_RECEIVED,
    )
    for store in (InMemoryOutbox(), JsonlOutbox(tmp_path / "outbox")):
        assert store.deliver(entry) is True
        assert store.deliver(entry) is False, "a second delivery is a no-op"
        assert [item.notification_id for item in store.entries("case-1")] == ["n-1"]
        assert store.entries("case-unknown") == []
        assert store.case_ids() == ["case-1"]


def test_the_jsonl_outbox_refuses_an_unsafe_case_id(tmp_path: Path) -> None:
    store = JsonlOutbox(tmp_path / "outbox")
    with pytest.raises(ValueError, match="filesystem-safe"):
        store.entries("../../etc/passwd")


def test_the_default_outbox_follows_its_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine.notify.outbox import OUTBOX_DIR_ENV, default_outbox

    monkeypatch.delenv(OUTBOX_DIR_ENV, raising=False)
    assert isinstance(default_outbox(), InMemoryOutbox)
    monkeypatch.setenv(OUTBOX_DIR_ENV, str(tmp_path / "outbox"))
    assert isinstance(default_outbox(), JsonlOutbox)


# ----------------------------------------------------------- the latency ---


def test_latency_is_the_delta_between_two_journal_timestamps(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    _run(config, journal, outbox)
    samples = case_latencies(journal.read(journal.case_ids()[0]))
    assert [sample.trigger for sample in samples] == ["received", "routed"]
    assert [sample.milliseconds for sample in samples] == [2000.0, 2000.0]


def test_a_notification_without_its_source_event_is_not_timed(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    _run(config, journal, outbox)
    events = journal.read(journal.case_ids()[0])
    orphans = [event for event in events if event.type is EventType.NOTIFIED]
    assert case_latencies(orphans) == ()


def test_the_latency_section_is_nearest_rank_and_per_trigger() -> None:
    samples = [
        LatencySample(f"case-{index}", "received", "t", float(index))
        for index in range(1, 101)
    ] + [LatencySample("case-1", "routed", "u", 7.0)]
    section = latency_section(samples)
    assert section["notification_count"] == 101
    assert section["by_template"] == {"t": 100, "u": 1}
    received = section["by_trigger"]["received"]
    assert received == {
        "count": 100,
        "min_ms": 1.0,
        "median_ms": 50.0,
        "p95_ms": 95.0,
        "max_ms": 100.0,
    }
    assert section["by_trigger"]["routed"]["count"] == 1


def test_an_empty_latency_section_reports_nothing_rather_than_zero() -> None:
    """A distribution over no samples has no minimum, and 0.0 would be a lie."""
    section = latency_section([])
    assert section["notification_count"] == 0
    assert section["overall"] == {
        "count": 0,
        "min_ms": None,
        "median_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }


# --------------------------------------------------------- the replay CLI ---


def test_the_replay_cli_sends_once_and_then_nothing(
    tmp_path: Path, payload: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    journal_dir = tmp_path / "journal"
    outbox_dir = tmp_path / "outbox"
    store = JsonlJournalStore(journal_dir)
    run_pipeline(
        payload,
        config=load_config(),
        journal=store,
        vault=InMemoryVaultStore(),
        now=FIXED_RECEIVED,
    )
    argv = ["--journal", str(journal_dir), "--outbox", str(outbox_dir)]

    assert replay_cli.main([*argv, "--dry-run"]) == 0
    assert "would send" in capsys.readouterr().out
    assert JsonlOutbox(outbox_dir).case_ids() == [], "a dry run writes nothing"

    assert replay_cli.main(argv) == 0
    assert "sent" in capsys.readouterr().out
    assert len(JsonlOutbox(outbox_dir).entries(store.case_ids()[0])) == 2

    assert replay_cli.main(argv) == 0
    assert "Nothing owed" in capsys.readouterr().out
    assert len(JsonlOutbox(outbox_dir).entries(store.case_ids()[0])) == 2


def test_the_replay_cli_refuses_a_config_without_notifications(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", destination)
    shutil.rmtree(destination / "notifications")
    exit_code = replay_cli.main(
        ["--journal", str(tmp_path / "journal"), "--config", str(destination)]
    )
    assert exit_code == 1
    assert "sends no applicant notifications" in capsys.readouterr().err


# ------------------------------------------------------------ the fold ---


def test_a_case_that_was_never_routed_gets_the_receipt_only(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """Five items in gold v4 route nowhere; the applicant still gets a receipt.

    That asymmetry is the honest one: "we have your request" is always true,
    "here is who handles it" is not yet.
    """
    unrouted = json.loads(
        (gold_v4_dir / "xx-0001-grundsicherung-anfrage.json").read_text("utf-8")
    )
    store = InMemoryJournalStore()
    result = run_pipeline(
        unrouted,
        config=config,
        journal=store,
        vault=InMemoryVaultStore(),
        now=FIXED_RECEIVED,
    )
    assert result.decision.routed_unit_id is None
    outbox = InMemoryOutbox()
    outcome = notify_case(
        store.read(store.case_ids()[0]),
        config=config,
        journal=store,
        outbox=outbox,
        now=FIXED_NOTIFIED,
    )
    assert [event.template_id for event in outcome.events] == [
        "eingangsbestaetigung_v1"
    ]


def test_an_empty_event_list_owes_nothing(config: ConfigBundle) -> None:
    outcome = notify_case(
        [],
        config=config,
        journal=InMemoryJournalStore(),
        outbox=InMemoryOutbox(),
        now=FIXED_NOTIFIED,
    )
    assert outcome.count == 0
    assert outcome.case_id == ""


def test_the_status_update_names_the_unit_the_routed_event_names(
    config: ConfigBundle, journal: InMemoryJournalStore, outbox: InMemoryOutbox
) -> None:
    """The name comes from the taxonomy, keyed by the id in the ROUTED payload."""
    _run(config, journal, outbox)
    status = next(
        entry
        for entry in outbox.entries(journal.case_ids()[0])
        if entry.template_id == "zuordnung_v1"
    )
    routed = next(
        event
        for event in journal.read(journal.case_ids()[0])
        if event.type is EventType.ROUTED
    )
    unit = config.unit(str(routed.payload["unit_id"]))
    assert unit is not None
    assert unit.name in status.body


def _event(event_type: EventType, sequence: int, versions: Any) -> Event:
    from engine.journal.store import make_event

    return make_event(
        case_id="case-synthetic",
        event_type=event_type,
        sequence=sequence,
        versions=versions,
        occurred_at=FIXED_RECEIVED,
        payload={"channel": "email"} if event_type is EventType.RECEIVED else {},
    )


def test_an_event_type_that_owes_nothing_produces_nothing(
    config: ConfigBundle, versions: Any
) -> None:
    events = [
        _event(EventType.REDACTED, 0, versions),
        _event(EventType.EXTRACTED, 1, versions),
        _event(EventType.TIER_DECIDED, 2, versions),
    ]
    assert owed_notifications(events, config=config) == ()


def test_a_status_update_without_a_receipt_in_the_list_still_renders(
    config: ConfigBundle, versions: Any
) -> None:
    """A truncated read must not crash the worker, only lose a timestamp.

    ``routed`` without ``received`` cannot happen in a healthy journal, but a
    partial file read can produce it, and the receipt's timestamp is the only
    thing that goes missing - not the message.
    """
    from engine.journal.store import make_event

    routed = make_event(
        case_id="case-truncated",
        event_type=EventType.ROUTED,
        sequence=0,
        versions=versions,
        occurred_at=FIXED_RECEIVED,
        payload={"unit_id": "Referat_312_Renten"},
    )
    store = InMemoryJournalStore()
    store.append(routed)
    outbox = InMemoryOutbox()
    outcome = notify_case(
        [routed], config=config, journal=store, outbox=outbox, now=FIXED_NOTIFIED
    )
    assert [event.template_id for event in outcome.events] == ["zuordnung_v1"]
    assert "Referat 312 - Altersrenten" in outbox.entries("case-truncated")[0].body


def test_a_dropped_optional_line_leaves_no_blank_gap() -> None:
    """The receipt's ``Anliegen:`` line disappears cleanly when unknown."""
    rendered = render_text(
        "A\n{% if name %}\nB: {{ name }}\n{% endif %}\n\nC",
        {"name": ""},
        label="test",
    )
    assert rendered == "A\n\nC"
