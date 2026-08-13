"""Scenario specs: the declared facts a corpus item is built from.

A spec says what the case *is* (procedure, facts) and what the ground truth for
it *is* (unit, tier, gaps). The renderer turns the same facts object into the
payload, so the labels are true by construction rather than by re-reading the
generated file. Nothing here re-derives a label from a payload; that would make
the corpus a mirror of the pipeline instead of a check on it.

These models are generator-local tooling, deliberately NOT in ``schemas/``: a
scenario spec never crosses a module boundary at runtime, it is an input to a
build step. If a later part needs specs at runtime, that is the moment to
promote them to a contract, with an ADR.

Divergences are declared, never discovered. A spec may say "today's rules route
this somewhere I consider wrong" via ``expected.known_divergence``; the build
then *requires* that divergence to occur, so a corpus item can neither hide a
regression nor rot into a silent lie once the classifier lands. Tier
divergences may never point at tier 1: a gold item that expects oversight and
gets cleared is the one error class this project does not accept, and it must
fail the build, not be documented into the corpus.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Paraphrase provenance recorded per item.
PARAPHRASE_KINDS = ("llm", "deterministic", "none")


class ScenarioKind(StrEnum):
    """The corpus shapes parts 02 and 03 are required to cover."""

    COMPLETE_CLEAR = "complete_clear"
    MISSING_FIELD = "missing_field"
    INVALID_FIELD = "invalid_field"
    AMBIGUOUS_CONFLICTING = "ambiguous_conflicting"
    UNKNOWN_PROCEDURE = "unknown_procedure"
    ANOMALOUS_RULE_PASSING = "anomalous_rule_passing"
    #: Part 03: the procedure is knowable from content, but the channel did
    #: not declare it. The shape that measures procedure derivation.
    HINT_MISSING = "hint_missing"


class SpecModel(BaseModel):
    """Generator-local base: unknown keys in a scenario file are hard errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LetterSpec(SpecModel):
    """This item arrives as PROSE: the facts are written into a letter.

    An item with this block has an EMPTY ``data`` object. That is the whole
    point of it: every ``payload.*`` derivation signal is silent, every routing
    rule that reads a payload path is silent, and the item can only be
    understood through the text path - normalize, derive from ``text.*``,
    extract, verify every span (ADR-019, ADR-020).

    ``subject`` and ``opening`` are per item rather than templated, because the
    thing being measured is whether an ordinary German Anschreiben is readable,
    and twenty items opening with the same sentence would measure one sentence.
    """

    subject: str = Field(min_length=8)
    opening: str = Field(min_length=20)
    closing: str = "Mit freundlichen Gruessen"
    with_sender: bool = Field(
        default=True,
        description="Write the Absender/Anschrift block. Its values are sealed "
        "at ingest, so it is how a letter item exercises span sealing",
    )
    ocr_noise: bool = Field(
        default=False,
        description="Apply seeded scanner mistakes outside identity values, "
        "labels and short values; only meaningful on the scan channel",
    )


class ExpectedGap(SpecModel):
    """One gap the item is expected to produce."""

    requirement_id: str
    status: Literal["missing", "invalid"] = "missing"


class ExpectedLabels(SpecModel):
    """Ground truth for one item, declared next to the facts it follows from."""

    unit_id: str | None = None
    tier: Literal[1, 2, 3]
    derivation_source: Literal["hint", "content", "none"] = Field(
        description="How the evidence plane must arrive at the procedure. "
        "Required, never inferred: 'the hint happens to be right' and 'the "
        "content says so' are different claims about the same item"
    )
    derived_procedure_id: str | None = Field(
        default=None,
        description="Procedure derivation must produce; defaults to the "
        "spec's procedure_id, and must stay unset when the source is 'none'",
    )
    gaps: list[ExpectedGap] = Field(default_factory=list)
    known_divergence: list[Literal["unit", "tier"]] = Field(
        default_factory=list,
        description="Label fields today's rules are expected to get wrong",
    )
    divergence_reason: str | None = None

    @model_validator(mode="after")
    def _derivation_is_coherent(self) -> ExpectedLabels:
        if self.derivation_source == "none" and self.derived_procedure_id is not None:
            raise ValueError(
                "derivation_source 'none' cannot name a derived_procedure_id: "
                "'we could not tell' and 'it is X' are different outcomes"
            )
        return self

    @model_validator(mode="after")
    def _divergence_is_explained(self) -> ExpectedLabels:
        if self.known_divergence and not self.divergence_reason:
            raise ValueError(
                "known_divergence needs a divergence_reason: an expected "
                "mismatch without a reason is indistinguishable from a bug"
            )
        if self.divergence_reason and not self.known_divergence:
            raise ValueError(
                "divergence_reason without known_divergence: say which label "
                "field diverges"
            )
        if "tier" in self.known_divergence and self.tier == 1:
            raise ValueError(
                "a tier divergence on a tier-1 item would license a false "
                "clear; not permitted"
            )
        if len(set(self.known_divergence)) != len(self.known_divergence):
            raise ValueError("duplicate entry in known_divergence")
        return self


