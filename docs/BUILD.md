# Build & Run

Skeleton; each part fills in its sections as it lands.

## Prerequisites
- Python 3.12+
- Docker Desktop (for PostgreSQL 16 + pgvector; compose profiles in `eingangslotse/deploy/`)
- Ollama (dev LLM serving; model pinned in config) - required from part 05 onward
- Git

## Quick start (dev)
```powershell
cd eingangslotse
py -3.13 -m venv .venv          # any native Windows CPython 3.12+ works
.venv\Scripts\Activate.ps1
pip install -e .[dev]           # core + dev tooling; every gate passes on this
pip install -e ".[dev,redact]"  # optional: adds the NER member of the redaction
                                # detector union (see "Redaction extra" below)
```
The venv committed to nobody's repo but expected by every command below lives at
`eingangslotse/.venv`; it currently holds CPython 3.13.14.

Note for this workstation: the `python` on PATH is the MSYS2/UCRT64 build, which
creates a POSIX-layout venv (`.venv/bin/`) and does not produce
`.venv\Scripts\Activate.ps1`. Use the `py` launcher as shown above, or call the
interpreter directly (`.\.venv\Scripts\python.exe -m pytest`) instead of
activating.

## Database (from part 02+)
```powershell
docker compose -f deploy/compose.dev.yaml up -d   # PostgreSQL 16 + pgvector
```
Not yet present. Part 01 runs against the journal backends described in
ADR-008: `InMemoryJournalStore` (default) and `JsonlJournalStore` (file-backed,
selected by setting `EINGANGSLOTSE_JOURNAL_DIR`). PostgreSQL replaces both.

## Tests, lint, types
```powershell
pytest                            # coverage floor: 95% over engine/decide + engine/evidence
ruff format --check . ; ruff check .
mypy                              # strict on engine/decide, standard elsewhere
```
`pytest` runs the two non-negotiable gates of this stage: the Hypothesis
one-way-valve monotonicity properties (`tests/test_decide_properties.py`) and
the decision-table golden files (`tests/golden/`).

The coverage floor is enforced over the combined report for `engine/decide`,
`engine/evidence`, `engine/redact`, `engine/textlayer`, `engine/extract` and
`engine/notify` (part 03 added the second, part 04 the third, part 05 the next
two, part 07 the last). `engine/decide` stands at 100%, `engine/evidence` at 99%,
`engine/redact` at 97% and `engine/notify` at 98%; the floor cannot express a
per-package minimum, so the per-module column in the `term-missing` output is
what to read when one of them slips. `engine/notify` is on the list for the
reason `engine/redact` is: it carries the "no identity data reaches a citizen"
invariant, and a branch nobody exercises there is a guard nobody knows still
works.

## Corpus generator (from part 02, versioned sets from part 03)
```powershell
python -m corpus.generator.build --out corpus/gold/v4 --seed 42   # rebuild
python -m corpus.generator.build --out corpus/gold/v4 --check     # verify only
python -m corpus.generator.build --out corpus/gold/v3 --check     # integrity only
```
Scenario specs in `eingangslotse/corpus/generator/scenarios/*.yaml` declare the
facts of a case and its ground truth; the renderer builds payload and label
sidecar from the same facts object, so labels are true by construction. After
rendering, the build runs the real pipeline over every item and refuses to write
anything if an outcome disagrees with the declared labels (`--check` compares
without writing, exit code 1 on a difference, 2 on a build error).

Output is a pure function of (specs, seed, generator version, paraphrase
strategy), so a rebuild over the committed corpus leaves the tree clean.

Paraphrase pass, controlled by `--paraphrase {auto,deterministic,llm,none}`:
- `auto` (default) uses an OpenAI-compatible endpoint **only when one is
  configured** via `--llm-base-url` / `EINGANGSLOTSE_LLM_BASE_URL` (model:
  `--llm-model` / `EINGANGSLOTSE_LLM_MODEL`), and falls back to the
  deterministic pass when it is unreachable. Without a configured endpoint no
  probe happens at all, so a locally running model cannot silently change the
  corpus.
- `llm` fails instead of falling back; `deterministic` and `none` force the
  strategy. Provenance is recorded per item (`paraphrase:` in each sidecar,
  aggregated in `MANIFEST.yaml` and the eval report).

Gold sets are FROZEN (ADR-010): never edited by hand, never trained on,
superseded by a new versioned directory rather than corrected in place.
`corpus/gold/REGISTRY.yaml` says which set is current and how each one is
verified (ADR-015):

| Set | Status | `--check` does |
|---|---|---|
| `corpus/gold/v4` | current, 101 items (77 forms + 24 letters) | rebuild from the specs and compare byte for byte, including the self-check against the live engine |
| `corpus/gold/v3` | superseded, 77 items | integrity only: SHA-256 of every item against the set's own MANIFEST |
| `corpus/gold/v2` | superseded, 57 items | integrity only |
| `corpus/gold/v1` | superseded, 41 items | integrity only |
| `corpus/gold/s1` | superseded, 2 items | nothing; part-01 scaffolding without a manifest |

