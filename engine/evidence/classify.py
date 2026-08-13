"""Zero-shot unit classification: a fallback suggestion, never an override.

Four of gold v4's items reach no routing rule at all (a Grundsicherungs-Anfrage
that belongs to another Traeger, a submission without any Verfahrenskennung, a
Terminanfrage, two letters naming two procedures at once). They land at tier 3
with `routed_unit_id: null` - in nobody's queue. This module is the answer to
"who would probably have to look at this", and it is deliberately the weakest
kind of answer the system can give:

* **Rules first, fallback only.** :func:`classify` is consulted only when no
  routing rule fired. A rule is a sentence an agency wrote; a cosine similarity
  is a guess about what a sentence resembles, and the guess never overrules the
  sentence.
* **Zero-shot.** Nothing is trained on anything. The unit texts come from the
  taxonomy an agency edits (``TaxonomyNode.name`` plus ``responsibilities``), so
  re-cutting a Referat re-aims the classifier by editing YAML. There is no
  model to retrain and no label set to maintain, which is the only shape of
  classifier that keeps "config is the product" true.
* **Log-only until an agency says otherwise.** Whether a suggestion may govern a
  decision is not decided here (see ``engine/decide``: the admitted routing
  sources default to RULE alone). This module only produces evidence.
* **A failure is "no suggestion".** Missing model, empty taxonomy, an item with
  no readable text: every one of them returns ``None``, which is exactly what
  the system did before this module existed. Nothing here can raise into the
  pipeline.

**Raw similarity is not a confidence.** A cosine of 0.87 between an item and a
Referat's responsibility text is a number about two vectors, not a probability
that the Referat is right. Until a :class:`Calibration` fitted on a gold set
says what a raw score is worth, a suggestion carries ``confidence`` 0.0 and its
honest ``raw_score``; ``eval/calibration.py`` is where the mapping comes from
and the loader refuses to enable the classifier without one.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from engine.config_loader import ClassifierConfig
from engine.namespaces import PAYLOAD_PREFIX, TEXT_PREFIX
from engine.redact.verify import mask_placeholders
from schemas.config import TaxonomyNode
from schemas.evidence import RoutingSource, RoutingSuggestion

#: One embedding. A tuple, because a suggestion must not be able to mutate the
#: unit vectors it was compared against.
Vector = tuple[float, ...]

#: What a suggestion produced WITHOUT a calibration block is worth to a
#: decision: nothing. It is recorded (that is the point of log-only) with its
#: raw score intact, and it can never reach a threshold, because the number it
#: would be compared against does not mean anything yet.
UNCALIBRATED_CONFIDENCE = 0.0

#: The reserved placeholder syntax (engine.redact.placeholders). A sealed value
#: is a random token; feeding it to an embedder would embed noise and, worse,
#: would make the suggestion depend on which token was drawn.
PLACEHOLDER_MARK = "[[PII|"

#: How much item text is embedded. An e5-class model truncates at its own
#: sequence length anyway; cutting deterministically here means the input does
#: not depend on which tokenizer happens to be installed.
MAX_ITEM_CHARS = 1200

#: Separator between rendered key facts of a structured item.
FACT_SEPARATOR = " | "


class Embedder(Protocol):
    """What the classifier needs from an embedding model.

    Two methods rather than one because asymmetric models (e5, GTE, BGE) want
    a different prefix on the thing being looked up than on the thing being
    searched, and which prefix that is belongs to the model adapter, never to
    the caller.
    """

    @property
    def model_id(self) -> str:
        """Provenance: what produced these vectors."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        """Embed the per-unit texts."""
        ...

    def embed_query(self, text: str) -> Vector:
        """Embed one item's text."""
        ...