class ScenarioSpec(SpecModel):
    """One corpus item, declared as facts plus ground truth."""

    scenario_id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)
    kind: ScenarioKind
    description: str = Field(min_length=10)
    procedure_id: str | None = Field(
        description="Fachverfahren the item really belongs to; None when the "
        "item is not one of the configured procedures"
    )
    procedure_hint: str | None = Field(
        description="What the channel claims, written to the payload; may "
        "disagree with procedure_id on purpose"
    )
    channel: Literal["fit_connect", "email", "scan"] = "fit_connect"
    facts: dict[str, str] = Field(
        default_factory=dict,
        description="Field id -> literal value; a missing key means the field "
        "is absent from the payload",
    )
    letter: LetterSpec | None = Field(
        default=None,
        description="Present when the item arrives as free text rather than as "
        "a form; the facts above are written into the letter and `data` stays "
        "empty",
    )
    expected: ExpectedLabels
    anomaly_expected: bool = False
    anomaly_pattern: str | None = Field(
        default=None,
        description="What is statistically odd about this item; read by the "
        "shadow scorer work in part 06 and the review UI in part 09",
    )
    notes: str | None = None

    @property
    def expected_procedure_id(self) -> str | None:
        """The procedure derivation must produce for this item.

        The spec's ``procedure_id`` says which Fachverfahren the case belongs
        to; this says what the engine is expected to be able to *tell*. They
        differ exactly when an item is deliberately unreadable (a contradiction
        between channel and form), and that difference is the metric.
        """
        if self.expected.derivation_source == "none":
            return None
        return self.expected.derived_procedure_id or self.procedure_id

    @model_validator(mode="after")
    def _derivation_is_declarable(self) -> ScenarioSpec:
        if (
            self.expected.derivation_source != "none"
            and self.expected_procedure_id is None
        ):
            raise ValueError(
                f"{self.scenario_id}: derivation_source "
                f"{self.expected.derivation_source!r} needs a procedure; set "
                f"procedure_id or expected.derived_procedure_id"
            )
        if self.expected.derivation_source == "hint" and (
            self.procedure_hint is None
            or self.procedure_hint != self.expected_procedure_id
        ):
            raise ValueError(
                f"{self.scenario_id}: derivation_source 'hint' requires a "
                f"procedure_hint equal to the derived procedure "
                f"({self.procedure_hint!r} vs {self.expected_procedure_id!r})"
            )
        return self

    @model_validator(mode="after")
    def _anomalies_are_documented(self) -> ScenarioSpec:
        if self.anomaly_expected and not self.anomaly_pattern:
            raise ValueError(
                f"{self.scenario_id}: anomaly_expected needs an anomaly_pattern "
                "describing what is odd about the item"
            )
        if self.anomaly_pattern and not self.anomaly_expected:
            raise ValueError(
                f"{self.scenario_id}: anomaly_pattern set but anomaly_expected is False"
            )
        if (
            self.kind is ScenarioKind.ANOMALOUS_RULE_PASSING
            and not self.anomaly_expected
        ):
            raise ValueError(
                f"{self.scenario_id}: kind anomalous_rule_passing requires "
                "anomaly_expected: true"
            )
        if (
            self.kind is ScenarioKind.UNKNOWN_PROCEDURE
            and self.procedure_id is not None
        ):
            raise ValueError(
                f"{self.scenario_id}: kind unknown_procedure must not name a "
                "procedure_id"
            )
        if self.kind is ScenarioKind.HINT_MISSING and self.procedure_hint is not None:
            raise ValueError(
                f"{self.scenario_id}: kind hint_missing describes an item whose "
                "channel declared nothing; procedure_hint must be null"
            )
        return self

    @model_validator(mode="after")
    def _letters_arrive_through_a_text_channel(self) -> ScenarioSpec:
        if self.letter is None:
            return self
        if self.channel not in ("email", "scan"):
            raise ValueError(
                f"{self.scenario_id}: a letter arrives by e-mail or as a scan; "
                f"channel {self.channel!r} would give it the wrong source type "
                f"and therefore the wrong span-match mode"
            )
        if self.letter.ocr_noise and self.channel != "scan":
            raise ValueError(
                f"{self.scenario_id}: ocr_noise on the {self.channel!r} channel "
                f"would corrupt text that was never scanned"
            )
        return self


class ScenarioFile(SpecModel):
    """One file under ``corpus/generator/scenarios/``."""

    description: str
    scenarios: list[ScenarioSpec] = Field(min_length=1)


def parse_scenario_file(document: Any, *, source: str) -> list[ScenarioSpec]:
    """Validate one scenario document and return its specs.

    Args:
        document: parsed YAML mapping.
        source: file name, used in error messages.

    Raises:
        ValueError: if the document is not a valid scenario file.
    """
    if not isinstance(document, dict):
        raise ValueError(f"{source}: scenario file must contain a YAML mapping")
    try:
        return list(ScenarioFile.model_validate(document).scenarios)
    except Exception as error:  # re-raised with the file name attached
        raise ValueError(f"{source}: {error}") from error