Re-running today's engine over a superseded set is what `python -m eval.run
--gold corpus/gold/v3` is for. Those numbers are *meant* to move; that is why
the set was superseded. (v3 happens NOT to move, because all 77 of its items are
byte-identical inside v4 and the v4 eval gates on exactly that.)

**Letter items (from part 05).** An item with a `letter:` block in its scenario
spec is rendered as German administrative prose into `bodyText` with an empty
`data` object, plus an `extractionFixture` sidecar telling the replay extractor
which label each value stands behind. `ocr_noise: true` adds seeded scanner
mistakes outside identity values, labels and short values. The build refuses to
write a letter item unless the working copy verified clean under the
DETERMINISTIC union alone, something was actually sealed out of the letter, and
every declared span passed the double lock.

## Eval harness (from part 01, extended in parts 02 and 03)
```powershell
python -m eval.run                                        # defaults below
python -m eval.run --gold corpus/gold/v4 --report eval/reports/latest.json
python -m eval.run --gold corpus/gold/v3                  # a superseded set
```
The default gold set is `corpus/gold/v4` (part 05). It carries three
procedures - altersrente, erwerbsminderungsrente and statusfeststellung - so the
per-procedure breakdown has three rows plus the `unknown` bucket, and 24 of its
101 items arrive as free text. Exit code 1 when any of the three gates fails.
`eval/reports/` is gitignored; the report is a build artifact.

Four gates, reported separately because they fail for unrelated reasons:

| Gate | Fails when |
|---|---|
| false clear | an item gold says needs oversight was cleared to tier 1 |
| redaction recall | a labelled identifier in the seeded PII golden set was not found |
| structured subset | an item with NO free text in it scored differently than it did before the text path existed |
| anomaly reasons | the shadow scorer flagged an item without a feature-level reason a caseworker can read (part 09) |

The third one is the regression identity of part 05: the items without prose are
the previous gold set, byte-identical, so they have to score exactly routing
1.000, tier 1.000, false clear 0.000, false flag 0.000, derivation 1.000. The
subset is computed from the envelope (an item that produced no text part), not
from item ids.

Metrics: routing accuracy, tier accuracy, false-clear rate (gate: zero),
false-flag rate, gap exact-match rate, item count (part 01); completeness
precision/recall/F1 over (item, requirement) pairs, a per-procedure breakdown,
the anomalous-subset tier agreement reported separately, and paraphrase
provenance counts (part 02); procedure-derivation accuracy with a per-source
breakdown and a confusion table (part 03); span verification - verified rate,
discard rate and failure histogram, split by source type and by procedure - plus
the structured-subset section (part 05); the threshold review and the classifier
summary (part 06, both below). Still to come with the module that produces it:
the anomaly-downgrade rate against the efficiency budget (part 09).

Two sections arrived in part 06 and neither is gated. **Threshold review**
(backlog P-5) lists every number that governs which items a human never sees -
both span-match minimums, the routing-confidence bound, the anomaly threshold,
the downgrade budget, the classifier minimum - with the file and version that
owns it, its provenance, whether it is a measurement or an uncalibrated
placeholder, a measured operating point on this run and a deterministic
`+/-0.01 / +/-0.05` sweep. It restates no value; every one is read from the file
that owns it. The review date lives in `config/review/threshold_review_v1.yaml`
and the loader assembles it into `AgencyRiskConfig.review_due`; when it has
passed, the report and the panel print a NOTICE and the exit code does not
change. `--today 2027-01-15` drives that clock for a test or a demo.
**Classifier** is described under the `[classify]` extra below.

Span verification is **reported and never gated**: a gate on it would create
pressure to lower the match threshold until the number looked good, which is the
opposite of what the threshold is for (`docs/KNOWN-ERRORS.md`, KE-3).

Procedure-derivation accuracy counts an item as correct only when the engine
finds the right procedure **by the route the corpus declares** (`hint`,
`content` or `none`). Items without derivation ground truth (the part-01
sidecars) are skipped rather than scored as wrong, and their number is reported.

Reading the report: items whose outcome differs from gold are marked `DIFF`,
except those whose divergence the corpus declares and explains, which are marked
`DECL` (ADR-011). `DECL` items still count against the metrics. Gold v4 has
**none**, like v3 before it - v2's single divergence (`xx-0005`, no routing rule
for Widersprueche) was closed by the Widerspruchs-Regel in `routing_v3.yaml`.
The mechanism stays in place for the next one.

## API and metrics panel (from part 01 / part 02)
```powershell
uvicorn api.app:app --reload      # POST /ingest, GET /cases/{case_id}, GET /health
                                  # GET /metrics, GET /metrics/panel
                                  # GET /inbox, GET /inbox/{case_id}
                                  # GET /drafts/{case_id}?unit=<unit_id>
                                  # GET /review, /review/queue/{id}, /review/case/{id}
```
`GET /cases/{case_id}` returns the raw event list plus the derived state; that
JSON dump is the S1 "UI" and the data the review UI will render later.

`GET /metrics` shows the headline metrics including procedure-derivation
accuracy, the per-procedure breakdown, and the per-source derivation figures.
It renders the latest eval report as a plain HTML page (server-
rendered Jinja2 in `ui/templates/`, vendored htmx in `ui/static/vendor/`, works
with JavaScript disabled). It computes nothing itself: with no report on disk it
returns 200 and prints `python -m eval.run`. Override the report location with
`EINGANGSLOTSE_EVAL_REPORT`.

## Redaction: the [redact] extra and the recall metric (from part 04)

The privacy boundary (`engine/redact`, ADR-017/ADR-018) runs on deterministic
German recognizers and needs nothing beyond the core install. The optional extra
adds the model-backed member of the detector union - bare person names and
context-free place names, the entities no regular expression carries.

```powershell
pip install -e ".[dev,redact]"      # presidio-analyzer + spacy
python -m spacy download de_core_news_lg
```

**Every gate passes without the extra**, on the deterministic recognizers alone.
What changes when it is installed is the NAME kind: recall goes from 0.500 to
1.000 on the seeded golden set, and the NER-gated test stops skipping. Nothing
else in the system depends on it, `engine.redact` imports cleanly without it,
and `engine.redact.ner.available()` reports the truth either way.

Wheel situation on this workstation (2026-08-11): **available and installed
cleanly on CPython 3.13 / Windows** - `presidio-analyzer 2.2.364`,
`spacy 3.8.15`, `thinc 8.3.13`, `de_core_news_lg 3.8.0` (567 MB). Presidio
downloads the spaCy model itself the first time an engine is built, so the
explicit `spacy download` above is belt and braces rather than a prerequisite.
The extra targets the 3.12 deploy image; should a future wheel be missing for a
platform, the fallback is documented and tested: the deterministic union alone,
NER tests skip, the eval report says `NER not installed` and the deterministic
recall gate still has to be 1.000.

### The recall metric (backlog P-7)

```powershell
python -m corpus.pii_golden.build            # rebuild the seeded PII golden set
python -m corpus.pii_golden.build --check    # verify, write nothing
python -m eval.run                           # includes the `redaction` section
```

`corpus/pii_golden/` holds 81 labelled German administrative snippets (79
labelled spans, 12 hard negatives), generated from an explicit seed with no wall
clock, with its own README and MANIFEST. It is deliberately **outside**
`corpus/gold/REGISTRY.yaml`: it measures the redactor, not the triage.

Recall is measured by containment (a label counts as found only when a detection
of the same kind covers it entirely) and is **gated**: 1.000 on the ten
deterministic kinds always, and on NAME as well when the extra is installed.
Precision is **reported per kind and never gated** - over-redaction costs
utility, under-redaction costs a person's data, and a precision gate would push
in exactly the wrong direction. `python -m eval.run` exits 1 when the recall gate
fails, alongside the existing false-clear gate.

### Storage

```powershell
$env:EINGANGSLOTSE_VAULT_DIR = "var/vault"   # file-backed identity vault
$env:EINGANGSLOTSE_JOURNAL_DIR = "var/journal"
```
Both default to in-memory. `JsonlVaultStore` writes **plaintext JSON for
synthetic development data** and says so in every file it produces; the
production vault is PostgreSQL, encrypted at rest
(`docs/vault-dpia-input.md`).

## JSON Schema artifacts
```powershell
python -m schemas.export_json_schema
```

## The text path and the extraction model (from part 05)

Part 05 added the other half of the inbox: an Anschreiben by e-mail, a scanned
letter. Such an item carries `bodyText` (and/or attachments with extracted
`text`) on the same submission JSON the FIT-Connect adapter reads; the channel
decides the source type unless the item states one, and the source type decides
how spans are matched.

| channel | source type | span matching |
|---|---|---|
| `fit_connect`, `email` | `born_digital` | EXACT: the quote stands at the offset or it does not |
| `scan` | `ocr` | bounded fuzzy above `match.ocr.min_score` in `config/extraction/extraction_v1.yaml` |

A real IMAP/MIME adapter and a real FIT-Connect event-log client are part 07
(backlog P-14). What exists today is the envelope shape a text item produces.

### Replay vs live extraction

There are two readers of prose and one verifier that cannot tell them apart.

**Replay (default, deterministic, what the gate runs).** The corpus writes an
`extractionFixture` sidecar next to every generated letter, and the replay
extractor turns it into proposals. It exercises the whole verification
machinery - both locks, the merge, the discard accounting - on every gold item,
on any machine, with no model installed. Nothing needs configuring; it is what
`python -m eval.run` and `pytest` use.

**Live (opt-in, never gated).** An OpenAI-compatible endpoint with JSON-Schema
constrained decoding. Off by default: `live.enabled: false` and no `base_url` in
`config/extraction/extraction_v1.yaml`. There is **no probe** unless an endpoint
is explicitly configured, so a developer who happens to run a local model cannot
silently produce different evidence than the machine next to them. Every failure
mode - unreachable, timeout, HTTP error, non-JSON, a body that does not fit the
schema, a refusal - degrades to "no proposals", which the pipeline treats as a
gap that pushes toward tier 3.

### Ollama setup

Every command below was executed on the build workstation on 2026-08-13
(Windows 11 Pro 26200, i7-14700K, 32 GB RAM, RTX 5070 12 GB, driver 610.62 /
CUDA 13.3). Where something did not behave as expected, that is written down
too.

```powershell
winget install --id Ollama.Ollama --exact `
       --accept-source-agreements --accept-package-agreements
```

