"""The extractor switch (part 12): replay by default, live only if asked.

Four properties, and the first one is the one that matters most:

* **Flag-off identity.** With the environment untouched, the app runs exactly
  what it ran before this part existed - no extractor object, no endpoint, no
  probe, and an ``/ingest`` whose result is byte-comparable to a direct
  ``run_pipeline`` call. The gate suite therefore never depends on a model, and
  a machine with no Ollama on it produces the shipped numbers.
* **The switch resolves once, and an unrecognized value is an error.** A typo
  in a posture variable that silently selected the safe default would be a
  service running a posture nobody chose.
* **Live-mode runtime failure degrades, it does not error.** An endpoint that
  is not there costs proposals, which cost discards, which push toward tier 3
  and land in the journal's failure histogram. The caller gets a 201.
* **The seal holds at the process boundary.** A canary-bearing item run in live
  mode puts nothing in the request body but the redacted working copy. This is
  the assertion that the topology of ADR-019 survives contact with a socket:
  the sweep in ``tests/test_redact_canaries.py`` covers what the system STORES
  and RENDERS, and this covers what it SENDS.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from engine.config_loader import ConfigBundle, LivePolicy
from engine.extract import (
    EXTRACTOR_ENV,
    EXTRACTOR_MODEL_ENV,
    EXTRACTOR_URL_ENV,
    LIVE,
    REPLAY,
    ExtractorPosture,
    ExtractorSelectionError,
    LiveExtractor,
    build_extractor,
)
from engine.journal import InMemoryJournalStore
from engine.notify import InMemoryOutbox
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from schemas.events import EventType
from tests.test_redact_canaries import CANARIES, canary_submission

URL = "http://localhost:11434"
MODEL = "mistral:7b-instruct-v0.3-q4_K_M"
LIVE_ENV = {EXTRACTOR_ENV: LIVE, EXTRACTOR_URL_ENV: URL, EXTRACTOR_MODEL_ENV: MODEL}


def recording_transport(*documents: Any):  # type: ignore[no-untyped-def]
    """A transport that records every request body and answers from a script."""
    calls: list[tuple[str, dict[str, Any] | None, float]] = []

    def transport(
        url: str, body: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        calls.append((url, body, timeout))
        document = documents[min(len(calls) - 1, len(documents) - 1)]
        if isinstance(document, Exception):
            raise document
        return document  # type: ignore[no-any-return]

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def with_live_enabled(config: ConfigBundle) -> ConfigBundle:
    """The shipped config with an agency's ``live`` block filled in."""
    return replace(
        config,
        extraction=config.extraction.model_copy(
            update={"live": LivePolicy(enabled=True, base_url=URL, model=MODEL)}
        ),
    )


def live_extractor_with(config: ConfigBundle, transport: Any) -> LiveExtractor:
    """The extractor the switch would build, with a scripted transport in it."""
    built = build_extractor(config, environ=LIVE_ENV)
    assert built is not None
    return LiveExtractor(
        built.settings,
        system_prompt=config.extraction.prompt.system,
        user_prompt=config.extraction.prompt.user,
        transport=transport,
    )


# ------------------------------------------------------------- resolution ---


def test_the_default_is_replay_and_the_shipped_config_agrees(
    config: ConfigBundle,
) -> None:
    """No environment, no model. This is the state every gate runs in."""
    assert config.extraction.live.enabled is False
    posture = ExtractorPosture.from_env(config, {})
    assert posture.mode == REPLAY
    assert posture.live is False
    assert build_extractor(config, environ={}) is None
    assert "replay" in posture.describe()


def test_replay_can_be_selected_explicitly_and_beats_an_enabled_config(
    config: ConfigBundle,
) -> None:
    """The kill switch: an operator can turn a configured model off by hand."""
    enabled = with_live_enabled(config)
    assert ExtractorPosture.from_env(enabled, {}).mode == LIVE
    assert ExtractorPosture.from_env(enabled, {EXTRACTOR_ENV: REPLAY}).mode == REPLAY
    assert build_extractor(enabled, environ={EXTRACTOR_ENV: REPLAY}) is None


