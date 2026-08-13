"""Optional LLM client for the paraphrase pass (OpenAI-compatible endpoint).

Three constraints shape this module:

1. **No hard dependency.** Generation must work on a machine that has never
   heard of Ollama. The client probes the endpoint; every failure mode
   (unreachable, timeout, HTTP error, garbage, refusal) returns ``None`` and the
   caller falls back to the deterministic pass. Nothing here raises upward.
2. **No new runtime dependency.** ``urllib.request`` from the standard library
   is enough for one POST; the transport is injectable so tests never touch a
   socket.
3. **The model may not touch ground truth.** It rewrites one free-text cover
   note and nothing else. The note lives at a payload path no ``field_map``
   references, and the build re-runs the pipeline over every generated item
   afterwards, so a model that ignores its instructions costs surface realism,
   never a wrong label.

Base URL and model come from the CLI flags or from ``EINGANGSLOTSE_LLM_BASE_URL``
/ ``EINGANGSLOTSE_LLM_MODEL``. With no base URL configured, no probe is
attempted at all: a machine that happens to run a local model must not silently
produce a different corpus than the committed one.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

BASE_URL_ENV = "EINGANGSLOTSE_LLM_BASE_URL"
MODEL_ENV = "EINGANGSLOTSE_LLM_MODEL"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_TIMEOUT = 20.0

#: Accepted length window for a rewritten cover note, in characters.
MIN_NOTE_CHARS = 20
MAX_NOTE_CHARS = 400

#: Characters that suggest the model answered with markup or JSON instead of
#: a sentence; such answers are discarded rather than repaired.
FORBIDDEN_NOTE_CHARS = "{}[]<>"

SYSTEM_PROMPT = (
    "Du formulierst kurze Anschreiben an eine deutsche Behoerde neu. "
    "Antworte ausschliesslich mit dem umformulierten Text, ohne Anrede an mich, "
    "ohne Erklaerung, ohne Aufzaehlung, ohne Formatierung."
)

USER_PROMPT = (
    "Formuliere den folgenden Begleittext eines Antrags in einem anderen Stil "
    "neu. Nenne keine neuen Tatsachen, keine Zahlen, keine Daten und keine "
    "Namen. Hoechstens zwei Saetze.\n\nText:\n{note}"
)

#: url, json body (None means GET), timeout -> decoded JSON response.
Transport = Callable[[str, dict[str, Any] | None, float], dict[str, Any]]


@dataclass(frozen=True)
class LlmSettings:
    """Where the paraphrase model lives."""

    base_url: str
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/models"


def settings_from_env(
    base_url: str | None = None, model: str | None = None
) -> LlmSettings | None:
    """Build settings from CLI values falling back to env vars, or None."""
    resolved_url = base_url or os.environ.get(BASE_URL_ENV)
    if not resolved_url:
        return None
    return LlmSettings(
        base_url=resolved_url,
        model=model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL,
    )


def urllib_transport(
    url: str, body: dict[str, Any] | None, timeout: float
) -> dict[str, Any]:
    """Default transport: one request, JSON in, JSON out."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    # The URL is operator-supplied and points at a local model endpoint.
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return decoded


class LlmClient:
    """Minimal OpenAI-compatible chat client that never raises."""

    def __init__(
        self, settings: LlmSettings, transport: Transport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport or urllib_transport

    @property
    def settings(self) -> LlmSettings:
        return self._settings

    def available(self) -> bool:
        """True when the endpoint answers a model listing in time."""
        try:
            document = self._transport(self._settings.models_url, None, 5.0)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(document, dict)

    def rewrite_note(self, note: str) -> str | None:
        """Rewrite one cover note, or None when the answer is unusable."""
        body = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT.format(note=note)},
            ],
            "temperature": 0.0,
            "stream": False,
        }
        try:
            document = self._transport(
                self._settings.chat_url, body, self._settings.timeout
            )
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return None
        return clean_note(_first_message(document))


def _first_message(document: object) -> object:
    if not isinstance(document, dict):
        return None
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    return message.get("content")


def clean_note(content: object) -> str | None:
    """Accept a model answer only if it looks like a plain German sentence.

    A refusal ("Ich kann diese Anfrage nicht ..."), a JSON blob, an empty
    string, a wall of text or a non-string all return None so the caller falls
    back deterministically.
    """
    if not isinstance(content, str):
        return None
    text = " ".join(content.split())
    if not MIN_NOTE_CHARS <= len(text) <= MAX_NOTE_CHARS:
        return None
    if any(character in text for character in FORBIDDEN_NOTE_CHARS):
        return None
    lowered = text.lower()
    if lowered.startswith(("ich kann ", "als ki", "as an ai", "i cannot", "sorry")):
        return None
    return text