class HashingEmbedder:
    """A deterministic stand-in: character n-grams hashed into a fixed vector.

    Not a model and not pretending to be one. It exists so every code path in
    this module - unit texts, item rendering, ranking, thresholds, calibration,
    the log-only integration - is exercised by tests on a machine with no
    ``[classify]`` extra, with the same numbers on every platform. ``hashlib``
    rather than ``hash()``: Python salts string hashing per process, and a
    classifier whose ranking changed between two runs of the same test would be
    untestable by construction.

    It does carry a little real signal (shared substrings raise the cosine),
    which is enough for a test to assert "the Reha letter ranks the Reha unit
    first" without asserting a number a real model would have to reproduce.
    """

    def __init__(self, *, dim: int = 96, min_n: int = 3, max_n: int = 5) -> None:
        if dim < 2:
            raise ValueError("dim must be at least 2")
        if not 1 <= min_n <= max_n:
            raise ValueError("expected 1 <= min_n <= max_n")
        self._dim = dim
        self._min_n = min_n
        self._max_n = max_n

    @property
    def model_id(self) -> str:
        return f"hashing-ngram-v1:dim{self._dim}"

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return tuple(self._embed(text) for text in texts)

    def embed_query(self, text: str) -> Vector:
        return self._embed(text)

    def _embed(self, text: str) -> Vector:
        buckets = [0.0] * self._dim
        folded = " ".join(text.lower().split())
        for size in range(self._min_n, self._max_n + 1):
            for start in range(0, max(len(folded) - size + 1, 0)):
                gram = folded[start : start + size]
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "big") % self._dim
                # The sign bit spreads collisions instead of piling them up:
                # two unrelated n-grams in one bucket cancel as often as they
                # reinforce, which keeps the cosine from drifting upward with
                # text length alone.
                buckets[index] += 1.0 if digest[4] & 1 else -1.0
        return normalize(tuple(buckets))


@dataclass(frozen=True)
class UnitText:
    """One organizational unit as the text it is compared against."""

    unit_id: str
    text: str


@dataclass(frozen=True)
class CalibrationBin:
    """Raw scores at or below ``upper`` are worth ``confidence``."""

    upper: float
    confidence: float


@dataclass(frozen=True)
class Calibration:
    """A fitted map from raw similarity to something a threshold may read.

    Data with provenance, never a code default: ``calibrated_on`` names the gold
    set, ``model_id`` the model whose scores were fitted, ``fitted_at`` the day.
    A calibration for one model says nothing about another, which is why the
    classifier refuses to apply one whose ``model_id`` does not match.
    """

    bins: tuple[CalibrationBin, ...]
    calibrated_on: str
    model_id: str
    fitted_at: str
    expected_calibration_error: float | None = None

    def apply(self, raw_score: float) -> float:
        """The calibrated confidence for a raw score.

        A step function over sorted bins, so it is monotone by construction:
        a higher raw score can never map to a lower confidence, and an agency
        reading the block can see exactly which score buys which number.
        """
        for entry in self.bins:
            if raw_score <= entry.upper:
                return entry.confidence
        return self.bins[-1].confidence if self.bins else UNCALIBRATED_CONFIDENCE

    def as_payload(self) -> dict[str, object]:
        """Journal/report-shaped view."""
        return {
            "calibrated_on": self.calibrated_on,
            "model_id": self.model_id,
            "fitted_at": self.fitted_at,
            "expected_calibration_error": self.expected_calibration_error,
            "bins": [
                {"upper": entry.upper, "confidence": entry.confidence}
                for entry in self.bins
            ],
        }


@dataclass(frozen=True)
class ClassifierSuggestion:
    """One classifier proposal, with everything needed to disbelieve it."""

    unit_id: str
    raw_score: float
    confidence: float
    margin: float
    model_id: str
    calibrated: bool
    ranking: tuple[tuple[str, float], ...]

    def as_routing_suggestion(self) -> RoutingSuggestion:
        """The contract shape. ``rule_ids`` stays empty: no rule fired."""
        return RoutingSuggestion(
            unit_id=self.unit_id,
            source=RoutingSource.CLASSIFIER,
            rule_ids=[],
            confidence=self.confidence,
            # No span: the suggestion is about the whole item, and pointing at
            # one passage would claim evidence the cosine does not have.
            evidence_span=None,
        )

    def as_payload(self) -> dict[str, object]:
        """Journal-shaped view for the evidence_assembled event."""
        return {
            "unit_id": self.unit_id,
            "raw_score": round(self.raw_score, 6),
            "confidence": self.confidence,
            "margin": round(self.margin, 6),
            "model_id": self.model_id,
            "calibrated": self.calibrated,
            "ranking": [
                {"unit_id": unit_id, "raw_score": round(score, 6)}
                for unit_id, score in self.ranking
            ],
        }


