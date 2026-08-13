"""The optional live extractor: an OpenAI-compatible endpoint, schema-constrained.

Same three constraints as the corpus paraphraser this client is copied from
(ADR-012), for the same reasons, and one more that is specific to extraction:

1. **No hard dependency.** No probe unless an endpoint is explicitly configured.
   A developer who happens to run Ollama must not silently get different
   evidence than the machine next to them.
2. **No new runtime dependency.** ``urllib.request`` is enough for one POST and
   the transport is injectable, so tests never touch a socket.
3. **Every failure is "no proposals".** Unreachable, timeout, HTTP error,
   non-JSON, JSON that does not fit the schema, a refusal, a model that answered
   in prose: all of them return an empty tuple, and the pipeline already treats
   an unextracted field as a gap that pushes toward tier 3. The one thing this
   client may never do is raise into the pipeline or return a value nobody
   checked.
4. **It cannot bypass verification.** Its proposals go through
   :mod:`engine.extract.verify` exactly like the replay extractor's, and the
   verifier does not know which one produced them. This is the whole reason the
   architecture can afford to call a model at all.

**Structured outputs.** The request carries a JSON Schema in ``response_format``
(the OpenAI-compatible spelling that Ollama's ``/v1`` endpoint accepts), so the
model is constrained to the proposal shape rather than asked politely for it.
Constrained decoding is not verification: it guarantees the SHAPE, and the
double lock is what checks the CONTENT.

**Offsets.** The second lock needs a character offset and no language model can
count characters across a page, so the text is presented in numbered chunks with
their start offsets and the model is asked to add the position inside the chunk.
Models are demonstrably poor at this; that is a measured, reported number
(``offset_out_of_range`` and ``quote_mismatch`` in the failure histogram), not a
hidden one, and the consequence of getting it wrong is a discard. The
alternative - letting the system find the quote and calling the result an offset
- would collapse the two locks into one and is the thing P-8 exists to prevent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engine.config_loader import LivePolicy
from engine.extract.proposal import Proposal

#: url, json body (None means GET), timeout -> decoded JSON response.
Transport = Callable[[str, dict[str, Any] | None, float], dict[str, Any]]

#: The shape the endpoint is constrained to. Deliberately minimal: four scalar
#: fields per proposal and nothing optional, so "the model left something out"
#: is a decoding error rather than a proposal with a hole in it.
PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "angaben": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "value": {"type": "string"},
                    "quote": {"type": "string"},
                    "offset": {"type": "integer"},
                },
                "required": ["field", "value", "quote", "offset"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["angaben"],
    "additionalProperties": False,
}


class LiveExtractionError(RuntimeError):
    """Raised only when a live run was EXPLICITLY requested and cannot happen.

    Never raised from the pipeline: there the client degrades to no proposals.
    This is the loud half of ADR-012's rule - silence when nobody asked for a
    model, a clear error when somebody did and it is not there.
    """


@dataclass(frozen=True)
class LiveSettings:
    """Where a live extraction model lives, and how patient to be with it."""

    base_url: str
    model: str
    timeout: float = 30.0
    attempts: int = 2
    chunk_chars: int = 96

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/models"

    @property
    def extractor_id(self) -> str:
        """Provenance stamped onto every record this model produces."""
        return f"llm:{self.model}"


def settings_from_policy(
    policy: LivePolicy,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> LiveSettings | None:
    """Settings from the config, overridden by explicit arguments, or None."""
    resolved_url = base_url or policy.base_url
    resolved_model = model or policy.model
    if not resolved_url or not resolved_model:
        return None
    return LiveSettings(
        base_url=resolved_url,
        model=resolved_model,
        timeout=policy.timeout_seconds,
        attempts=policy.attempts,
        chunk_chars=policy.chunk_chars,
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


class LiveExtractor:
    """Minimal OpenAI-compatible extraction client that never raises upward."""

    def __init__(
        self,
        settings: LiveSettings,
        *,
        system_prompt: str,
        user_prompt: str,
        transport: Transport | None = None,
    ) -> None:
        self._settings = settings
        self._system = system_prompt
        self._user = user_prompt
        self._transport = transport or urllib_transport

    @property
    def settings(self) -> LiveSettings:
        return self._settings

    @property
    def extractor_id(self) -> str:
        return self._settings.extractor_id

    def available(self) -> bool:
        """True when the endpoint answers a model listing in time."""
        try:
            document = self._transport(self._settings.models_url, None, 5.0)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(document, dict)

    def require_available(self) -> None:
        """Raise when an explicitly requested endpoint is not there."""
        if not self.available():
            raise LiveExtractionError(
                f"live extraction was requested but {self._settings.base_url} "
                f"is unreachable"
            )

    def propose(
        self,
        *,
        part_id: str,
        text: str,
        fields: Mapping[str, str],
    ) -> tuple[Proposal, ...]:
        """Ask the model for proposals over one part; () on any failure at all.

        Args:
            part_id: the content part the offsets will refer to.
            text: the part's NORMALIZED, already redacted text.
            fields: field id -> description, straight from the procedure's
                requirements. The requirement wording is what a caseworker
                would read, so it is what the model reads too - one definition
                of what a field means, not a second one written for a prompt.
        """
        if not text or not fields:
            return ()
        body = self._body(text, fields)
        for _ in range(max(1, self._settings.attempts)):
            try:
                document = self._transport(
                    self._settings.chat_url, body, self._settings.timeout
                )
            except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
                continue
            parsed = parse_answer(_first_message(document))
            if parsed is None:
                continue
            return tuple(
                Proposal(
                    field=str(item["field"]),
                    value=str(item["value"]),
                    quote=str(item["quote"]),
                    part_id=part_id,
                    offset=int(item["offset"]),
                    extractor_id=self.extractor_id,
                )
                for item in parsed
                if str(item["field"]) in fields
            )
        return ()

    def _body(self, text: str, fields: Mapping[str, str]) -> dict[str, Any]:
        rendered = self._user.format(
            fields="\n".join(
                f"- {field}: {description}"
                for field, description in sorted(fields.items())
            ),
            text=chunk_text(text, self._settings.chunk_chars),
        )
        return {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": self._system},
                {"role": "user", "content": rendered},
            ],
            "temperature": 0.0,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "eingangslotse_angaben",
                    "strict": True,
                    "schema": PROPOSAL_SCHEMA,
                },
            },
        }


def chunk_text(text: str, size: int) -> str:
    """The text in numbered chunks, each prefixed with its start offset.

    ``[412] nummer lautet [[PII|VSNR|...]], mein`` - the model adds the position
    inside the chunk to 412. Short chunks keep that addition short, which is the
    only thing that makes the offset lock answerable at all by something that
    reads tokens rather than characters.
    """
    if size <= 0:  # pragma: no cover - the config model forbids it
        return text
    return "\n".join(
        f"[{start}] {text[start : start + size]}" for start in range(0, len(text), size)
    )


def parse_answer(content: object) -> list[dict[str, Any]] | None:
    """Validate a model answer into proposal dictionaries, or None.

    None means "unusable", and every unusable answer is treated the same: a
    refusal, a wall of prose, a JSON object with the wrong keys, an offset that
    is a string, a null value. None of them is repaired.
    """
    if isinstance(content, dict):
        document: Any = content
    elif isinstance(content, str):
        try:
            document = json.loads(content)
        except (ValueError, json.JSONDecodeError):
            return None
    else:
        return None
    if not isinstance(document, dict):
        return None
    items = document.get("angaben")
    if not isinstance(items, list):
        return None
    parsed: list[dict[str, Any]] = []
    for item in items:
        entry = _entry(item)
        if entry is None:
            return None
        parsed.append(entry)
    return parsed


def _entry(item: object) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    field, value, quote, offset = (
        item.get("field"),
        item.get("value"),
        item.get("quote"),
        item.get("offset"),
    )
    if not isinstance(field, str) or not isinstance(value, str):
        return None
    if not isinstance(quote, str):
        return None
    if isinstance(offset, bool) or not isinstance(offset, int):
        return None
    return {"field": field, "value": value, "quote": quote, "offset": offset}


def _first_message(document: object) -> object:
    if not isinstance(document, Mapping):
        return None
    choices = document.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, str) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    message = first.get("message")
    if not isinstance(message, Mapping):
        return None
    return message.get("content")