Installed **0.32.9**, which was the current release that day. Take the latest
build rather than whatever a cache offers: this GPU is Blackwell generation, and
older Ollama builds fall back to CPU on it **silently** - the numbers below would
then be measuring the wrong thing entirely.

The installer puts `ollama.exe` in `%LOCALAPPDATA%\Programs\Ollama` and starts
the tray app plus the server. It does not add itself to the current shell's
`PATH`; open a new shell, or prepend the directory.

```powershell
ollama --version                                     # ollama version is 0.32.9
Invoke-RestMethod http://localhost:11434/            # "Ollama is running"
ollama pull mistral:7b-instruct-v0.3-q4_K_M          # 4.4 GB
```

**Pin the tag, including the point version.** `mistral:7b-instruct` floats and
`mistral:latest` floats further; the extractor stamps `llm:<tag>` into every
record's provenance, so a floating tag makes the version stamp a half-truth.
Check what a tag currently resolves to on <https://ollama.com/library> rather
than assuming.

**Verify the model is actually on the GPU**, because a silent CPU fallback
invalidates every timing number:

```powershell
ollama ps          # PROCESSOR column must say "100% GPU"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

Observed here: `100% GPU`, 5.0 GB resident, 6235 MiB of 12227 MiB in use against
a 1284 MiB desktop baseline. First load plus a short generation took 56 s;
subsequent generations are 2-7 s. Both 7B models fit on the card at once
(10986 MiB of 12227).

### Turning live extraction on (LOCAL only)

The switch is environment-based, so no frozen-versioned config file is edited:

```powershell
$env:EINGANGSLOTSE_EXTRACTOR = "live"
$env:EINGANGSLOTSE_EXTRACTOR_URL = "http://localhost:11434"
$env:EINGANGSLOTSE_EXTRACTOR_MODEL = "mistral:7b-instruct-v0.3-q4_K_M"
python -m uvicorn api.app:app --factory   # or however you run it locally
```

`base_url` is the ROOT; the client appends `/v1/chat/completions` and
`/v1/models` itself. Unset the variable (or set it to `replay`) to go back;
`replay` set explicitly also overrides an `live.enabled: true` in
`config/extraction/extraction_v1.yaml`, which is the other way to select live
mode and which timeout, attempts and chunk size keep coming from either way.

Behaviour, all of it tested in `tests/test_extractor_switch.py`:

| posture | endpoint | result |
|---|---|---|
| unset (default) | - | replay; no extractor object, no probe, nothing observable changes |
| `replay` | - | replay, even if the config enables live |
| `live` | reachable | live proposals, still through the double lock |
| `live` | **down or slow** | no proposals; discards toward tier 3, journaled; the request still returns 201 |
| `live` | none configured | **startup error** - refuses to boot rather than degrade silently |
| `Live`, `1`, anything else | - | **startup error** naming the two legal values |

Nothing probes the endpoint at startup, so a live-configured service starts
fine with Ollama stopped. vLLM with guided decoding speaks the same
OpenAI-compatible shape for a pilot; the swap is the two variables above.

**This is a local laboratory posture, not a demo setting.** The default is
replay in the gate, in CI, in the container image and on the hosted
demonstration, and the measurements below are why.

### Ollama on a multipurpose PC

This is somebody's desktop as well as a build machine, so the GPU has to be
returnable on demand. All of this was verified here.

**Unload the model, keep the server.** The fastest way to get the VRAM back:

```powershell
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 6248 MiB
ollama stop mistral:7b-instruct-v0.3-q4_K_M
ollama ps                                                  # empty
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 1359 MiB
```

`ollama ps` listing nothing and `nvidia-smi` back at the desktop baseline are
the two confirmations; check both, because the first can empty while a runner
process still holds memory (see the warning below).

**Auto-unload.** A loaded model unloads itself after **5 minutes** idle by
default - immediately after a request `ollama ps` shows `UNTIL 4 minutes from
now`. Tune it with `OLLAMA_KEEP_ALIVE` (e.g. `$env:OLLAMA_KEEP_ALIVE = "30s"`
before starting the server, or `"-1"` to keep a model resident), or per request
with a `keep_alive` field - `keep_alive: 0` unloads as soon as that request
finishes, which was verified here and is the cleanest way to run one job and
give the card straight back.

**Stop the service entirely.** Quit "Ollama" from the system tray, or:

```powershell
ollama stop mistral:7b-instruct-v0.3-q4_K_M    # FIRST - see the warning
Get-Process ollama,"ollama app" -ErrorAction SilentlyContinue | Stop-Process -Force
```

> **Warning, reproduced here.** Killing `ollama` and `ollama app` while a model
> is loaded leaves an orphaned `llama-server.exe` holding the VRAM: processes
> gone, endpoint refusing connections, and `nvidia-smi` still at 6107 MiB.
> Either `ollama stop <model>` first, or clean up after:
> `Stop-Process -Name llama-server -Force`. That returned the card to 1382 MiB.

**Start it again.** `ollama serve` is the reliable way and answers within
seconds:

```powershell
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
Invoke-RestMethod http://localhost:11434/     # "Ollama is running"
```

Launching only the tray app (`ollama app.exe`) did **not** bring the API back up
within ~25 s in this test, so do not rely on it.

> **Stopping is not sticky.** Running *any* `ollama` CLI command afterwards
> starts the background server again - `ollama list` did it here, with no model
> loaded and no VRAM taken, but the process is back. If you want the machine
> genuinely free of it, stop it and then leave the CLI alone.

**Auto-start at login: yes, via the Startup folder.** The installer creates
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk`. There is
**no** `HKCU:\...\CurrentVersion\Run` entry and **no** Windows service (checked
both). Delete that shortcut, or turn "Ollama" off under Settings > Apps >
Startup, and Ollama stops launching with the desktop.

**None of this can break anything.** The application defaults to replay mode,
and the test suite, all four eval gates, the container image and the hosted demo
never contact a model endpoint - so stopping Ollama, uninstalling it or never
installing it leaves every number and every demo exactly as it was.

### Measuring a model, and swapping it (backlog P-16)

```powershell
python -m eval.live --model ollama=http://localhost:11434,mistral:7b-instruct-v0.3-q4_K_M
python -m eval.live --model a=http://localhost:11434,mistral:7b-instruct-v0.3-q4_K_M `
                    --model b=http://localhost:11434,qwen2.5:7b-instruct-q4_K_M