class UnitClassifier:
    """Cosine similarity between one item and every candidate unit text.

    Construction embeds the unit texts once; :meth:`suggest` embeds the item.
    Both halves are pure functions of their input, so two runs over the same
    corpus with the same embedder produce the same ranking.
    """

    def __init__(
        self,
        units: Sequence[UnitText],
        embedder: Embedder,
        *,
        calibration: Calibration | None = None,
        min_confidence: float = 0.0,
    ) -> None:
        self._units = tuple(units)
        self._embedder = embedder
        self._calibration = calibration
        self._min_confidence = min_confidence
        self._vectors = (
            embedder.embed_documents([unit.text for unit in self._units])
            if self._units
            else ()
        )

    @property
    def model_id(self) -> str:
        return self._embedder.model_id

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.unit_id for unit in self._units)

    @property
    def calibration(self) -> Calibration | None:
        return self._calibration

    def scores(self, item_text: str) -> tuple[tuple[str, float], ...]:
        """Every unit with its raw similarity, best first.

        Ties break on ``unit_id`` so the order is total: two units whose texts
        score identically must not swap places between runs.
        """
        if not self._units or not item_text.strip():
            return ()
        query = self._embedder.embed_query(item_text)
        scored = [
            (unit.unit_id, cosine(query, vector))
            for unit, vector in zip(self._units, self._vectors, strict=True)
        ]
        return tuple(sorted(scored, key=lambda entry: (-entry[1], entry[0])))

    def suggest(self, item_text: str) -> ClassifierSuggestion | None:
        """The best unit for an item, or None.

        None on every failure and every refusal: no units, no text, a model that
        raised, or a calibrated confidence below the configured minimum. The
        caller's behaviour for None is the behaviour it had before this module
        existed.
        """
        try:
            ranking = self.scores(item_text)
        except Exception:  # a model failure is "no suggestion", never an outage
            return None
        if not ranking:
            return None
        unit_id, raw_score = ranking[0]
        calibrated = self._calibration is not None
        confidence = (
            self._calibration.apply(raw_score)
            if self._calibration is not None
            else UNCALIBRATED_CONFIDENCE
        )
        # The minimum is a statement about calibrated confidence. Applying it to
        # a raw cosine would compare two different scales, so an uncalibrated
        # classifier proposes and lets the log say what it proposed - it cannot
        # be enabled anyway (the loader refuses).
        if calibrated and confidence < self._min_confidence:
            return None
        runner_up = ranking[1][1] if len(ranking) > 1 else 0.0
        return ClassifierSuggestion(
            unit_id=unit_id,
            raw_score=raw_score,
            confidence=confidence,
            margin=raw_score - runner_up,
            model_id=self.model_id,
            calibrated=calibrated,
            ranking=ranking,
        )


def classifier_from_config(
    settings: ClassifierConfig | None,
    nodes: Sequence[TaxonomyNode],
    embedder: Embedder | None,
) -> UnitClassifier | None:
    """Build the classifier the config describes, or None.

    None on every "there is nothing to build": no ``config/classifier/``, no
    embedder passed in (the gate's state - the model is never auto-loaded, so a
    measured number cannot depend on which wheels a machine has), or a taxonomy
    whose nodes all lack responsibilities.

    The import direction is one-way on purpose: the loader owns config SHAPES
    and knows nothing about the evidence plane, so the conversion from the YAML
    block to a :class:`Calibration` lives here rather than there.
    """
    if settings is None or embedder is None:
        return None
    units = unit_texts(nodes, exclude_unit_ids=set(settings.exclude_unit_ids))
    if not units:
        return None
    return UnitClassifier(
        units,
        embedder,
        calibration=_calibration_from_config(settings),
        min_confidence=settings.min_confidence,
    )


