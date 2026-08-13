"""The optional LLM paraphrase client, fully mocked.

No test in this suite may touch a socket: every transport here is a fake. What
is being tested is the contract with an OpenAI-compatible endpoint (request
shape, timeout, answer handling) and, more importantly, that every failure mode
degrades to the deterministic pass instead of raising or, worse, writing a
model's hallucination into a payload field.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from corpus.generator.llm import (
    BASE_URL_ENV,
    MAX_NOTE_CHARS,
    MODEL_ENV,
    LlmClient,
    LlmSettings,
    clean_note,
    settings_from_env,
)
from corpus.generator.paraphrase import DeterministicParaphraser, LlmParaphraser
from corpus.generator.render import item_rng, mapped_values, render_payload
from tests.test_corpus_generator import _spec

SETTINGS = LlmSettings(base_url="http://localhost:11434", model="testmodel")


class RecordingTransport:
    """A fake transport that records calls and replays canned answers."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, Any] | None, float]] = []

    def __call__(
        self, url: str, body: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        self.calls.append((url, body, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _chat(content: object) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def test_settings_come_from_flags_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    monkeypatch.delenv(MODEL_ENV, raising=False)
    assert settings_from_env(None, None) is None

    monkeypatch.setenv(BASE_URL_ENV, "http://env:11434")
    monkeypatch.setenv(MODEL_ENV, "envmodel")
    from_env = settings_from_env(None, None)
    assert from_env is not None
    assert (from_env.base_url, from_env.model) == ("http://env:11434", "envmodel")

    explicit = settings_from_env("http://flag:11434", "flagmodel")
    assert explicit is not None
    assert explicit.base_url == "http://flag:11434"


def test_urls_are_openai_compatible() -> None:
    settings = LlmSettings(base_url="http://localhost:11434/", model="m")
    assert settings.chat_url == "http://localhost:11434/v1/chat/completions"
    assert settings.models_url == "http://localhost:11434/v1/models"


def test_probe_reports_availability() -> None:
    reachable = LlmClient(SETTINGS, RecordingTransport({"data": []}))
    assert reachable.available() is True

    unreachable = LlmClient(
        SETTINGS, RecordingTransport(urllib.error.URLError("connection refused"))
    )
    assert unreachable.available() is False


def test_request_shape_and_timeout() -> None:
    transport = RecordingTransport(_chat("Ich reiche hiermit meinen Antrag ein."))
    client = LlmClient(SETTINGS, transport)
    assert client.rewrite_note("Antrag eingereicht.") is not None

    url, body, timeout = transport.calls[0]
    assert url.endswith("/v1/chat/completions")
    assert body is not None
    assert body["model"] == "testmodel"
    assert body["stream"] is False
    assert body["temperature"] == 0.0
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert "Antrag eingereicht." in body["messages"][1]["content"]
    assert timeout == SETTINGS.timeout


@pytest.mark.parametrize(
    "answer",
    [
        urllib.error.URLError("refused"),
        TimeoutError("timed out"),
        json.JSONDecodeError("bad", "doc", 0),
        OSError("socket exploded"),
    ],
)
def test_transport_failures_return_none(answer: Exception) -> None:
    client = LlmClient(SETTINGS, RecordingTransport(answer))
    assert client.rewrite_note("Antrag eingereicht.") is None


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": ["nope"]},
        {"choices": [{"message": {}}]},
        "not a dict",
    ],
)
def test_malformed_responses_return_none(document: Any) -> None:
    client = LlmClient(SETTINGS, RecordingTransport(document))
    assert client.rewrite_note("Antrag eingereicht.") is None


@pytest.mark.parametrize(
    "content",
    [
        None,
        123,
        "",
        "zu kurz",
        "x" * (MAX_NOTE_CHARS + 1),
        '{"note": "Ich reiche hiermit meinen Antrag ein und bitte um Pruefung."}',
        "Ich kann diese Anfrage leider nicht bearbeiten, da sie gegen Richtlinien.",
        "As an AI language model I cannot rewrite this text for you, sorry indeed.",
    ],
)
def test_refusals_and_garbage_are_discarded(content: Any) -> None:
    assert clean_note(content) is None


def test_a_plain_sentence_is_accepted_and_whitespace_normalized() -> None:
    cleaned = clean_note("  Guten Tag,\n ich sende Ihnen   meinen Antrag zu. ")
    assert cleaned == "Guten Tag, ich sende Ihnen meinen Antrag zu."


def test_llm_paraphraser_uses_the_model_answer_for_the_note_only() -> None:
    spec = _spec()
    canonical = render_payload(spec, rng=item_rng(42, spec.scenario_id))
    client = LlmClient(
        SETTINGS, RecordingTransport(_chat("Anbei mein Antrag, bitte um Bearbeitung."))
    )
    result = LlmParaphraser(client, DeterministicParaphraser()).apply(
        spec, canonical, item_rng(42, spec.scenario_id)
    )
    assert result.provenance == "llm"
    assert (
        result.payload["data"]["antrag"]["hinweistext"]
        == "Anbei mein Antrag, bitte um Bearbeitung."
    )
    assert mapped_values(result.payload) == mapped_values(canonical)


def test_llm_paraphraser_falls_back_and_records_the_fallback() -> None:
    spec = _spec()
    canonical = render_payload(spec, rng=item_rng(42, spec.scenario_id))
    client = LlmClient(SETTINGS, RecordingTransport(_chat("nope")))
    result = LlmParaphraser(client, DeterministicParaphraser()).apply(
        spec, canonical, item_rng(42, spec.scenario_id)
    )
    assert result.provenance == "deterministic"
    assert result.payload["data"]["antrag"]["hinweistext"]
    assert mapped_values(result.payload) == mapped_values(canonical)