```

It runs every free-text corpus item with the sidecar **removed** and a model in
its place, and reports per model: how many of the corpus's declared values the
model got verified, how many spans it proposed, how many survived the double
lock, the failure histogram, the wall clock per item, and whether the tier
moved. Two `--model` flags is the sovereignty demonstration: the same
comparison, pointed twice, config only.

It writes `eval/reports/live.json` and **always exits 0**. It measures; it does
not gate. An endpoint that was configured and is not there is reported as
unreachable rather than skipped.

### What two local models actually did (part 12, 2026-08-13)

24 free-text letters of gold v4 (16 e-mail, 8 OCR), both models Q4 on the GPU:

| measure | mistral 7b-instruct-v0.3 Q4_K_M | qwen2.5 7b-instruct Q4_K_M | replay |
|---|---|---|---|
| reachable | yes | yes | n/a |
| spans proposed | 86 | 85 | 88 |
| spans verified | 0 | 3 | 88 |
| acceptance rate | 0.000 | 0.035 | 1.000 |
| declared fields recovered | 0 of 50 | 1 of 50 | 50 of 50 |
| field recall | 0.000 | 0.020 | 1.000 |
| tier agreement with replay | 0.667 | 0.667 | 1.000 |
| seconds per letter | 4.07 | 4.40 | 0.012 |

Both models answer in German, on the first attempt, with schema-valid JSON, the
right field names and frequently the right values. What they cannot do is the
fourth thing the double lock needs: a character offset. `quote_mismatch`
accounts for 72 of 86 and 76 of 85 discards. Full diagnosis in
`docs/KNOWN-ERRORS.md` KE-5; the architectural consequence in ADR-028.

**The control that matters.** Running the same letters with no extractor at all
produced the same tier as the live run on **24 of 24** letters. On this corpus a
7B model at Q4 is not a worse extractor than nothing - it is indistinguishable
from nothing.

**Would a gated number have moved?** The 77 form items carry no free text, so
the model is never called for them and the whole possible delta is in the 24
letters. Substituting live evidence there: routing accuracy 1.000 -> 1.000,
**false-clear rate 0.000 -> 0.000** (the metric whose budget is permanently
zero), tier accuracy 1.000 -> 0.921 (8 items), spans 88/88 -> 0-3 of 85-88.
Worth knowing: of those 8 items, 3 moved toward MORE oversight and 5 toward
LESS - losing extraction is not automatically losing in the safe direction.

**Temperature 0 is not determinism.** Three runs of the same model over the same
letters proposed 88, 86 and 86 spans and verified 1, 1 and 0. The aggregate
verdict was stable to three decimals; the individual proposals were not. Report
the run, not the number.

**Recommendation: replay, including for the local showcase.** Live mode is
built, tested and documented, and it is a laboratory instrument for the next
model - not a demonstration setting. Turning it on makes the demo roughly 350x
slower per letter and strictly less informative.

### Redaction of free text in production

The service seals prose with the deterministic recognizers **plus** the optional
NER member when the `[redact]` extra is installed, because a letter carries bare
person names in the middle of a sentence and no regular expression finds them.
The gate deliberately uses the deterministic union alone.

```powershell
$env:EINGANGSLOTSE_TEXT_NER = "0"   # make the service match the gate exactly
```

The corpus generator asserts at build time that deterministic sealing leaves
every gold letter verification-clean, so the gate is not the weaker path.
`docs/KNOWN-ERRORS.md` records the limit neither union closes: OCR-mangled
identity evades any pattern-based detector.

## The fallback classifier: the [classify] extra and the calibration fit (from part 06)

The zero-shot unit classifier (ADR-021) suggests an organizational unit for the
items no routing rule catches - five of gold v4's 101. It is **log-only** in the
shipped config: the suggestion rides the evidence record and the
`evidence_assembled` event, and the decision plane does not read it, because
`engine.decide` admits only the routing sources an agency has allowed and the
default is rules alone.

```powershell
pip install -e ".[dev,classify]"    # sentence-transformers (pulls torch)
```

**Every gate passes without the extra**, and more than that: no gate ever loads
the model at all. `run_pipeline` takes an `embedder` argument that defaults to
None and nothing in the gate passes one, so a gated number cannot depend on
which wheels a machine has. All classifier code paths are covered by a
deterministic hashed-n-gram stub.

Wheel situation on this workstation (2026-08-12): **available and installed
cleanly on CPython 3.13 / Windows** - `sentence-transformers 5.7.0`,
`torch 2.13.0`, `transformers 5.15.0`, `scikit-learn 1.9.0`. `pip check` clean.
The model weights (`intfloat/multilingual-e5-small`, 384 dimensions) download on
first use to the Hugging Face cache; the first call took 34 seconds cold,
including the download. Windows without Developer Mode cannot symlink, so the HF
cache stores full copies - harmless, noisier on disk.

### Measuring what it would suggest

```powershell
python -m eval.run --classifier              # adds the measurements, gates nothing
python -m eval.run                           # the gate path: model never loaded
```

`--classifier` loads the configured model and fills in the `classifier` section:
coverage on the rule-less items (there is no ground truth for them - gold says
`expected_unit_id: null`, so nothing there is scored right or wrong), agreement
with the corpus on the items a rule already routed, and the calibration curve.
Without the flag the section still appears and reports the configured state and
the addressable set, so "what would this have done" is never a missing key.

Measured on gold v4 (2026-08-12, log-only): suggestions for 5/5 rule-less items;
agreement 0.708 (68/96) where the corpus names a unit; raw expected calibration
error 0.2212. Every gated number was identical with and without the model
running, which is the log-only claim checked rather than asserted.

### Fitting the calibration (a human pastes the result)

```powershell
python -m eval.calibrate                     # prints a YAML block, writes nothing
python -m eval.calibrate --bins 8 --gold corpus/gold/v4
```

The fit learns a monotone map from raw cosine to observed accuracy on the items
the corpus labels, and prints a `calibration:` block with its provenance (gold
set, model id, date) plus the expected calibration error before and after. It
**writes nothing**: a human pastes the block into
`config/classifier/classifier_v1.yaml`, and the loader refuses `enabled: true`
without it. That refusal would be theatre if the calibration could appear in the
config with nobody deciding to put it there.

The shipped config deliberately carries **no** calibration block. The fit exists
and is recorded in the engineering log; adopting it is an agency decision, and
there is a real caveat to decide against: the map is fitted on items a rule
already routed (the only ones with ground truth) and would be applied to items
no rule caught.

## Applicant notifications and the simulated inbox (from part 07)

Two messages, both projections of the case journal (ADR-005): `received` triggers
the Eingangsbestaetigung, `routed` triggers the Zwischenstand. Both are
informational Realakte, both are automated end to end, and neither passes the
review UI. The wording lives in `config/notifications/notifications_v1.yaml` and
nowhere else.

The worker runs **inline** after the pipeline in `POST /ingest`, so nothing has
to be started or scheduled. Two URLs show the result:

```powershell
uvicorn api.app:app --reload
# GET /inbox              the simulated applicant inbox, as an HTML page
# GET /inbox/{case_id}    the same messages as JSON
```

### The outbox backend
```powershell
$env:EINGANGSLOTSE_OUTBOX_DIR = "C:\tmp\eingangslotse-outbox"   # one JSONL per case
```
Unset, the outbox is in-memory and is discarded with the process, which is what
tests and a throwaway demo want. Set, it is a `JsonlOutbox` and behaves exactly
like the journal and the vault backends (`EINGANGSLOTSE_JOURNAL_DIR`,
`EINGANGSLOTSE_VAULT_DIR`): three stores, one convention, all three replaced by
PostgreSQL later. In a pilot the backend is replaced by a FIT-Connect status
callback, an SMTP relay or a print spooler; nothing above `engine/notify/outbox.py`
knows which.

### Replaying a journal
```powershell
python -m engine.notify.replay --journal C:\tmp\journal --outbox C:\tmp\outbox --dry-run
python -m engine.notify.replay --journal C:\tmp\journal --outbox C:\tmp\outbox
python -m engine.notify.replay --journal C:\tmp\journal --outbox C:\tmp\outbox   # no-op
```
The third run prints "Nothing owed" and writes nothing. That is the whole
demonstration: the fold derives what a case owes from its events and subtracts
what the journal already records as sent, so a replay of an up-to-date journal
sends nothing. `--dry-run` prints what would go out and writes to neither store.

Note that the worker WRITES to the journal: one NOTIFIED event per dispatch,
carrying `informational_only=True` and the `template_id`. Delivery happens before
the journal write (ADR-022), so the guarantee is at-least-once deduplicated by an
id that is a pure function of the source event and the template.

### Editing the wording
`config/notifications/notifications_v1.yaml` is validated at load time and the
loader refuses:
- a template whose text contains par. 66 SGB I trigger words ("Mitwirkungspflicht",
  "Frist", "Rechtsfolge", "vorzulegen", ...) or a date-shaped literal - a cheap
  tripwire that keeps a receipt from turning into a Nachforderung, not a legal
  analyzer;
- two templates claiming the same trigger, an unknown trigger, an unmapped
  inbound channel, invalid Jinja2, or a display name for a procedure that does
  not exist.

Both rendered texts are frozen as golden files
(`tests/golden/notification_*.txt`), so a wording change fails a test and has to
be looked at rather than noticed in production. Rebuild them by reading the two
entries out of an outbox after a run with a fixed clock; see
`tests/test_notify.py` for the exact fixture.

Nothing on this path is re-hydrated out of the identity vault: the render context
is the case id, the journal timestamps and names that come from config. The
renderer refuses any output carrying a redaction placeholder, and the canary
sweep covers the outbox files, the NOTIFIED payloads and both inbox URLs.

## Prepared drafts and vault re-hydration (from part 08)

Two letters, both prepared AFTER the tier decision and both waiting for a human
(ADR-003, ADR-023): tier 2 with gaps owes a **Nachforderung** assembled from the
gap sentences the procedure configs already author, tier 1 owes a
**Bewilligungsentwurf** with an unmissable ENTWURF framing, and **tier 3 owes
nothing** - drafting for an item nobody has read would presume the outcome. The
wording lives in `config/drafting/drafting_v1.yaml` and nowhere else.

**Nothing is dispatched.** Confirmation, editing and dispatch are part 10. What
part 08 does is render, store and journal that a draft exists.

The drafting step runs **inline** after the notification worker in `POST /ingest`:

```powershell
uvicorn api.app:app --reload
# POST /ingest            reports WHICH drafts exist, never their text
# GET  /drafts/{case_id}  the prepared letters themselves, read-only
```

`GET /drafts/{case_id}` is the one route in this project that returns identity
data - a draft is a letter to a named person, so it carries the applicant's
re-hydrated Anschrift, Versicherungsnummer and Geburtsdatum. **Since part 10 it
needs a unit**: `GET /drafts/{case_id}?unit=Referat_312_Renten`, and it answers
403 without one. That gate is the DEMO form of the Berechtigungskonzept - a
query parameter validated against the taxonomy, no identity provider, no
authentication - and a real deployment replaces it before any real data exists
(C-5).

### The draft store
```powershell
$env:EINGANGSLOTSE_DRAFTS_DIR = "C:\tmp\eingangslotse-drafts"   # one JSONL per case
```
Unset, drafts are in-memory and discarded with the process. Set, the store is a
`JsonlDraftStore` and follows the same convention as the journal, the vault and
the outbox (`EINGANGSLOTSE_JOURNAL_DIR`, `EINGANGSLOTSE_VAULT_DIR`,
`EINGANGSLOTSE_OUTBOX_DIR`). **This store needs the VAULT's protections, not the
journal's**: it holds plaintext personal data in the dev backend exactly as
`JsonlVaultStore` does, and a deployment has to give it the same encryption at
rest, the same retention period and the same erasure path
(`docs/vault-dpia-input.md`).

### Replaying a journal into drafts
```powershell
python -m engine.draft.replay --journal C:\tmp\journal --vault C:\tmp\vault --drafts C:\tmp\drafts --dry-run
python -m engine.draft.replay --journal C:\tmp\journal --vault C:\tmp\vault --drafts C:\tmp\drafts
python -m engine.draft.replay --journal C:\tmp\journal --vault C:\tmp\vault --drafts C:\tmp\drafts   # no-op
```
Same shape as the notification replay, with the vault in front of it. The draft
id is a pure function of the tier-decision event and the template, so the third
run prints "Nothing owed" and writes nothing. `--rechtsfolgenhinweis` renders the
par. 66 Abs. 3 SGB I block into every Nachforderung it produces; it is off by
default and deliberately awkward here, because that choice belongs to a
caseworker, per case, in the review UI of part 10.

One honest limitation: **a prepared decision is reported as blocked on a replay.**
It states the item's extracted values back to the applicant and the journal
deliberately carries none of them (the EXTRACTED payload records field ids and
counts, not values). Nachforderungen replay in full, because every sentence they
contain rides in the EVIDENCE_ASSEMBLED payload.

### Re-hydration, and what blocks a draft
The re-hydrator (`engine/draft/rehydrate.py`) is the **only** production caller
of `VaultStore.fetch`. A template renders with the reserved
`[[PII|KIND|TOKEN]]` syntax still in it, and every token is then resolved
against the fetched record. Any of these produces NO draft at all - not a
partial one:
- an unknown token (which is what stops an invented placeholder resolving to
  somebody else's data), a malformed or truncated one, or a kind that disagrees
  with the vault;
- a value that renders empty, or a round trip that lost a character: the display
  normalizes whitespace and is then compared against the RAW as-received value;
- a record that cannot be read, or a template naming a slot that does not exist.

The blocked draft is reported with its reason and the case simply has no letter.

### Editing the wording
`config/drafting/drafting_v1.yaml` is validated at load time and the loader
refuses:
- an Amtsermittlung entry (C-7) naming a requirement id no procedure declares -
  that list decides both which requests soften and which requirements stay OUT
  of a par. 66 Abs. 3 scope, so a typo would threaten an applicant over a fact
  the agency can look up itself;
- a par. 66 block without its `{{ requirements }}` slot (a hint that names
  nothing is boilerplate) or one that does not cite par. 66 SGB I;
- a letter head naming a payload path the redaction policy does not seal;
- a template naming a context key or a filter that does not exist, invalid
  Jinja2, a missing or duplicated template kind, an unmapped inbound channel, an
  unknown dispatch shape, or a response window outside 1..365 days.

Both rendered letters are frozen as golden files (`tests/golden/draft_*.txt`),
produced from frozen corpus items with a fixed clock; see
`tests/test_draft_letters.py`.

### Deadline math (shipped in 08, called at dispatch by 10)
```python
from engine.draft import response_deadline