def _calibration_from_config(settings: ClassifierConfig) -> Calibration | None:
    spec = settings.calibration
    if spec is None:
        return None
    return Calibration(
        bins=tuple(
            CalibrationBin(upper=entry.upper, confidence=entry.confidence)
            for entry in spec.bins
        ),
        calibrated_on=spec.calibrated_on,
        model_id=spec.model_id,
        fitted_at=spec.fitted_at,
        expected_calibration_error=spec.expected_calibration_error,
    )


def unit_texts(
    nodes: Sequence[TaxonomyNode], *, exclude_unit_ids: Container[str] = ()
) -> tuple[UnitText, ...]:
    """Per-unit similarity texts from the taxonomy, in file order.

    Name plus responsibilities, nothing else. ``TaxonomyNode.source`` is
    deliberately left out: it is provenance about the CONFIG ("abgeleiteter
    Platzhalter bis zur Bestaetigung durch den Design-Partner"), it says nothing
    about the work the unit does, and every node's source ends in nearly the
    same sentence - which would pull every unit toward every other one.

    A node with no responsibilities produces no text and is silently skipped: a
    unit described only by its own name is a unit the classifier cannot honestly
    rank, and an empty string would score against everything.
    """
    texts: list[UnitText] = []
    for node in nodes:
        if node.unit_id in exclude_unit_ids or not node.responsibilities:
            continue
        body = ". ".join(part.strip() for part in node.responsibilities if part.strip())
        if not body:
            continue
        texts.append(UnitText(unit_id=node.unit_id, text=f"{node.name}. {body}"))
    return tuple(texts)


def render_item_text(
    context: Mapping[str, object], *, max_chars: int = MAX_ITEM_CHARS
) -> str:
    """What the classifier reads for one item; empty string when there is none.

    Two shapes arrive in this system and they are rendered differently on
    purpose:

    * **A letter** has ``text.normalized``: the normalized, already-redacted
      prose. That IS the item text, truncated deterministically.
    * **A form** has no prose at all. Its key facts are rendered from the
      ``payload.*`` namespace as ``path: value``, sorted by path. The path is
      half the signal - ``antrag.rentenart: regelaltersrente`` says more to an
      embedding than ``regelaltersrente`` alone - and sorting makes the
      rendering independent of key order in the submission JSON.

    **No placeholder ever reaches the embedder.** A sealed value is a random
    token drawn per case; embedding it makes the suggestion depend on the draw.
    A key fact whose value carries one is dropped, and a placeholder in prose is
    masked out - which is not cosmetic: the first real-model run of this module
    disagreed with itself by one item across two runs of the same corpus,
    because two Referate scored within 0.0003 of each other and a different
    random token tipped the order.
    """
    prose = context.get(f"{TEXT_PREFIX}normalized")
    if isinstance(prose, str) and prose.strip():
        return _truncate(" ".join(mask_placeholders(prose).split()), max_chars)
    facts = [
        f"{key[len(PAYLOAD_PREFIX) :]}: {value.strip()}"
        for key, value in sorted(context.items())
        if key.startswith(PAYLOAD_PREFIX)
        and isinstance(value, str)
        and value.strip()
        and PLACEHOLDER_MARK not in value
    ]
    return _truncate(FACT_SEPARATOR.join(facts), max_chars)


def cosine(left: Vector, right: Vector) -> float:
    """Cosine similarity of two vectors; 0.0 when either has no length."""
    if len(left) != len(right):
        raise ValueError("cosine needs two vectors of the same dimension")
    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    norm = _norm(left) * _norm(right)
    if norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / norm))


def normalize(vector: Vector) -> Vector:
    """The unit vector, or the zero vector unchanged."""
    norm = _norm(vector)
    if norm == 0.0:
        return vector
    return tuple(value / norm for value in vector)


def _norm(vector: Vector) -> float:
    return math.sqrt(math.fsum(value * value for value in vector))


def _truncate(text: str, max_chars: int) -> str:
    """Cut at the last word boundary at or before ``max_chars``."""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    cut = head.rfind(" ")
    return head[:cut] if cut > 0 else head
