"""The optional model-backed member of the recognizer union.

Presidio's ``AnalyzerEngine`` over spaCy's ``de_core_news_lg``, contributing the
entity types no regular expression can carry: bare person names, and place names
that appear without a street grammar around them.

Three properties this module has to have, and all three are why the import is
inside the functions rather than at the top of the file:

* ``engine.redact`` must import cleanly on a core install. The ``[redact]``
  extra is optional, the deterministic recognizers are not, and a missing wheel
  may never take down the redaction boundary.
* :func:`available` has to be answerable without side effects, so callers can
  skip-mark tests and the eval report can say honestly whether the number it
  prints was measured with the model or without it.
* The recall gate that matters without the extra is a gate over the
  deterministic kinds only. NAME is the one kind the deterministic union cannot
  cover, which is exactly the finding P-7 records: single German NER is weak,
  the union is the answer, and the measurement has to say which half produced
  which number.

Presidio is explicitly documented by Microsoft as not guaranteeing recall. That
is the reason this is a UNION member and not the detector.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from engine.redact.placeholders import Kind
from engine.redact.recognizers import Detection, Evidence

#: The spaCy model the German pipeline needs. ``sm``/``md`` are markedly worse
#: on person names in administrative prose, so the union pins the large one.
SPACY_MODEL = "de_core_news_lg"

#: Presidio entity type -> our placeholder kind. Everything else is dropped:
#: DATE_TIME and NRP would fire on procedural content that the structured plane
#: legitimately reads, and this member has no business deciding those.
ENTITY_KINDS: dict[str, Kind] = {
    "PERSON": Kind.NAME,
    "LOCATION": Kind.ADDR,
    "ORGANIZATION": Kind.ORG,
}

NER_RECOGNIZER_ID = "presidio_spacy_de"


@runtime_checkable
class NerMember(Protocol):
    """What the detector needs from a model-backed member."""

    def scan(self, text: str) -> tuple[Detection, ...]:
        """Entity spans in ``text``, already mapped onto placeholder kinds."""
        ...

    def describe(self) -> dict[str, object]:
        """What this member is, for the eval report."""
        ...


class PresidioNerMember:
    """Thin adapter over a Presidio ``AnalyzerEngine``."""

    def __init__(self, analyzer: Any) -> None:
        self._analyzer = analyzer

    def scan(self, text: str) -> tuple[Detection, ...]:
        results = self._analyzer.analyze(
            text=text, language="de", entities=sorted(ENTITY_KINDS)
        )
        return tuple(
            Detection(
                start=int(result.start),
                end=int(result.end),
                kind=ENTITY_KINDS[str(result.entity_type)],
                recognizer_id=NER_RECOGNIZER_ID,
                validated=True,
                # A model guess, and the merge rule has to know it: spaCy calls
                # an e-mail address an ORGANIZATION often enough that leaving
                # this at the default would let it rename one.
                evidence=Evidence.MODEL,
            )
            for result in results
            if str(result.entity_type) in ENTITY_KINDS
        )

    def describe(self) -> dict[str, object]:
        return {
            "recognizer_id": NER_RECOGNIZER_ID,
            "model": SPACY_MODEL,
            "entities": sorted(ENTITY_KINDS),
        }


def available() -> bool:
    """Whether the ``[redact]`` extra and the spaCy model are both installed."""
    return load_ner() is not None


def unavailable_reason() -> str | None:
    """Why the NER member is not usable, or None when it is."""
    return _build_ner()[1]


@lru_cache(maxsize=1)
def load_ner() -> NerMember | None:
    """The NER member, or None on a core install. Built at most once."""
    return _build_ner()[0]


@lru_cache(maxsize=1)
def _build_ner() -> tuple[NerMember | None, str | None]:
    """Try to construct the analyzer; never raises, always explains."""
    try:  # pragma: no cover - exercised only where the extra is installed
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "de", "model_name": SPACY_MODEL}],
            }
        )
        engine = provider.create_engine()
        analyzer = AnalyzerEngine(nlp_engine=engine, supported_languages=["de"])
    except Exception as error:  # any failure at all means "not available"
        return None, f"{type(error).__name__}: {error}"
    return PresidioNerMember(analyzer), None  # pragma: no cover - see above


def reset_cache() -> None:
    """Forget the cached member; used by tests that patch the import path."""
    load_ner.cache_clear()
    _build_ner.cache_clear()