def test_an_enabled_config_selects_live_without_any_environment(
    config: ConfigBundle,
) -> None:
    """``live.enabled: true`` has documented this since part 05; now it works."""
    extractor = build_extractor(with_live_enabled(config), environ={})
    assert extractor is not None
    assert extractor.extractor_id == f"llm:{MODEL}"


def test_the_environment_overrides_the_config_without_editing_a_frozen_file(
    config: ConfigBundle,
) -> None:
    extractor = build_extractor(config, environ=LIVE_ENV)
    assert extractor is not None
    assert extractor.settings.base_url == URL
    assert extractor.settings.model == MODEL
    assert extractor.extractor_id == f"llm:{MODEL}"
    # Policy stays policy: patience and chunking come from the config file.
    assert extractor.settings.timeout == config.extraction.live.timeout_seconds
    assert extractor.settings.attempts == config.extraction.live.attempts
    assert extractor.settings.chunk_chars == config.extraction.live.chunk_chars
    assert MODEL in ExtractorPosture.from_env(config, LIVE_ENV).describe()


@pytest.mark.parametrize("value", ["Live", "LIVE", "ollama", "1", "true", "replay "])
def test_an_unrecognized_posture_is_a_startup_error_not_a_silent_default(
    config: ConfigBundle, value: str
) -> None:
    """``replay `` with a trailing space is stripped and legal; the rest are not."""
    if value.strip() == REPLAY:
        assert ExtractorPosture.from_env(config, {EXTRACTOR_ENV: value}).mode == REPLAY
        return
    with pytest.raises(ExtractorSelectionError, match="not a known extractor"):
        ExtractorPosture.from_env(config, {EXTRACTOR_ENV: value})


def test_live_without_an_endpoint_refuses_to_start(config: ConfigBundle) -> None:
    """Better than a service that pushes every item to tier 3 while looking fine."""
    with pytest.raises(ExtractorSelectionError, match="no endpoint is configured"):
        build_extractor(config, environ={EXTRACTOR_ENV: LIVE})
    with pytest.raises(ExtractorSelectionError, match="no endpoint is configured"):
        build_extractor(config, environ={EXTRACTOR_ENV: LIVE, EXTRACTOR_URL_ENV: URL})


