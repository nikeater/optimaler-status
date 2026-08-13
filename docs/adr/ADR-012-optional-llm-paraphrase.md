# ADR-012: The LLM Paraphrase Pass Is Optional, Provenance-Stamped and Label-Safe

**Status:** Accepted, 2026-08-11 (part 02, plan step S2)

## Context
A corpus rendered from one template teaches the pipeline nothing about surface
variation, and a demo built on it flatters itself. Real submissions differ in
key order, whitespace, date formats and the free-text note a human wrote. Using
a local instruct model to vary that surface is attractive and carries two
risks:

1. **Dependency risk.** If generation needs Ollama, the corpus cannot be rebuilt
   on a machine without it, and the verification gate becomes environment-
   dependent. Worse: a developer who happens to have a model running would
   produce a *different* corpus from the committed one, silently.
2. **Truth risk.** A model asked to "make this more realistic" will eventually
   change a date, a Rentenart or a Versicherungsnummer, and the corresponding
   label would then be a lie in the ground truth.

## Decision
The paraphrase pass has two strategies and a hard boundary.

* **Deterministic by default.** Seeded jitter of decorative fields, key order,
  whitespace and a Jinja2-rendered cover note. Reproducible from the seed and
  sufficient on its own; the committed v1 corpus is 41/41 deterministic.
* **LLM when explicitly configured.** An OpenAI-compatible endpoint (base URL +
  model from a flag or env var). `--paraphrase auto` **does not probe unless an
  endpoint is configured**, so having a model running locally cannot change the
  build. `--paraphrase llm` fails loudly when the endpoint is unreachable
  instead of falling back quietly.
* **The model may only touch the free-text cover note**, which lives at a
  payload path no `field_map` references. Every other value comes from the
  scenario spec. Two independent checks enforce it: the paraphrase guard
  compares mapper-visible values before and after, and the build re-runs the
  whole pipeline over the written item.
* **Every failure mode degrades deterministically.** Unreachable, timeout, HTTP
  error, non-JSON, missing choices, an answer that is empty, too long, contains
  markup, or opens with a refusal: all discarded, deterministic note kept.
* **Provenance is recorded per item** (`paraphrase: llm|deterministic|none`) in
  the sidecar and aggregated in the MANIFEST and the eval report, so a mixed
  corpus can always be split by how its surface was produced.

## Consequences
- The corpus can be rebuilt anywhere, byte-identically, with no model installed;
  CI needs no GPU and no network.
- A model contributes realism and can never contribute a wrong label.
- Cost: the committed v1 corpus has less linguistic variety than a model pass
  would give it. Free-text realism matters from part 05 (text layer, LLM
  extraction) onward, and that is when running the pass with a model and
  publishing a `v2` set becomes worthwhile - as a new frozen version, per
  ADR-010, not as an edit of v1.
- The client is stdlib-only (`urllib`) with an injectable transport, so no test
  in the suite touches a socket and no runtime dependency was added.
