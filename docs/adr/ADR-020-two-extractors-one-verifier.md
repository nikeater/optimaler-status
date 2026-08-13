# ADR-020: Two Extractors, One Verifier, and the Double Lock

**Status:** Accepted, 2026-08-12 (part 05, plan step S5; closes backlog row P-8)

## Context

The plan's step S5 is the first place a language model is allowed anywhere near a
decision. ADR-001 drew the line: the evidence plane may be probabilistic, the
decision plane may not. That line only holds if a value a model produced is
distinguishable from a value the system checked, and if the checking is real.

Backlog row P-8 (from the prior-art pass) states the mechanism as "double-lock
grounding: verbatim quote AND offset, verified independently". Two further
constraints come from the shape of the project:

* The gate has to run on a laptop with no model on it. A verification machinery
  exercised only through an LLM is a machinery whose value depends on a wheel, a
  GPU and a sampling temperature.
* A free-text Anschreiben has no structured payload, so until part 05 it had no
  derivable procedure at all: every `payload.*` signal is silent, and the answer
  was "tier 3" by construction rather than by judgement.

## Decision

### 1. Two extractors, one verifier, and the verifier cannot tell them apart.

Both readers of prose emit the same `Proposal` - field, value, verbatim quote,
part id, offset, extractor id - and `engine.extract.verify` accepts a proposal
without knowing which produced it.

**The REPLAY extractor** derives proposals from a sidecar the corpus generator
writes next to every letter: which field it wrote, behind which label, in what
wording. It exists so the entire verification machinery, the merge and the
discard accounting run deterministically over every gold item, on any machine.
It is not an extractor for real post and never becomes one - it locates values
by labels it wrote itself.

