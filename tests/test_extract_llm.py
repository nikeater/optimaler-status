"""The optional live extractor: every way it can fail is "no proposals".

The client is the only place in the evidence plane that talks to a language
model, so the interesting tests are the negative ones. Unreachable endpoint,
timeout, HTTP error, a body that is not JSON, JSON with the wrong shape, an
offset that arrived as a string, a refusal written in prose: all of them return
an empty tuple, and the pipeline already treats "nothing extracted" as a gap
that pushes toward tier 3.

The transport is injected, so nothing here opens a socket. The one test that
exercises the real ``urllib`` transport patches ``urlopen`` itself, because the
alternative is a module nobody ever runs until production.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import TracebackType
from typing import Any

import pytest

from engine.config_loader import ConfigBundle, LivePolicy
from engine.extract import (
    PROPOSAL_SCHEMA,
    LiveExtractionError,
    LiveExtractor,
    LiveSettings,
    chunk_text,
    parse_answer,
    settings_from_policy,
    verify_proposal,
)
from engine.extract.llm import urllib_transport
from tests.factories import make_text_layer

TEXT = "Rentenart: regelaltersrente. Rentenbeginn: 2026-11-01."
FIELDS = {"rentenart": "Beantragte Rentenart.", "rentenbeginn": "Gewuenschter Beginn."}
SETTINGS = LiveSettings(base_url="http://localhost:11434", model="mistral-small")


def answer(*items: dict[str, Any]) -> dict[str, Any]:
    """A well-formed OpenAI-compatible chat completion."""
    return {
        "choices": [
            {"message": {"content": json.dumps({"angaben": list(items)})}},
        ]
    }


def transport_returning(*documents: Any):  # type: ignore[no-untyped-def]
    """A transport that answers each call from ``documents``, then repeats."""
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


# ------------------------------------------------------------- settings ---


def test_no_endpoint_means_no_client_at_all(config: ConfigBundle) -> None:
    """The shipped config has live extraction off, and off must stay silent."""
    assert config.extraction.live.enabled is False
    assert settings_from_policy(config.extraction.live) is None


def test_settings_come_from_config_and_may_be_overridden() -> None:
    policy = LivePolicy(
        enabled=True,
        base_url="http://configured:11434/",
        model="from-config",
        timeout_seconds=7.0,
        attempts=3,
        chunk_chars=64,
    )
    settings = settings_from_policy(policy)
    assert settings is not None
    assert settings.chat_url == "http://configured:11434/v1/chat/completions"
    assert settings.models_url == "http://configured:11434/v1/models"
    assert settings.extractor_id == "llm:from-config"
    assert (settings.timeout, settings.attempts, settings.chunk_chars) == (7.0, 3, 64)

    swapped = settings_from_policy(policy, base_url="http://other:8080", model="b")
    assert swapped is not None
    assert swapped.extractor_id == "llm:b"


def test_an_enabled_policy_without_an_endpoint_is_a_config_error() -> None:
    """Silently degrading every item to tier 3 is worse than not starting."""
    with pytest.raises(ValueError, match="base_url"):
        LivePolicy(enabled=True, model="m")


# --------------------------------------------------------------- prompt ---


def test_the_text_is_presented_in_numbered_chunks() -> None:
    """No language model counts characters across a page, so the offsets are
    handed to it and it only has to add a small number."""
    assert chunk_text("abcdefgh", 3) == "[0] abc\n[3] def\n[6] gh"
    assert chunk_text("", 3) == ""


def test_the_request_carries_the_schema_the_requirements_and_no_temperature(
    config: ConfigBundle,
) -> None:
    transport = transport_returning(answer())
    extractor = LiveExtractor(
        SETTINGS,
        system_prompt=config.extraction.prompt.system,
        user_prompt=config.extraction.prompt.user,
        transport=transport,
    )
    extractor.propose(part_id="part-text-0", text=TEXT, fields=FIELDS)
    url, body, timeout = transport.calls[0]  # type: ignore[attr-defined]
    assert url == SETTINGS.chat_url
    assert timeout == SETTINGS.timeout
    assert body is not None
    assert body["temperature"] == 0.0
    assert body["stream"] is False
    assert body["response_format"]["json_schema"]["schema"] == PROPOSAL_SCHEMA
    assert body["response_format"]["json_schema"]["strict"] is True
    rendered = body["messages"][1]["content"]
    assert "Beantragte Rentenart." in rendered, "the requirement wording IS the prompt"
    assert "[0] " in rendered


# ------------------------------------------------------- parsing answers ---


@pytest.mark.parametrize(
    "content",
    [
        None,
        42,
        "das darf ich leider nicht beantworten",
        json.dumps({"angaben": "nicht mal eine Liste"}),
        json.dumps({"etwas_anderes": []}),
        json.dumps(["angaben"]),
        json.dumps({"angaben": ["nur ein String"]}),
        json.dumps({"angaben": [{"field": "rentenart"}]}),
        json.dumps(
            {"angaben": [{"field": "x", "value": "y", "quote": "z", "offset": "3"}]}
        ),
        json.dumps(
            {"angaben": [{"field": "x", "value": "y", "quote": "z", "offset": True}]}
        ),
        json.dumps(
            {"angaben": [{"field": "x", "value": None, "quote": "z", "offset": 3}]}
        ),
        json.dumps(
            {"angaben": [{"field": "x", "value": "y", "quote": 5, "offset": 3}]}
        ),
    ],
)
def test_an_unusable_answer_is_none_and_is_never_repaired(content: object) -> None:
    assert parse_answer(content) is None


def test_a_usable_answer_parses_from_a_string_or_from_an_object() -> None:
    item = {"field": "rentenart", "value": "r", "quote": "q", "offset": 3}
    assert parse_answer(json.dumps({"angaben": [item]})) == [item]
    assert parse_answer({"angaben": [item]}) == [item]
    assert parse_answer({"angaben": []}) == []


# ----------------------------------------------------- every failure mode ---


@pytest.mark.parametrize(
    "document",
    [
        urllib.error.URLError("connection refused"),
        urllib.error.HTTPError("u", 500, "boom", {}, None),  # type: ignore[arg-type]
        TimeoutError("timed out"),
        ValueError("not JSON"),
        {},
        {"choices": []},
        {"choices": "not a list"},
        {"choices": [{"no_message": 1}]},
        {"choices": [{"message": {"content": "ich kann das nicht"}}]},
        {"choices": [{"message": "not a mapping"}]},
        {"choices": [{"message": {}}]},
        {"choices": ["not a mapping either"]},
        {"choices": "choices as a bare string"},
        # A transport that ignores its own type and hands back a list: the
        # client is defensive against its injection point too.
        ["not an object at all"],
    ],
)
def test_every_transport_and_protocol_failure_degrades_to_no_proposals(
    document: Any,
) -> None:
    extractor = LiveExtractor(
        SETTINGS,
        system_prompt="s",
        user_prompt="{fields}{text}",
        transport=transport_returning(document),
    )
    assert extractor.propose(part_id="part-text-0", text=TEXT, fields=FIELDS) == ()


def test_a_second_attempt_is_made_and_a_late_answer_is_accepted() -> None:
    transport = transport_returning(
        urllib.error.URLError("first one failed"),
        answer(
            {
                "field": "rentenart",
                "value": "regelaltersrente",
                "quote": "Rentenart: regelaltersrente",
                "offset": 0,
            }
        ),
    )
    extractor = LiveExtractor(
        SETTINGS, system_prompt="s", user_prompt="{fields}{text}", transport=transport
    )
    proposals = extractor.propose(part_id="part-text-0", text=TEXT, fields=FIELDS)
    assert len(transport.calls) == 2  # type: ignore[attr-defined]
    assert [claim.field for claim in proposals] == ["rentenart"]
    assert proposals[0].extractor_id == "llm:mistral-small"


def test_a_field_nobody_asked_about_is_dropped_before_it_is_even_verified() -> None:
    extractor = LiveExtractor(
        SETTINGS,
        system_prompt="s",
        user_prompt="{fields}{text}",
        transport=transport_returning(
            answer(
                {
                    "field": "lieblingsfarbe",
                    "value": "blau",
                    "quote": "blau",
                    "offset": 0,
                },
                {
                    "field": "rentenart",
                    "value": "regelaltersrente",
                    "quote": "Rentenart: regelaltersrente",
                    "offset": 0,
                },
            )
        ),
    )
    proposals = extractor.propose(part_id="part-text-0", text=TEXT, fields=FIELDS)
    assert [claim.field for claim in proposals] == ["rentenart"]


def test_nothing_to_read_or_nothing_to_look_for_asks_nobody() -> None:
    transport = transport_returning(answer())
    extractor = LiveExtractor(
        SETTINGS, system_prompt="s", user_prompt="{fields}{text}", transport=transport
    )
    assert extractor.propose(part_id="p", text="", fields=FIELDS) == ()
    assert extractor.propose(part_id="p", text=TEXT, fields={}) == ()
    assert transport.calls == []  # type: ignore[attr-defined]


def test_a_confident_lie_still_has_to_pass_the_double_lock(
    config: ConfigBundle,
) -> None:
    """The whole reason a model may be called at all."""
    extractor = LiveExtractor(
        SETTINGS,
        system_prompt="s",
        user_prompt="{fields}{text}",
        transport=transport_returning(
            answer(
                {
                    "field": "rentenart",
                    "value": "altersrente_langjaehrig",
                    "quote": "Rentenart: altersrente_langjaehrig",
                    "offset": 0,
                }
            )
        ),
    )
    (claim,) = extractor.propose(part_id="part-text-0", text=TEXT, fields=FIELDS)
    layer = make_text_layer(("part-text-0", "born_digital", TEXT))
    outcome = verify_proposal(claim, layer, config=config.extraction)
    assert not outcome.accepted
    assert outcome.record is None


# ------------------------------------------------------- availability ---


def test_availability_is_answered_without_guessing() -> None:
    reachable = LiveExtractor(
        SETTINGS,
        system_prompt="s",
        user_prompt="{fields}{text}",
        transport=transport_returning({"data": []}),
    )
    assert reachable.available() is True
    reachable.require_available()
    assert reachable.settings is SETTINGS

    unreachable = LiveExtractor(
        SETTINGS,
        system_prompt="s",
        user_prompt="{fields}{text}",
        transport=transport_returning(urllib.error.URLError("nothing there")),
    )
    assert unreachable.available() is False
    with pytest.raises(LiveExtractionError, match="unreachable"):
        unreachable.require_available()


# --------------------------------------------------------- the transport ---


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def test_the_default_transport_posts_json_and_reads_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float = 0.0) -> FakeResponse:
        seen["method"] = request.method
        seen["url"] = request.full_url
        seen["data"] = request.data
        seen["timeout"] = timeout
        return FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert urllib_transport("http://x/v1/chat", {"a": 1}, 3.0) == {"ok": True}
    assert seen["method"] == "POST"
    assert json.loads(seen["data"]) == {"a": 1}
    assert seen["timeout"] == 3.0

    # A GET (the availability probe) carries no body.
    assert urllib_transport("http://x/v1/models", None, 1.0) == {"ok": True}
    assert seen["method"] == "GET"
    assert seen["data"] is None


def test_the_default_transport_refuses_a_non_object_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=0.0: FakeResponse(b"[1, 2, 3]"),
    )
    with pytest.raises(ValueError, match="JSON object"):
        urllib_transport("http://x/v1/chat", {}, 1.0)
