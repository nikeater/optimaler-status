# ADR-028: Live Extraction Is a Local Posture, and the Measurement That Made It One

**Status:** Accepted, 2026-08-13 (part 12; closes backlog row P-16)

## Context

ADR-020 built two readers of prose and ruled that live-model numbers are
measured and never gated. Part 05 could only honour half of that: the harness
shipped tested against a scripted transport, and no model endpoint existed on
the workstation, so the benchmark P-16 asks for was never run and the caveat
"no local benchmark" travelled in every report.

Part 12 installed Ollama on the build workstation (RTX 5070, 12 GB), pulled two
open-weights 7B instruct models at Q4, and ran them against the 24 free-text
letters of gold v4. What came back decides two things at once: whether live
extraction is a mode this project can offer, and what the switch that offers it
is allowed to be.

**The measurement.** Both models are reachable, fast and well-behaved. Both
produce schema-valid JSON on the first attempt, in German, with the right field
names and - very often - the right values. Both fail the double lock almost
completely:

| measure | mistral 7b-instruct-v0.3 Q4_K_M | qwen2.5 7b-instruct Q4_K_M | replay |
|---|---|---|---|
| spans proposed | 86 | 85 | 88 |
| spans verified | 0 | 3 | 88 |
| acceptance rate | 0.000 | 0.035 | 1.000 |
| field recall | 0.000 | 0.020 | 1.000 |
| seconds per item | 4.07 | 4.40 | 0.012 |

The failure is concentrated in one place: `quote_mismatch`, 72 of 86 and 76 of
85. The prompt hands the model the redacted text in numbered chunks with their
start offsets and asks it to add the position inside the chunk; the models add
it wrong, by amounts between -34 and +18 characters, item by item. A second,
smaller share quotes the chunk marker itself (`[288] Geburtsdatum: ...`), and a
third arises where a 96-character chunk boundary falls inside a redaction
placeholder and the model reassembles the two halves with a space.

**The control that settles it.** Every letter was run a third way: fixture
removed and NO extractor at all. The tier that produced was identical to the
live tier on **24 of 24 letters**. On this corpus, a 7B model at Q4 through this
prompt is indistinguishable from no extractor at all - not worse, not better,
the same. Nothing it said reached a decision, because nothing it said survived
the lock.

## Decision

### 1. Replay stays the shipped extractor. Live is a local posture, not a mode.

`EINGANGSLOTSE_EXTRACTOR=replay|live` selects the reader of prose once, at app
startup, with `EINGANGSLOTSE_EXTRACTOR_URL` and `EINGANGSLOTSE_EXTRACTOR_MODEL`
overriding the endpoint without editing a frozen-versioned config file. The
default is replay: in the gate, in CI, in the container image and on the hosted
demonstration, none of which has a model endpoint or asks for one.

The switch also honours `live.enabled` in the extraction config, which has
documented itself as the way to turn a model on for the running service since
part 05 while nothing read it. An explicit `EINGANGSLOTSE_EXTRACTOR=replay`
beats it, so an operator can turn a configured model off without a config
change.

### 2. The failure taxonomy splits at startup, and only there.

Two things are startup errors, both because the alternative is a service that
looks configured and silently degrades every item:

* an unrecognized posture value (`Live`, `1`, `ollama`) - the operator meant
  something and a service that guessed would be running a posture nobody chose;
* `live` with no resolvable endpoint - an extractor that can never answer would
  push every item toward tier 3 while appearing to work.

**Everything after startup is a discard.** Nothing probes the endpoint at boot,
so a live-configured service starts with the model off; and an endpoint that is
down, times out, returns an HTTP error, answers non-JSON or answers prose
produces no proposals, which the pipeline already reads as a gap. The service
never returns an error to a caller because a model is missing. This is ADR-020's
rule reached through a switch rather than through an argument.

### 3. The double lock does not move, and neither does the prompt.

The obvious way to make these numbers better is to let the system find the quote
and treat the result as the offset. That is exactly the failure P-8 exists to
prevent and ADR-020 already rejected it; a measurement that shows the lock
biting is the lock working, not the lock misconfigured.

The three secondary causes above are prompt-shaped, and the prompt is frozen
behind `prompt_version` with numbers attached to it. They are recorded as
KE-5 rather than tuned inside a part whose ruling is "measure what the shipped
configuration does".

### 4. The verdict is written into the documentation, not only into a report.

`docs/BUILD.md`'s model section is now instructions from a machine where they
were executed, including how to stop the GPU consumer on a workstation that has
other uses. P-16 closes with measured numbers rather than a promise.

## Consequences

- **P-16 closes.** Two open-weights models from different lineages were
  benchmarked through one config-only swap (`--model a=... --model b=...`), the
  rows printed side by side. Swappability is demonstrated; the models are also
  demonstrated to be equally unusable here, which is the more useful half.
- **The recommendation is replay-only, including for the local showcase.** Live
  mode exists, works, is tested and is documented - but at 0 to 3 verified spans
  out of ~86 and 4.1 to 4.4 seconds per letter against 0.012, turning it on
  makes the demo 350x slower and strictly less informative. It is a laboratory
  instrument for the next model, not a demonstration setting.
- **The gate is untouched by construction, and it was checked.** The 77 form
  items carry no free text, so the live extractor is never called for them; the
  whole possible delta lives in the 24 letters. Substituting live evidence for
  those: routing accuracy 1.000 -> 1.000, **false clear 0.000 -> 0.000**, tier
  accuracy 1.000 -> 0.921 (8 items), spans 88/88 -> 0-3/85-88. The metric with a
  permanent budget of zero does not move.
- **"Fewer values is safer" turns out to be false, and that is worth keeping.**
  Of the 8 items whose tier moved, 3 went 1 -> 2 (more oversight) and **5 went
  3 -> 2, toward LESS oversight**: their tier-3 verdict was being produced by a
  value the deterministic reader established, and without that value the item
  falls back to the generic incomplete path instead. A collapse in extraction is
  not automatically a collapse into caution.
- **Temperature 0 is not determinism.** Three runs of the same model over the
  same 24 letters proposed 88, 86 and 86 spans and verified 1, 1 and 0. The
  aggregate verdict was stable to three decimals; the underlying proposals were
  not. Any future comparison has to report the run, not a number.
- **No live-transport glue was needed.** Roughly 200 real requests across two
  models produced no timeout, no connection churn and no malformed body the
  client did not already handle. One cold-start response carried a null content
  and the existing second attempt absorbed it. The only harness change is the
  wall clock, which was a missing measurement rather than a defect.

## Alternatives considered

**Step down to a 3B model.** Rejected: the ruling reserved it for a failed GPU
path, and the GPU path did not fail. Both 7B models sit at 100% GPU with room to
spare, and a smaller model would be worse at the arithmetic that is the binding
constraint.

**Widen the OCR fuzzy threshold so more live spans pass.** Rejected for the
reason ADR-020 already gave: the threshold's job is to refuse, and tuning it
until a model looks good is the pressure the no-gate rule exists to remove. It
would also not help - the offsets are wrong by tens of characters, not by one.

**Make live mode the default for the local demo.** Rejected on the numbers
above: 350x slower for strictly less evidence.

**A new config file for the switch.** Rejected as unnecessary: three environment
variables carry a machine's address and a posture, which is deployment data, not
agency policy. Timeout, attempts and chunk size stay in the versioned extraction
config, because those are policy and belong with the prompt version they were
measured against.