response_deadline(date(2026, 8, 4), window_days=30, holidays={date(2026, 8, 15)})
```
The letter states the window RELATIVELY ("innerhalb von 30 Tagen nach
Bekanntgabe") because the absolute date depends on the dispatch date, which does
not exist while a draft waits for a caseworker. `engine/draft/bekanntgabe.py`
computes the absolute one at dispatch: four days for the par. 37 Abs. 2 SGB X
Bekanntgabefiktion, then the window, both moved to the next working day
(par. 26 Abs. 3 SGB X). No function in it reads a clock, and the holiday set is
INJECTED and empty by default - German holidays are Land-specific and the
repository cannot cite which ones apply where a letter is served.

## The shadow scorer (from part 09)

`scikit-learn` is a CORE dependency, unlike the `[redact]` and `[classify]`
extras: those carry a model whose absence degrades a metric, while a missing
scorer would silently remove the system's only source of extra oversight. The
reference population is a 15 KB matrix of numbers in this repository, not a
gigabyte of weights, so there was no wheel-size reason to make it optional.

Two files own it:

| File | What it is |
|---|---|
| `config/scoring/scoring_v1.yaml` | the agency-editable half: threshold, feature wording, the par. 7a Indizien and which value points which way, the leading date per procedure, the sampling salt, the bias advisory |
| `config/scoring/reference_gold_v4.json` | GENERATED. The fitted reference population as a readable matrix with its provenance |

### What a score means

A percentile of the reference population, and nothing else: 0.94 reads as "more
unusual than 94 percent of gold v4", NOT "94 percent likely to be wrong". Two
readings produce it - an IsolationForest over the whole vector for unusual
COMBINATIONS, and a tail share over the three consistency features for a single
value FAR OUT - and the score is the percentile of the larger one (ADR-024).

The scorer runs on every item automatically when `config/scoring/` exists. A
config directory without it means this agency runs no scorer, which is a defined
state and not a degraded one.

### Re-fitting the reference population

```powershell
python -m eval.score_fit                      # rewrite config/scoring/reference_gold_v4.json
python -m eval.score_fit --check              # rebuild and compare; exit 1 on a difference
python -m eval.score_fit --distribution       # the calibration table the threshold was chosen from
```

`--check` is what makes "the reference population is a pure function of (corpus,
feature set, seed, engine)" a gate rather than a claim, and it belongs in CI next
to the corpus check. Re-fit whenever the feature set, the corpus or the seed
changes, and bump `feature_set_version` in the same commit: a score computed by
two different feature sets is two different numbers wearing one name.

`--distribution` prints every item's score and a threshold sweep with recall,
false flags and what an enforcing run would move. That table is the calibration
evidence; the operating point in the config was chosen from it and the rejected
alternatives are its other rows (ADR-024 lists them).

### Determinism, and its limits

Fixed seed, fixed feature order, no clock, no dict-order dependence. Two eval
runs produce byte-identical anomaly outcomes. The claim is **per machine and per
library version**: a tree ensemble is only reproducible against the library that
grew it, so the installed `scikit-learn` version is recorded in the artifact and
printed in the eval report, and `ScoringModel.drift()` reports how far the
recomputed reference scores are from the recorded ones (0.0 here).

### Log-only, and how much friction enforcement costs

`scorer_mode` lives in `config/thresholds.yaml`, whose version string is frozen
into `corpus/gold/v4/MANIFEST.yaml`. Switching it to `enforcing` therefore fails
`python -m corpus.generator.build --check` until the gold set is superseded. That
is deliberate: ADR-004 says enforcement waits for reviewed flag precision, and the
threshold shipped here was calibrated IN-SAMPLE on the frozen set (ADR-024 says so
in those words).

### Audit sampling (P-1)

`AgencyRiskConfig.audit_sample_rate` in `config/thresholds.yaml` is 0.0, so
nothing is sampled and gold behaviour is unchanged. The salt is in
`config/scoring/scoring_v1.yaml` so it can be rotated without a risk-config
supersession. A caseworker can recompute any draw:

```powershell
python -c "import hashlib; print(int.from_bytes(hashlib.blake2b(b'case-ar-0001', key=b'eingangslotse-stichprobe-2026-demo', digest_size=8).digest(),'big')/2**64)"
```

Below the rate means "into full review, tier 3", with a reason that says in words
that this is a random sample and NOT an Auffaelligkeitsbefund. Replace the demo
salt before a pilot and record the replacement in the Betriebsdokumentation.

### Privacy note for a real re-fit

The shipped matrix is derived from a synthetic corpus. An agency that re-fits on
real intake creates a derived personal-data set - 101 rows of aggregate features
per case - and must cover it in its DPIA (`docs/vault-dpia-input.md`). The
generated file carries `row_ids` for debuggability on the synthetic corpus; a
production re-fit should omit them.

## The review UI: queues, case view, confirm and dispatch (from part 10)

```powershell
uvicorn api.app:app --reload
# GET  /review                      queue overview, unit picker, P-6 and P-10 numbers
# GET  /review/queue/{unit_id}      one unit's open work, oldest first
# GET  /review/queue/__clearing__   items no rule routed (par. 16 Abs. 2 SGB I)
# GET  /review/case/{case_id}       the evidence, the drafts, the journal, the actions
# POST /review/case/{case_id}/confirm    -> CONFIRMED (+ dispatch facts)
# POST /review/case/{case_id}/override   -> OVERRIDDEN (re-route or tier, reason required)
# POST /review/case/{case_id}/escalate   -> OVERRIDDEN (one click to tier 3, P-4)
```

Everything renders from the journal and the stores that already exist; the UI
adds no source of truth. Every POST appends exactly one event (two when the
par. 66 opt-in re-renders a letter), returns 303 to the case view so a reload
cannot re-append, and refuses rather than overwrites: a second confirmation, a
correction after confirmation, an escalation of a tier-3 item and an override
that changes nothing are all declined with a sentence.

### The unit picker is not authentication

Pick a unit with `?unit=<unit_id>` from `config/taxonomy/`. An unknown id is
silently no unit rather than an error, and it gates exactly one thing: the case
view's draft section and `GET /drafts/{case_id}`, the only surface that returns
re-hydrated identity data. Every page says on screen that this is a demo and
that a real Berechtigungskonzept with an identity provider is a pilot
prerequisite. Do not put real data behind it.

### Confirm and dispatch

```powershell
$env:EINGANGSLOTSE_DISPATCH_DIR = "C:\tmp\eingangslotse-dispatch"
```

Confirming a case with a prepared letter journals the dispatch facts on the
CONFIRMED payload: `dispatched_at` from an injectable clock, the channel shape
part 08 recorded per case (postal / qualified electronic / status event, C-8),
and for a Nachforderung the ABSOLUTE deadline computed now by
`response_deadline` - four days for the par. 37 Abs. 2 SGB X Bekanntgabefiktion,
then the response window, both moved to the next working day (par. 26 Abs. 3
SGB X).

With `EINGANGSLOTSE_DISPATCH_DIR` set, an **xdomea-SHAPED** XML stub also lands
there, one file per dispatched draft, named by a digest of case and draft so a
re-run rewrites its own file instead of leaving two. It is a placeholder for a
pilot adapter and says so in its own first line and in a `konform="false"`
attribute: no XOEV namespace, no schema validation, no signature. **It carries
no letter text and no addressee** - the body stays in the draft store, and the
canary exception list stays two members long (the vault and the draft store).
With the variable unset nothing is written and the facts are still journaled.

### The Land and its holidays: the one config a deployment MUST change

`config/dispatch/dispatch_v1.yaml` carries `land` as a marked placeholder and
`holidays: []`. Empty means weekends shift and nothing else does, which computes
a deadline that is right except where a Land-specific holiday would have pushed
it one or two working days further out. Filling it is a Fachbereich decision,
not an engineering one: German holidays depend on the place of service and this
repository cannot cite which apply where. The configured Land and the number of
holidays are printed on the confirm form, above the button, so nobody stamps a
deadline without seeing which calendar produced it.

### Queue clocks and budgets

`config/queues/queues_v1.yaml` holds the clearing SLA (48 hours, labelled as an
operational self-commitment because par. 16 Abs. 2 S. 1 SGB I says
"unverzueglich" and not a number), the par. 14 Abs. 1 SGB IX two-week Reha
period (a statutory number), the C-9 Widerspruch flag, and a per-tier latency
budget (24/72/120 hours, P-10). **Nothing in that file gates anything**: no clock
re-orders a queue, hides an item or fails a build.

Both files are NEW, independently versioned config directories rather than keys
in `config/thresholds.yaml` or `config/decision/`, whose versions are frozen into
`corpus/gold/v4/MANIFEST.yaml` and verified by a byte-identical rebuild.

### The correction pool (training data, not a gold set)

```powershell
python -m engine.journal.corrections --journal C:\tmp\eingangslotse-journal `
                                     --out eval/reports/corrections.json
```

