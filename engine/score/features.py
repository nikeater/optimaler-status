"""The identity-blind feature set: everything the scorer is allowed to see.

Three rules define this module, and all three are checked rather than promised
(``tests/test_score_feedback_guard.py``, backlog P-3):

1. **The input type is the contract.** :class:`ScoringInput` carries the
   REDACTED envelope, the span-verified extractions and the assembled evidence.
   It has no vault, no witness, no journal, no draft store, no prior
   AnomalyEvidence and no per-applicant anything - because no such store
   exists, and the type is where that stays true when one does. The FSV and
   toeslagenaffaire lesson written into ``schemas/anomaly.py`` is a property of
   this signature (ADR-016).
2. **Nothing sealed becomes a feature.** Part 04 seals identity-classed paths
   at ingest, so the working copy carries a random placeholder where a
   Versicherungsnummer used to be. A feature computed over that token would be
   a feature over noise (part-04 finding, and the bug part 06 actually hit).
   Every string on the way to a number or to a rendered reason therefore passes
   :func:`_guarded`, which masks with part 04's single masking definition and
   then REFUSES anything still shaped like a placeholder. Refusing rather than
   masking is deliberate: a masked value in a numeric feature is a feature over
   blanks, and a masked value in a caseworker's reason is a sentence with a hole
   in it. The refusal degrades the item to "no anomaly evidence", which is
   exactly what the decision plane did before this part existed.
3. **No clock.** The reference date is the envelope's arrival timestamp, which
   ingest fixed once from the submission itself. A feature that read the wall
   clock would make a frozen gold set start scoring differently on a Tuesday.

What the boundary costs, stated here rather than discovered later: two of gold
v4's nine labelled anomalies are structurally out of reach. ``ar-0042`` (an
applicant aged 118) needs the Geburtsdatum and ``ar-0044`` (Bereichsnummer 99)
needs the Versicherungsnummer, and both are sealed before this module runs.
They are not scorer material any more - they are deterministic checks over the
witness, next to the birthdate-in-VSNR cross-check that already exists. See
ADR-024 and the engineering log.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from engine.redact import contains_placeholder, mask_placeholders
from engine.redact.placeholders import PLACEHOLDER_SHAPED_RE
from schemas.envelope import Envelope
from schemas.evidence import EvidenceRecord
from schemas.extraction import ExtractionSet

#: The opening of the reserved placeholder syntax. Checked on its own because a
#: token truncated on the right matches neither ``PLACEHOLDER_RE`` nor
#: ``PLACEHOLDER_SHAPED_RE`` - the shape part 08's round-trip property caught
#: on its way into a letter.
PLACEHOLDER_OPENER = "[[PII"

#: Days in a mean year, for turning a date distance into a number a caseworker
#: reads without dividing. Deliberately not 365: a 13-year distance printed as
#: 13.0 and computed as 13.04 would invite somebody to reconcile the two.
DAYS_PER_YEAR = 365.25

#: Every feature this module implements, in the order the vector carries them.
#: Sorted, and asserted to be sorted below: the matrix must not depend on the
#: order a dict happened to be written in, or two machines would fit two
#: different models from the same corpus.
FEATURE_IDS: tuple[str, ...] = (
    "felder_belegt_anteil",
    "freitext_vorgang",
    "indizien_beschaeftigung_anteil",
    "indizien_erfasst_anteil",
    "leitdatum_abstand_jahre",
    "leitdatum_vorhanden",
    "ocr_vorgang",
    "umsatzanteil_hauptauftraggeber",
)

assert list(FEATURE_IDS) == sorted(FEATURE_IDS), "feature ids must be sorted"

#: The mirror image of the one-way valve, and the second structural rule of
#: this part. ADR-004 forbids anomaly evidence from entering a QUALIFYING
#: condition; this forbids a qualifying field from entering the feature vector.
#:
#: The reason is not tidiness. A downgrade can only ADD oversight, so a feature
#: that restates a qualifying field can only ever re-flag items the table has
#: already sent to review - it produces flags that are true by construction and
#: carry no information, while crowding out the signal the scorer exists for.
#: It also makes "the scorer flagged this" stop being independent of "the rules
#: were unhappy with this", which is precisely the independence a caseworker
#: reading both is entitled to assume.
#:
#: Measured rather than reasoned: the first fit of this part included all five
#: of these. Its top of the distribution was letters with nothing extracted and
#: items with no derivable procedure - every one of them already at tier 3 - and
#: it found none of the nine labelled anomalies. Recorded in ADR-024 and pinned
#: by ``tests/test_score_feedback_guard.py``.
QUALIFYING_FIELD_ECHOES: dict[str, str] = {
    "routing_konfidenz": "routing.confidence",
    "luecken_anzahl": "completeness.gap_count",
    "verdikt_nicht_pruefbar": "completeness.verdict",
    "extraktion_min_konfidenz": "extraction.min_confidence",
    "verworfene_extraktionen": "extraction.discarded_count",
}

#: The CONSISTENCY half of the feature set: features on which "far from usual"
#: is a statement about the CASE. These are the deliberate hand-offs the earlier
#: parts wrote down - the date distances the wide absolute calendar bounds
#: cannot catch, and the par. 7a Indizienbuendel that could not become a
#: checklist - and they get a second, explicit reading next to the forest (see
#: ``engine/score/model.py``).
#:
#: Everything NOT in here is context: whether an item is a letter, whether it
#: came off a scanner, whether the Indizien fields were filled in at all. Those
#: describe which POPULATION an item belongs to, not how far from it the item
#: sits, and treating a rare population as a deviation is how a scorer starts
#: flagging everybody who sends paper. They still reach the forest, which is
#: allowed to use them to know what normal looks like for this kind of item.
DEVIATION_FEATURES: tuple[str, ...] = (
    "indizien_beschaeftigung_anteil",
    "leitdatum_abstand_jahre",
    "umsatzanteil_hauptauftraggeber",
)

assert set(DEVIATION_FEATURES) <= set(FEATURE_IDS)


class FeatureGuardError(RuntimeError):
    """A value that may not enter the feature vector reached it.

    Raised rather than swallowed: a scorer that quietly computed over a
    placeholder would produce numbers nobody could tell from real ones. The
    caller degrades the item to no-anomaly-evidence and journals the reason.
    """


@dataclass(frozen=True)
class ScoringInput:
    """Everything the feature builder may look at. Nothing else exists here.

    This dataclass IS backlog P-3. Adding a field of type ``VaultStore``,
    ``Witness``, ``JournalStore``, ``DraftStore``, ``AnomalyEvidence`` or a
    sequence of journal events fails a normative property test - not because
    such a field would be wrong today, but because an earlier flag on the same
    applicant raising a later score is how a scoring system becomes
    self-confirming.
    """

    envelope: Envelope
    extractions: ExtractionSet
    evidence: EvidenceRecord
    procedure_id: str | None
    #: Field id -> payload path from the procedure's own ``field_map``. Used to
    #: measure how much of a procedure's schema the item actually filled.
    field_paths: Mapping[str, str]


@dataclass(frozen=True)
class Indiz:
    """One Indiz of the par. 7a SGB IV Gesamtabwaegung, as config states it."""

    path: str
    label: str
    beschaeftigung_values: tuple[str, ...]


@dataclass(frozen=True)
class FeatureSpec:
    """What the config says about one feature, for rendering and for checks."""

    feature_id: str
    label: str
    unit: str
    expected_note: str


@dataclass(frozen=True)
class FeaturePolicy:
    """The config-owned half of the feature set (``config/scoring/``).

    The engine owns which features EXIST; the agency owns which payload paths
    they read and how they are worded. Both halves are checked against each
    other at load time, so a renamed field cannot silently turn a feature into
    a constant.
    """

    feature_set_version: str
    leading_date_fields: Mapping[str, str]
    indizien: Sequence[Indiz]
    umsatz_path: str
    specs: Mapping[str, FeatureSpec]


@dataclass(frozen=True)
class Feature:
    """One computed feature: the number, and what it says in German."""

    feature_id: str
    value: float
    display: str
    available: bool


@dataclass(frozen=True)
class FeatureVector:
    """One item's features, in :data:`FEATURE_IDS` order."""

    feature_set_version: str
    features: tuple[Feature, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(feature.feature_id for feature in self.features)

    @property
    def values(self) -> list[float]:
        return [feature.value for feature in self.features]

    def display(self, feature_id: str) -> str:
        """What this item shows for a feature, already German and guarded."""
        for feature in self.features:
            if feature.feature_id == feature_id:
                return feature.display
        return "-"


def build_features(item: ScoringInput, policy: FeaturePolicy) -> FeatureVector:
    """Compute the whole vector for one item, in the fixed feature order.

    Raises:
        FeatureGuardError: a value that is or contains a placeholder reached a
            feature. Never silently ignored.
    """
    payload = _working_copy(item.envelope)
    builders = {
        "felder_belegt_anteil": lambda: _filled_share(item),
        "freitext_vorgang": lambda: _has_prose(item),
        "indizien_beschaeftigung_anteil": lambda: _indizien_share(payload, policy),
        "indizien_erfasst_anteil": lambda: _indizien_coverage(payload, policy),
        "leitdatum_abstand_jahre": lambda: _leading_date_distance(item, policy),
        "leitdatum_vorhanden": lambda: _leading_date_present(item, policy),
        "ocr_vorgang": lambda: _is_scan(item),
        "umsatzanteil_hauptauftraggeber": lambda: _umsatzanteil(payload, policy),
    }
    return FeatureVector(
        feature_set_version=policy.feature_set_version,
        features=tuple(builders[feature_id]() for feature_id in FEATURE_IDS),
    )


# ------------------------------------------------------------- the guard ---


def _guarded(text: str, *, where: str) -> str:
    """Part 04's masking definition, then a refusal if anything survived it.

    ``mask_placeholders`` is the single definition of "blank out a well-formed
    token" (part 05 introduced it after spaCy tagged one as a person). What it
    cannot blank is a malformed imitation, which is exactly what part 08's
    round-trip property found travelling into a letter - so the shaped pattern
    is checked as well, and so is a bare ``[[PII`` opener, which matched
    neither regex and is the shape that got through. All three outcomes are a
    refusal rather than a repair.
    """
    masked = mask_placeholders(text)
    if (
        contains_placeholder(text)
        or PLACEHOLDER_SHAPED_RE.search(masked)
        or PLACEHOLDER_OPENER in masked
    ):
        raise FeatureGuardError(
            f"a sealed value reached the feature vector at {where}; the scorer "
            f"produces no evidence rather than a number computed over a token"
        )
    return masked


def _working_copy(envelope: Envelope) -> Mapping[str, object]:
    """The redacted structured payload, or an empty mapping for a letter."""
    for part in envelope.parts:
        if part.structured_payload is not None:
            return part.structured_payload
    return {}


def _at(payload: Mapping[str, object], path: str) -> str | None:
    """The scalar at a dotted path of the working copy, guarded and stripped."""
    current: object = payload
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    if not isinstance(current, str):
        return None
    text = _guarded(current, where=f"payload.{path}").strip()
    return text or None


def _extracted(item: ScoringInput, field: str) -> str | None:
    """The verified extraction for a field id, guarded and stripped.

    Only for features that need the VALUE. Asking this about a sealed field is
    a configuration mistake and raises, which is the whole point of the guard.
    """
    for record in item.extractions.records:
        if record.field == field:
            text = _guarded(record.value, where=f"extraction.{field}").strip()
            return text or None
    return None


def _present(item: ScoringInput, field: str) -> bool:
    """Whether a field was extracted at all, without reading what it says.

    Presence survives sealing - part 04 pinned that as a property, because
    replacing "no answer" with a placeholder would change the meaning of every
    presence predicate in the system. So "did this person answer the question"
    is a legitimate identity-blind feature over a sealed field, and "what did
    they answer" is not. The two are different functions here rather than one
    function with a flag, so no caller can get the distinction wrong by
    accident.
    """
    return any(record.field == field for record in item.extractions.records)


def _iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _decimal(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").strip())
    except ValueError:
        return None


# ----------------------------------------------------------- the features ---


def _leading_field(item: ScoringInput, policy: FeaturePolicy) -> str | None:
    if item.procedure_id is None:
        return None
    return policy.leading_date_fields.get(item.procedure_id)


def _leading_date(item: ScoringInput, policy: FeaturePolicy) -> tuple[str, date] | None:
    """The procedure's leading date and the field it came from, or None."""
    field = _leading_field(item, policy)
    if field is None:
        return None
    raw = _extracted(item, field)
    if raw is None:
        return None
    parsed = _iso_date(raw)
    return None if parsed is None else (field, parsed)


def _leading_date_distance(item: ScoringInput, policy: FeaturePolicy) -> Feature:
    """Signed years between the item's arrival and its leading date.

    This is the deliberate part-03 hand-off, arriving: the calendar bounds in
    the procedure configs are absolute and wide because they catch the
    IMPOSSIBLE, and "possible but decades away from the day the application
    arrived" was left to the scorer on purpose. Signed, because both directions
    are real - a Rentenbeginn thirteen years out and one twenty-eight years
    back are different cases and the sign is what tells them apart.
    """
    found = _leading_date(item, policy)
    if found is None:
        return Feature(
            feature_id="leitdatum_abstand_jahre",
            value=0.0,
            display="kein Leitdatum im Vorgang",
            available=False,
        )
    field, value = found
    days = (value - item.envelope.created_at.date()).days
    years = days / DAYS_PER_YEAR
    return Feature(
        feature_id="leitdatum_abstand_jahre",
        value=round(years, 6),
        display=(
            f"{field} {value.isoformat()}, {days:+d} Tage "
            f"({years:+.1f} Jahre) zum Eingang"
        ),
        available=True,
    )


def _leading_date_present(item: ScoringInput, policy: FeaturePolicy) -> Feature:
    """Whether a leading date exists at all, so 0.0 is never read as 'today'."""
    found = _leading_date(item, policy) is not None
    return Feature(
        feature_id="leitdatum_vorhanden",
        value=1.0 if found else 0.0,
        display="Leitdatum vorhanden" if found else "kein Leitdatum",
        available=True,
    )


def _indizien_share(payload: Mapping[str, object], policy: FeaturePolicy) -> Feature:
    """Share of the recorded par. 7a Indizien that point at Beschaeftigung.

    The BUNDLE is the signal, not the single Indiz - which is the fachliche
    point of the Gesamtwuerdigung (par. 7a Abs. 2 S. 1 SGB IV, BSG 28.06.2022
    B 12 R 3/20 R) and the reason none of these is a completeness rule. No
    threshold is invented anywhere: the feature is a share of what the form
    actually states, and which value of which field points which way is
    written in ``config/scoring/`` where a Fachbereich can correct it.
    """
    present = [
        (indiz, value)
        for indiz in policy.indizien
        if (value := _at(payload, indiz.path)) is not None
    ]
    if not present:
        return Feature(
            feature_id="indizien_beschaeftigung_anteil",
            value=0.0,
            display="keine Indizien im Vorgang erfasst",
            available=False,
        )
    hits = [
        indiz.label
        for indiz, value in present
        if value.lower() in indiz.beschaeftigung_values
    ]
    return Feature(
        feature_id="indizien_beschaeftigung_anteil",
        value=round(len(hits) / len(present), 6),
        display=(
            f"{len(hits)} von {len(present)} erfassten Indizien sprechen fuer "
            f"Beschaeftigung" + (f" ({', '.join(hits)})" if hits else "")
        ),
        available=True,
    )


def _indizien_coverage(payload: Mapping[str, object], policy: FeaturePolicy) -> Feature:
    """How many of the Indizien the form filled in at all.

    Without it a zero share would mean two different things: "every Indiz
    points at Selbststaendigkeit" and "this is not a Statusfeststellung at
    all". A model cannot be expected to tell those apart from one column.
    """
    total = len(policy.indizien) or 1
    present = sum(
        1 for indiz in policy.indizien if _at(payload, indiz.path) is not None
    )
    return Feature(
        feature_id="indizien_erfasst_anteil",
        value=round(present / total, 6),
        display=f"{present} von {total} Indizienfeldern belegt",
        available=True,
    )


def _umsatzanteil(payload: Mapping[str, object], policy: FeaturePolicy) -> Feature:
    """The stated revenue share with the main client, as a fraction.

    A number the form asks for, reported as it stands. The Fuenf-Sechstel
    Faustregel of the Verwaltungspraxis is deliberately NOT encoded (part 03b):
    it is Verwaltungswissen this repository cannot cite, and an invented
    threshold would be worse than none. The scorer may find the value unusual
    relative to the reference population; it may not call it too high.
    """
    raw = _at(payload, policy.umsatz_path)
    value = None if raw is None else _decimal(raw)
    if value is None:
        return Feature(
            feature_id="umsatzanteil_hauptauftraggeber",
            value=0.0,
            display="kein Umsatzanteil angegeben",
            available=False,
        )
    share = max(0.0, min(1.0, value / 100.0))
    return Feature(
        feature_id="umsatzanteil_hauptauftraggeber",
        value=round(share, 6),
        display=f"{value:g} Prozent des Umsatzes mit dem Hauptauftraggeber",
        available=True,
    )


def _filled_share(item: ScoringInput) -> Feature:
    """Share of the procedure's mapped fields the item actually carries.

    Counts PRESENCE, never content, which is what lets it include the sealed
    fields: whether somebody wrote a Versicherungsnummer down is a fact about
    the form, and what it says is a fact about the person.
    """
    total = len(item.field_paths)
    if total == 0:
        return Feature(
            feature_id="felder_belegt_anteil",
            value=0.0,
            display="kein Verfahren, keine Feldliste",
            available=False,
        )
    present = sum(1 for field in item.field_paths if _present(item, field))
    return Feature(
        feature_id="felder_belegt_anteil",
        value=round(present / total, 6),
        display=f"{present} von {total} Verfahrensfeldern belegt",
        available=True,
    )


def _has_prose(item: ScoringInput) -> Feature:
    """Whether the item is a letter rather than a form.

    The item SHAPE is in the vector and the CHANNEL deliberately is not: on
    every configured intake path the channel is a function of the shape, and
    two names for one signal would weigh it twice. Both are dimensions of the
    bias section (P-2), which is where a systematic skew against people who
    send paper has to become visible.
    """
    prose = any(part.redacted_text is not None for part in item.envelope.parts)
    return Feature(
        feature_id="freitext_vorgang",
        value=1.0 if prose else 0.0,
        display="Anschreiben mit Freitext" if prose else "strukturiertes Formular",
        available=True,
    )


def _is_scan(item: ScoringInput) -> Feature:
    """Whether any part of the item came off a scanner."""
    scanned = any(
        part.redacted_text is not None and part.source_type.value == "ocr"
        for part in item.envelope.parts
    )
    return Feature(
        feature_id="ocr_vorgang",
        value=1.0 if scanned else 0.0,
        display="Scan (OCR)" if scanned else "kein Scan",
        available=True,
    )
