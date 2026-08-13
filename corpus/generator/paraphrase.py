"""Surface realism without touching ground truth.

Real inbound items differ in ways that carry no meaning: key order in the JSON,
stray whitespace, the format of a date nobody validates, and a free-text cover
note written by a human. A corpus whose items are all rendered from one template
would let the pipeline look better than it is, so every item goes through a
paraphrase pass.

The pass is constrained so it *cannot* change a label:

* it only ever writes to :data:`corpus.generator.render.DECORATIVE_PATHS`, which
  is disjoint from every path in ``FIELD_PATHS``;
* whitespace jitter is applied to values the mapper strips anyway;
* after every item, :func:`assert_labels_preserved` compares the mapper-visible
  values before and after, and the build aborts on any difference;
* the build then re-runs the whole pipeline over the written item and checks the
  declared labels again. Two independent checks, because "the paraphraser
  quietly changed a fact" is exactly the bug that would poison a gold set.

The LLM variant reuses all of it and swaps in a model-written cover note when
one is available and looks like a sentence; provenance is recorded per item.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from corpus.generator.llm import LlmClient
from corpus.generator.render import GeneratorError, mapped_values
from corpus.generator.spec import ScenarioSpec
from engine.extract import FIXTURE_KEY

TEMPLATE_DIR = Path(__file__).parent / "templates"

_MONTHS = (
    "Januar",
    "Februar",
    "Maerz",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

_WUNSCH = (
    "",
    "",
    "Eine Eingangsbestaetigung waere mir wichtig.",
    "Bitte antworten Sie schriftlich, nicht telefonisch.",
    "Ich bin ab kommender Woche im Urlaub.",
)

#: How often a mapper-visible string gets stray whitespace around it.
_PADDING_PROBABILITY = 0.3
_PADDINGS = (" ", "  ", " \t")


@dataclass(frozen=True)
class ParaphraseResult:
    """A paraphrased payload plus how it was produced."""

    payload: dict[str, Any]
    provenance: str


class Paraphraser(Protocol):
    """What the build step needs from a paraphrase strategy."""

    name: str

    def apply(
        self, spec: ScenarioSpec, payload: dict[str, Any], rng: random.Random
    ) -> ParaphraseResult:
        """Return a surface-varied copy of ``payload``."""
        ...


class NullParaphraser:
    """No surface variation at all; used by ``--paraphrase none``."""

    name = "none"

    def apply(
        self, spec: ScenarioSpec, payload: dict[str, Any], rng: random.Random
    ) -> ParaphraseResult:
        return ParaphraseResult(payload=copy.deepcopy(payload), provenance="none")


class DeterministicParaphraser:
    """Seeded surface variation: notes, formats, whitespace, key order."""

    name = "deterministic"

    def __init__(self, template_dir: Path = TEMPLATE_DIR) -> None:
        self._environment = Environment(
            loader=FileSystemLoader(template_dir),
            # Plain-text cover notes, never HTML: escaping would corrupt them.
            autoescape=False,
            undefined=StrictUndefined,
            keep_trailing_newline=False,
        )
        self._template_names = sorted(self._environment.list_templates(".j2"))
        if not self._template_names:
            raise GeneratorError(f"no cover-note templates found in {template_dir}")

    def note(self, spec: ScenarioSpec, rng: random.Random) -> str:
        """Render one free-text cover note for an item."""
        template = self._environment.get_template(rng.choice(self._template_names))
        rendered = template.render(wunsch=rng.choice(_WUNSCH), spec=spec)
        return " ".join(rendered.split())

    def apply(
        self, spec: ScenarioSpec, payload: dict[str, Any], rng: random.Random
    ) -> ParaphraseResult:
        if spec.letter is not None:
            # A letter item has no form to write a cover note on, and its `data`
            # object is empty by design (part 05): adding a structured
            # hinweistext would be a JSON field on an e-mail. Its surface
            # variation is the letter itself - sender, address, wording, and on
            # the scan channel the seeded reading mistakes - so the honest
            # provenance is "none" rather than a mode that did nothing.
            return ParaphraseResult(payload=copy.deepcopy(payload), provenance="none")
        varied = copy.deepcopy(payload)
        data = varied.setdefault("data", {})
        antrag = data.setdefault("antrag", {})
        antrag["hinweistext"] = self.note(spec, rng)
        data["meta"] = _meta_block(varied.get("submittedAt", ""), rng)
        varied["data"] = _shuffle_keys(_pad_strings(data, rng), rng)
        assert_labels_preserved(spec, payload, varied)
        return ParaphraseResult(payload=varied, provenance=self.name)


class LlmParaphraser:
    """Deterministic variation plus a model-written cover note when possible."""

    name = "llm"

    def __init__(self, client: LlmClient, fallback: DeterministicParaphraser) -> None:
        self._client = client
        self._fallback = fallback

    def apply(
        self, spec: ScenarioSpec, payload: dict[str, Any], rng: random.Random
    ) -> ParaphraseResult:
        base = self._fallback.apply(spec, payload, rng)
        if spec.letter is not None:
            return base
        seed_note = base.payload["data"]["antrag"]["hinweistext"]
        rewritten = self._client.rewrite_note(seed_note)
        if rewritten is None:
            # Unreachable, slow, refusing or garbling: the deterministic note
            # already in the payload stands, and provenance says so.
            return base
        base.payload["data"]["antrag"]["hinweistext"] = rewritten
        assert_labels_preserved(spec, payload, base.payload)
        return ParaphraseResult(payload=base.payload, provenance=self.name)


def assert_labels_preserved(
    spec: ScenarioSpec, before: dict[str, Any], after: dict[str, Any]
) -> None:
    """Abort the build if surface variation changed a mapper-visible value.

    Raises:
        GeneratorError: listing the fields that changed.
    """
    if before.get("bodyText") != after.get("bodyText"):
        raise GeneratorError(
            f"{spec.scenario_id}: paraphrase changed the letter text; the "
            f"extraction sidecar's offsets and quotes would no longer describe it"
        )
    if before.get(FIXTURE_KEY) != after.get(FIXTURE_KEY):
        raise GeneratorError(
            f"{spec.scenario_id}: paraphrase changed the extraction sidecar"
        )
    original = mapped_values(before)
    varied = mapped_values(after)
    if original == varied:
        return
    changed = sorted(
        set(original) ^ set(varied)
        | {
            field
            for field in set(original) & set(varied)
            if original[field] != varied[field]
        }
    )
    raise GeneratorError(
        f"{spec.scenario_id}: paraphrase changed mapper-visible values "
        f"{changed}; ground truth would be wrong"
    )


def _meta_block(submitted_at: str, rng: random.Random) -> dict[str, str]:
    """A decorative block: a date in a format nobody validates, plus a wish."""
    date_part = submitted_at.split("T")[0]
    block = {"eingangsdatum": _format_date(date_part, rng)}
    wunsch = rng.choice(_WUNSCH)
    if wunsch:
        block["bearbeitungswunsch"] = wunsch
    return block


def _format_date(iso_date: str, rng: random.Random) -> str:
    parts = iso_date.split("-")
    if len(parts) != 3:
        return iso_date
    year, month, day = parts
    style = rng.randrange(3)
    if style == 0:
        return iso_date
    if style == 1:
        return f"{day}.{month}.{year}"
    return f"{int(day)}. {_MONTHS[int(month) - 1]} {year}"


def _pad_strings(value: Any, rng: random.Random) -> Any:
    """Add stray whitespace to some strings; the mapper strips it back off."""
    if isinstance(value, dict):
        return {key: _pad_strings(item, rng) for key, item in value.items()}
    if isinstance(value, list):
        return [_pad_strings(item, rng) for item in value]
    if isinstance(value, str) and rng.random() < _PADDING_PROBABILITY:
        return f"{rng.choice(_PADDINGS)}{value}{rng.choice(_PADDINGS)}"
    return value


def _shuffle_keys(value: Any, rng: random.Random) -> Any:
    """Reorder mapping keys; the mapper reads paths, not positions."""
    if isinstance(value, dict):
        keys = list(value.keys())
        rng.shuffle(keys)
        return {key: _shuffle_keys(value[key], rng) for key in keys}
    if isinstance(value, list):
        return [_shuffle_keys(item, rng) for item in value]
    return value