Collects every OVERRIDDEN event into a labelled pool: case, field, old value,
new value, the caseworker's reason, the unit, and what the machine had decided.
No person (the actor is a unit), no case content. The file states in its own
header that it is NOT a gold set and may never be merged into one - train on the
pool, measure on the frozen sets (ADR-010). Regenerating it from the same journal
reproduces it byte for byte; the journal stays the truth.

### Accessibility

`docs/accessibility-selfcheck.md` is a SELF-ASSESSMENT against EN 301 549 V3.2.1
/ WCAG 2.1 AA, with three verdicts per criterion: `automated` (a test in
`tests/test_review_accessibility.py` fails if it regresses), `reviewed` (read by
a human) and `open`. It is not an audit and no accessibility statement under
par. 12b BGG may be derived from it. The external BITV 2.0 test is a pilot
prerequisite (P-15).

## Nightly eval
- Runs the real model against the frozen gold set and posts the metric trend, including scorer flag rates. Wiring documented when CI lands.
- The nightly run is `python -m eval.live`, NOT `python -m eval.run`. The gate must stay a function of the repository; the trend of a model is a separate artifact with a separate report.

## The public demonstration posture (from part 11)

OFF by default. With `EINGANGSLOTSE_DEMO_MODE` unset or anything other than
`1`, this project behaves exactly as it did through part 10 - `GET /` is a 404,
`POST /ingest` ingests, no page carries a banner - and
`tests/test_demo_mode.py` asserts that at the level of rendered bytes, not just
status codes.