def test_building_a_live_extractor_never_touches_the_network(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No probe at startup: a service configured for live mode still boots with
    the model off, which is the whole reason a runtime failure can be a discard."""

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("startup opened a socket")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    assert build_extractor(config, environ=LIVE_ENV) is not None


# --------------------------------------------------------- flag-off identity ---


def test_with_the_switch_absent_the_app_carries_no_extractor(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(EXTRACTOR_ENV, raising=False)
    app = create_app(
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        outbox=InMemoryOutbox(),
    )
    assert app.state.extractor is None


def test_flag_off_ingest_is_identical_to_a_direct_deterministic_run(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing observable changed: the switch's default IS the previous code."""
    monkeypatch.delenv(EXTRACTOR_ENV, raising=False)
    submission = canary_submission("switch-identity-0001")

    app = create_app(
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        outbox=InMemoryOutbox(),
    )
    with TestClient(app) as client:
        through_app = client.post("/ingest", json=submission)
    assert through_app.status_code == 201

    direct = run_pipeline(
        submission,
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
    )
    body = through_app.json()
    assert body["tier"] == int(direct.decision.tier)
    assert body["pre_downgrade_tier"] == int(direct.decision.pre_downgrade_tier)
    assert body["procedure_id"] == direct.procedure_id
    assert body["routed_unit_id"] == direct.decision.routed_unit_id
    assert body["clear_cut"] == direct.clear_cut
    assert body["completeness_verdict"] == (direct.evidence.completeness.verdict.value)
    assert [reason["rule_id"] for reason in body["reasons"]] == [
        reason.rule_id for reason in direct.decision.reasons
    ]
    # The provenance the whole switch turns on: no model ran, so the stamp says
    # the deterministic extractor and nothing else.
    assert direct.extractions.versions.model_id == config.extraction.replay.extractor_id


# ----------------------------------------------- live mode, and its failures ---


def test_live_mode_stamps_the_model_into_the_provenance(
    config: ConfigBundle,
) -> None:
    """A report over live evidence may never say ``replay:v4``."""
    extractor = live_extractor_with(config, recording_transport({"choices": []}))
    result = run_pipeline(
        canary_submission("switch-provenance-0001"),
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        live_extractor=extractor,
    )
    assert result.extractions.versions.model_id == f"llm:{MODEL}"


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
        urllib.error.HTTPError("u", 503, "model loading", {}, None),  # type: ignore[arg-type]
    ],
)
def test_a_dead_endpoint_degrades_and_journals_instead_of_erroring(
    config: ConfigBundle, failure: Exception
) -> None:
    """Part-05 discipline, reached through the switch: unverifiable is discarded."""
    journal = InMemoryJournalStore()
    app = create_app(
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),
        outbox=InMemoryOutbox(),
        live_extractor=live_extractor_with(config, recording_transport(failure)),
    )
    with TestClient(app) as client:
        response = client.post("/ingest", json=canary_submission("switch-dead-0001"))
    assert response.status_code == 201, "a missing model is never a caller's error"
    case_id = response.json()["case_id"]

    extracted = [
        event for event in journal.read(case_id) if event.type == EventType.EXTRACTED
    ]
    assert len(extracted) == 1
    payload: dict[str, Any] = dict(extracted[0].payload)
    stats: dict[str, Any] = dict(payload["verification"])
    # A model that never answered proposed nothing, so it verified nothing and
    # its provenance appears on no record. The loss is journaled, not hidden.
    assert stats["verified"] == 0
    assert f"llm:{MODEL}" not in payload["extractor_ids"]


def test_a_lying_model_still_loses_to_the_double_lock(config: ConfigBundle) -> None:
    """The switch cannot buy a value past the verifier; that is the point."""
    answer = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "angaben": [
                                {
                                    "field": "rentenart",
                                    "value": "regelaltersrente",
                                    "quote": "Rentenart: regelaltersrente",
                                    "offset": 3,
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }
    journal = InMemoryJournalStore()
    result = run_pipeline(
        canary_submission("switch-liar-0001"),
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),
        live_extractor=live_extractor_with(config, recording_transport(answer)),
    )
    accepted = [
        record
        for record in result.extractions.records
        if record.extractor_id.startswith("llm:")
    ]
    assert accepted == [], "an invented offset is a discard, never a repair"


# ---------------------------------------------------- the seal, on the wire ---


def test_a_canary_item_leaks_nothing_into_the_request_body(
    config: ConfigBundle,
) -> None:
    """What LEAVES the process is the redacted working copy and nothing else.

    Captured against the scripted transport, because the property is about the
    request body this client builds, not about which socket carries it. Every
    canary is swept out of the whole serialized body - prompt, system message,
    model name, schema - so a leak anywhere in the envelope fails this test.
    """
    transport = recording_transport({"choices": []})
    run_pipeline(
        canary_submission("switch-seal-0001"),
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        live_extractor=live_extractor_with(config, transport),
    )
    calls = transport.calls  # type: ignore[attr-defined]
    assert calls, "the model was never asked anything, so nothing was proven"
    for _, body, _ in calls:
        serialized = json.dumps(body, ensure_ascii=False)
        for canary in CANARIES:
            assert canary not in serialized, f"{canary} left the process boundary"
        # Positive control: the redacted text really was sent, so the sweep
        # above is not passing because the body was empty.
        assert "[[PII|" in serialized