**The LIVE client** speaks OpenAI-compatible chat completions with JSON-Schema
constrained decoding (Ollama's `/v1` spelling), on stdlib `urllib` with an
injectable transport, copied from ADR-012's paraphrase client for the same
reasons: no probe unless an endpoint is explicitly configured, a loud error when
one was explicitly requested and is unreachable, and **every** failure mode -
timeout, HTTP error, non-JSON, schema mismatch after N attempts, a refusal, a
wall of prose - degrading to "no proposals", which the pipeline already treats
as a gap that pushes toward tier 3.

If the verifier could tell them apart, the temptation to trust one source more
than the other would arrive with it, and "we trusted it because we wrote it" is
not a verification.

### 2. The double lock, checked independently, and disagreement is a discard.

A proposal carries a quote AND an offset, and the two are checked separately:

* **Lock one - does the text at that offset say that?** `normalized[offset :
  offset + len(quote)]` is compared with the quote. Born-digital text is compared
  exactly; OCR text is compared with a bounded fuzzy ratio above a configured
  per-source-type threshold. **The offset is never adjusted.** Searching the
  neighbourhood for a better window would turn a wrong offset into a right one,
  collapse two locks into one, and reward "the model was nearly right".
* **Lock two - does that quote contain that value?** The value must occur in the
  quote up to whitespace and case. A quote that does not contain its own value is
  an extractor summarizing, and a summary is not a span.

Disagreement is a DISCARD, never a repair. A discarded proposal increments
`ExtractionSet.discarded_count`, the same lever the schema mapper pulls when a
payload path is missing, which the decision table already reads as pressure
toward tier 3. Nothing on this path can produce a value; the worst it can do is
produce fewer of them, and fewer values means more oversight.

The match score feeds `ExtractionRecord.confidence`. An EXACT record carries the
configured exact confidence and no `match_score` (it is 1.0 by definition and
saying so twice invites the two numbers to disagree); a FUZZY record carries its
score, floored by config.

**Precedence:** a field the deterministic schema mapper filled is never
overwritten by a text proposal. Reading a JSON key is not an inference and prose
is. The losing proposal is recorded as a `duplicate_field` discard rather than
dropped silently, so the disagreement shows up in the failure histogram.

### 3. The `text.*` namespace, and two engine-level operators.

`build_payload_context` gains `text.normalized` - every free-text part of the
item, normalized, already redacted, joined by a space - and `text.source_types`.
The merged view is deliberate: a Rentenart named in the mail body and one named
in the scanned annex are the same fact about the same case, and a per-part
namespace would force every config rule to enumerate parts whose number it
cannot know. Spans stay strictly per part, because a span that did not name its
part could not be translated back.

Two operators join the predicate vocabulary, `contains` and `matches`, both
case-insensitive (German capitalizes nouns wherever they fall). They live in
`engine.predicate` rather than in `schemas.config.Op`: `RoutingRule.predicate` is
an opaque mapping in the contract, so a config vocabulary can grow without a
contract change. `matches` patterns are compiled at LOAD time, so a broken
regular expression is a startup error rather than a rule that silently never
fires.

Each procedure's derivation block gains the SAME signature it already declared
for its payload, over text. The house rule from ADR-013 does not move: signals
from two procedures mean no procedure, no completeness check and tier 3.

A **config lint** extends part 04's presence-only rule to this namespace and
refuses three things: a text rule whose literal looks like identity data to the
same recognizers that decide what to seal (a Versicherungsnummer in a config file
is in git, in every report, and on a person); a text rule on a field the
namespace does not have; and an ordinary comparison operator on a whole letter.
"Does this item have text at all" stays a legal presence test.

### 4. Live-model numbers are measured and never gated.

`python -m eval.live` runs the configured endpoints against the corpus with the
sidecar REMOVED and reports extraction agreement against the corpus's declared
facts. Pointed at two or more endpoints it is the P-16 sovereignty harness: a
config-only swap, with the rows printed next to each other. It never gates. A
metric that moved because a model was warm, or because somebody pulled a newer
tag of the same model name, is not a metric.

## Consequences

- **P-8 closes.** The grounding mechanism exists, is exercised on every gold
  item without a model, and is pinned by properties: an accepted record's span
  always slices out text its quote matches, and a shifted offset is never
  accepted under an exact policy.
- **P-12's engine half exists**: verification statistics ride in the `EXTRACTED`
  journal payload (counts, per-part, failure histogram - never a value, never a
  quote, not even a rejected one) and in an eval section split by source type.
- The corpus's `literal` sidecar entries quote the LABEL plus the value and
  their offset is the label's. A quote that WAS the value would satisfy lock two
  by construction and leave every corpus item testing only one lock.
- A `sealed` sidecar entry necessarily builds its quote from the text it is
  verified against, so it exercises the placeholder path rather than the double
  lock. The double lock is what every `literal` entry and every unit test
  exercises.
- **The prompt is built from requirement wording**, so a field no procedure
  declares as a requirement is never asked of a model. `auslandsbezug` is such a
  field and the Altersrente clear-cut criteria read it, so a live-only run
  cannot reach tier 1 on its own. Recorded in `docs/KNOWN-ERRORS.md`; the
  alternative would be a second definition of what a field means, written for a
  model, which would drift on the first fachliche correction nobody copied over.
- No schema change was needed. `MatchMode.EXACT/FUZZY`, `ExtractionRecord.span`
  and `match_score`, and `EventType.EXTRACTED` cover all of it.

## Alternatives considered

**Quote only, no offset.** Rejected: a model that invents both invents them
consistently, and a system that then searched for the quote would be
manufacturing the offset it claims to verify. That is precisely the failure P-8
names.

**Repair a near-miss offset by searching a window around it.** Rejected for the
same reason: it converts a wrong answer into a right one and reports the result
as verified.

**Put `contains`/`matches` on `schemas.config.Op`.** Rejected: contracts are
ADR-gated and a config vocabulary that grows with the rules does not
need to be one. If a later part needs these operators across a module boundary,
that is the moment to promote them, with an ADR.

**Gate on the verification rate.** Rejected: it would create pressure to lower
the match threshold until the number looked good, which is the opposite of what
the threshold is for. A collapse in extraction already shows up in the gated
numbers as caution, because every discarded span pushes its item toward tier 3.