```powershell
$env:EINGANGSLOTSE_DEMO_MODE = "1"
python -m uvicorn api.app:app --reload
# GET /            the landing page: what the system is, what this instance is not
# POST /ingest     403, refused BEFORE the body is read (middleware, not a route)
# every page       carries the synthetic-data banner
```

| Variable | Effect |
|---|---|
| `EINGANGSLOTSE_DEMO_MODE=1` | Arms the posture. Read ONCE at app construction; a change to the environment of a running process changes nothing |
| `EINGANGSLOTSE_INGEST_TOKEN` | Unset (the safe state) = ingest is refused outright. Set = a caller presenting the value in the `X-Ingest-Token` header gets the normal pipeline, everybody else gets 403 |
| `EINGANGSLOTSE_REPO_URL` | Where the landing page's source-code link points |

Three things it does NOT change, on purpose:

- **the review actions stay enabled.** Confirm, re-route and escalate are the
  product; a demo that showed them greyed out would be a demo of a screenshot.
  The reset is what makes them harmless.
- **the refusal is middleware, not a route dependency.** FastAPI reads and
  JSON-decodes a request body before it solves a route's dependencies, so a
  dependency-based gate answers 422 to a malformed submission - after the
  process has read it. On a closed instance nothing is read at all.
- **nothing is added to the route table or the middleware stack when the flag
  is off.** Both are registered conditionally.

### Seeding and resetting the demo state

```powershell
python -m engine.demo.seed --state-dir deploy/state          # wipe and rebuild
python -m engine.demo.seed --state-dir deploy/state --digest # ... and fingerprint it
python -m engine.demo.seed --gold-dir corpus/gold/v3 --state-dir deploy/state
```

With no `--state-dir` the five `EINGANGSLOTSE_*_DIR` variables are read
instead, and all five must be set: a half-configured state would put the
journal on a volume and the drafts in the container, and the demo would look
correct until the first restart lost half of it.

The seed runs every item of the frozen corpus through the same three calls
`POST /ingest` makes - `run_pipeline`, `notify_case`, `draft_case` - so the
state is the real thing rather than a fixture. On gold v4 that is 101 cases,
197 notifications, 60 drafts and 0 unresolved tokens, which are the eval's own
numbers. `corpus/gold/` is only ever READ.

It is deterministic given its inputs: the clock (`--now`, defaulting to the
current UTC time so a fresh restart shows fresh queues), the placeholder stream
(`--token-seed`) and the deterministic detector union, which it uses even on a
machine that has the `[redact]` extra so that container and laptop agree. What
is left over is the `event_id` of each journal event, a uuid4 the store mints;
`state_digest` folds the state with those removed and two seedings agree.

`deploy/state/` is gitignored. So are `state/`, `.state/`, `demo-state/` and
`var/`. Keep local state under one of those names: the vault's development
backend is plaintext, and a state directory in a public repository publishes
whatever that instance sealed.

## The guided three-phase journey (from part 13) and the tour (from part 15)

Three more pages behind the same flag. They turn the demo from "read the
queues" into "submit something and watch it happen to you".

```powershell
$env:EINGANGSLOTSE_DEMO_MODE = "1"
$env:EINGANGSLOTSE_INGEST_TOKEN = "any-non-empty-string"   # REQUIRED for phase 1
$env:EINGANGSLOTSE_TEXT_NER = "0"                          # if [redact] is installed
python -m uvicorn api.app:app --reload
# GET  /demo/rundgang                   START HERE: the whole system in six steps
# GET  /demo/antrag                     phase 1: pick a persona, edit, submit
# POST /demo/antrag                     -> 303 to the pipeline view
# GET  /demo/case/{id}/pipeline         phase 2: the seven stages of what happened
#                                       -> links into phase 3 with ?highlight=<case>
```

