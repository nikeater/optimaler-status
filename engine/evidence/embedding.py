"""The optional model-backed embedder, built exactly like the NER member.

``engine/redact/ner.py`` set the pattern in part 04 and this module repeats it
deliberately, because the three properties it buys are the same three:

* ``engine.evidence`` imports cleanly without the ``[classify]`` extra. The
  routing rules are not optional; a missing torch wheel may not take the
  evidence plane down with it.
* :func:`available` answers without side effects, so a test can skip-mark
  itself and a report can say honestly whether a number was measured with a
  model or without one.
* Nothing in the verification gate ever calls :func:`load_embedder`. The gate's
  numbers must not depend on which wheels a machine happens to have; the real
  model runs in the opt-in eval section and nowhere else.

Default model: ``intfloat/multilingual-e5-small``. Multilingual because the
corpus is German and most small English-first encoders are markedly worse on
German administrative prose; e5-class because it is an asymmetric retrieval
model, which is exactly the shape of this problem - an inbound letter is a
query, a Referat's responsibility text is a passage. The ``query:`` and
``passage:`` prefixes are not decoration: e5 was trained with them, and leaving
them off costs measurable accuracy. They live here, in the adapter, because
which prefix a model wants is a property of the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from engine.evidence.classify import Vector, normalize

#: The dev default. Agency-editable in the routing config's classifier block.
DEFAULT_MODEL_ID = "intfloat/multilingual-e5-small"

#: e5 asymmetry, as the model card specifies it.
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


class SentenceTransformerEmbedder:
    """Thin adapter over a ``sentence_transformers.SentenceTransformer``."""

    def __init__(self, model: Any, model_id: str) -> None:
        self._model = model
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        """Embed the per-unit texts as passages."""
        return self._encode([PASSAGE_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> Vector:
        """Embed one item's text as a query."""
        return self._encode([QUERY_PREFIX + text])[0]

    def _encode(self, texts: list[str]) -> tuple[Vector, ...]:
        vectors = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        # Back to plain tuples of floats: everything downstream is pure Python
        # and must not start depending on a numpy array's semantics.
        return tuple(normalize(tuple(float(value) for value in row)) for row in vectors)


def available(model_id: str = DEFAULT_MODEL_ID) -> bool:
    """Whether the ``[classify]`` extra and the model weights are both usable."""
    return load_embedder(model_id) is not None


def unavailable_reason(model_id: str = DEFAULT_MODEL_ID) -> str | None:
    """Why the embedder is not usable, or None when it is."""
    return _build_embedder(model_id)[1]


@lru_cache(maxsize=2)
def load_embedder(
    model_id: str = DEFAULT_MODEL_ID,
) -> SentenceTransformerEmbedder | None:
    """The model-backed embedder, or None. Built at most once per model id."""
    return _build_embedder(model_id)[0]


@lru_cache(maxsize=2)
def _build_embedder(
    model_id: str,
) -> tuple[SentenceTransformerEmbedder | None, str | None]:
    """Try to load the model; never raises, always explains.

    "Never raises" includes the network: the first call downloads weights, and
    an unreachable Hugging Face endpoint has to read as "not available", not as
    a traceback out of an eval run.
    """
    try:  # pragma: no cover - exercised only where the extra is installed
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_id)
    except Exception as error:  # any failure at all means "not available"
        return None, f"{type(error).__name__}: {error}"
    return SentenceTransformerEmbedder(model, model_id), None  # pragma: no cover


def reset_cache() -> None:
    """Forget the cached embedder; used by tests that patch the import path."""
    load_embedder.cache_clear()
    _build_embedder.cache_clear()