**`/demo/rundgang` is the page to hand somebody who has never seen this.** It
tells the whole story from the first submission to the closed loop in six
steps, German with a short English aside on each, and every step links to the
page where that step actually happens. It works in both intake postures: with a
token it invites a visitor to run their own case, without one it states the
closed posture and walks the seeded corpus instead. Step 3 points at a case
from the frozen gold set (`ar-0011-ohne-rentenbeginn`), so the seven stages of
the glass pipeline are walkable before anybody has submitted anything - on an
instance whose journal was never seeded the page says so and links `/review`
rather than a case id that is not there. The landing page `/` opens with it.

**The ingest token is not optional here.** The intake page is the authorized
server-side CALLER of the token-gated ingest, not an exception to it: it
presents the deployment's own token to the same check the middleware runs. With
`EINGANGSLOTSE_INGEST_TOKEN` unset the instance accepts no submission from
anybody, the demo app included, and `/demo/antrag` renders that in words rather
than failing. That is the shipped default of `render.yaml`, so a hosted
instance is a read-only tour of phases 2 and 3 until an operator decides
otherwise. A direct `POST /ingest` without the header still gets 403 either way.

**Set `EINGANGSLOTSE_TEXT_NER=0` if you installed the `[redact]` extra.** The
hosted image has no extras (ADR-027 ruling 8), so this reproduces its posture.
With the optional spaCy member in the union, the post-seal sweep tags the
context around a masked placeholder as a person on some persona letters and the
boundary refuses its own output - non-deterministically, because it depends on
which characters the token source drew. It is a real refusal and the page shows
it honestly, but it is not the screen you clicked for. Written up as KE-6.

The personas live in `config/demo/personas_v1.yaml`, which is a NEW
independently versioned file that `engine.config_loader` never reads - nothing
in it can move a version stamp or a frozen corpus. Editing it needs no code
change:

| Key | What it does |
|---|---|
| `personas[].fields[].path` | Dotted path into the submission's `data`; an empty value is OMITTED so completeness reports "missing" rather than "invalid" |
| `personas[].fields[].kind` | The placeholder kind this value is expected to become. Used ONLY to pair "what you typed" with "what the machine got" on the pipeline view; it never decides what is sealed |
| `personas[].fields[].group` | Fields sharing a group pair as ONE entry, because the redaction policy seals `antragsteller.anschrift` as a subtree into a single placeholder |
| `personas[].letter` | What the E-Mail tab prefills. Must be sealable by the DETERMINISTIC union alone: name behind a salutation, address as `<Strasse> <Nr>, <PLZ> <Ort>`, a labelled birth date |
| `hints` | The "was Sie ausprobieren koennen" panel |

Two rules a new persona has to keep, both asserted in
`tests/test_demo_personas.py`: the Versicherungsnummer must be checksum-valid
AND carry the persona's birth date in positions 3 to 8, and no value may occur
anywhere in `corpus/pii_golden/` or `corpus/gold/`. Compute a valid number with
`engine.redact.recognizers.vsnr_check_digit`.

The demo store behind the pipeline view holds the redacted working copy and the
visitor's own typed values for 30 minutes, in memory, capped at 64 submissions,
and is constructed only when the flag is on. It is never written to a file, so
a restart wipes it before it wipes anything else. Read ADR-029 before extending
it: it is deliberately NOT the answer to where a working copy lives in
production.

## Container, compose and hosting (from part 11)

```powershell
docker build -t eingangslotse:local .
docker run --rm -p 8000:8000 eingangslotse:local
docker compose up --build          # the demo profile, state on a named volume
docker compose down -v             # and the state with it
```

One image, CORE dependencies only. **No `[redact]` extra and no `[classify]`
extra, and that is a design property rather than a saving**: all four eval
gates are extra-free by construction - the deterministic redaction recall is
1.000 without the NER model, and no gate ever loads the classifier's embedding
model - so the container runs the same code and produces the same numbers on a
base image small enough for a free tier. Part 06 measured a torch image at over
2 GB; this one is 609 MB on disk and about 140 MB of content. A production
deployment that ingests real prose adds `[redact]`, and should.

The build runs `python -m eval.run`, which does two things: the image ships an
eval report so `/metrics` shows measured numbers the moment it boots, and **an
image that cannot pass its own gate is never produced**.

Non-root (uid 10001), healthcheck on `GET /healthz` - a constant, unlike
`GET /health`, which reads the config bundle to answer which versions are
running. The entrypoint seeds the state before uvicorn binds, so **a restart IS
a reset**; there is no timer and no scheduler anywhere in this project.

Hosting: `render.yaml` is a Render FREE web service blueprint and is the
primary target. `deploy/README.md` has the honest notes (15-minute spin-down,
roughly one-minute cold start, no persistent disk - which is the reset model
rather than a limitation) and the survey of the other zero-cost paths,
including why no `fly.toml` is shipped. `docs/PUBLISHING.md` is the user
handoff: the exact `gh` commands and the Render click path.

## The user interface (from part 16)

There is still no CSS build step, no bundler and no JavaScript requirement, so
there is nothing to run: `ui/static/*.css` is plain CSS served as it is written
and `ui/templates/*.html` is Jinja. Three things a contributor should know
before editing either.

**Sentences live in `api/i18n.py`, not in the templates.** Every visitor-facing
string is a key into one table whose values are `(German, English)` pairs, so a
key cannot exist in one language and not the other. A template asks for a
phrase with `t("some.key")`, or with `m("some.key")` for the handful that carry
inline markup - `m()` returns Markup and ESCAPES what it interpolates, which
`|safe` on a formatted string would not. `tests/test_i18n.py` sweeps every
template for the keys it asks for, so a typo fails the suite rather than
printing a key on a page.

The caseworker screens (`/review*`, `/metrics`) are deliberately NOT in the
table: they stay German in both language settings and carry one English line
saying why. Message bodies, gap sentences and letter texts are never translated
at all - they come from versioned configuration and are legal-text artifacts.

**Two colours may not carry text and a test enforces it.** `--brand` (#4db2ec)
measures 2.36:1 on white and `--alarm` (#dc0000) 4.32:1 on the darkest surface
here; both are element colours - a fill, a rule, an edge - and both have a
text-weight sibling in the same family (`--brand-ink`, `--alarm-text`). The
measured matrix is in `docs/accessibility-selfcheck.md`, and
`tests/test_review_accessibility.py` greps every stylesheet so the rule cannot
be forgotten.

**The landing hero is the only animated thing in the project.** One 16-second
CSS loop, five captions that are real text in the document, one keyframe set
with five negative `animation-delay` values so the picture and the sentence
share a clock. Both the `prefers-reduced-motion` answer and the on-page pause
control put it into the same still frame; neither uses `animation-play-state`,
because freezing the loop where it stands would hide four of the five captions.

## Continuous integration (from part 11)

`.github/workflows/gate.yml` runs the whole gate above on every push and pull
request: `pytest` with the coverage floor, `ruff format --check`, `ruff check`,
`mypy`, `--check` over gold v1 to v4 and the PII golden set,
`eval.score_fit --check`, `eval.run` (the four gates), the schema export and a
clean-tree assertion, then the image build and a container smoke test.

The commands in that file are the commands in this document, in this
document's order. **If the two disagree, this document is the specification and
the workflow is the bug.**

One step exists only to protect a property: it asserts that
`presidio_analyzer`, `spacy`, `torch` and `sentence_transformers` are NOT
importable. A gate that silently gained a model would keep passing while
measuring something else, and the whole claim of the numbers is that they do
not depend on which wheels a machine has.
