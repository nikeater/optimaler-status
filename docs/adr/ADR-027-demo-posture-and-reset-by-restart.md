# ADR-027: The Public Demo Is a Deployment Posture, and a Restart Is the Reset

**Status:** Accepted, 2026-08-13 (part 11, showcase deploy)

## Context

Ten parts built a system whose privacy argument rests on one sentence: identity
data is sealed at ingest, before anything else touches it (ADR-017). Two facts
about the shipped state make a PUBLIC instance of that system dangerous rather
than impressive.

First, the vault's development backend is plaintext JSONL, and it says so in
its own docstring (ADR-018) because a home-rolled file cipher would look like
protection without being any. That has been fine for ten parts, because the
only data it ever held was synthetic.

Second, `POST /ingest` is open. On a laptop that is a convenience. On a URL a
stranger can reach, it is an endpoint that will seal whatever a stranger types
into an unencrypted file - and the first person to paste a real
Versicherungsnummer into a demo turns the showcase into a data-protection
incident with a public address. The two facts are individually defensible and
jointly indefensible.

A third problem is smaller but shapes everything else: a demo that anyone can
click has to be resettable. The review actions - confirm, re-route, escalate -
are the product; the whole of Art. 22's "meaningful human involvement" becomes
visible only when a visitor can press the buttons. But they append to the
journal, and after a week of visitors the demo would be a screen full of other
people's decisions.

## Decision

**1. Demo mode is a deployment POSTURE, env-gated, default OFF, and the engine
does not know about it.** `EINGANGSLOTSE_DEMO_MODE=1` arms three things at
once: the ingest gate, a synthetic-data banner on every rendered page, and a
landing page at `GET /`. Nothing in the pipeline, the decision plane or the
redaction boundary imports `engine.demo`; the posture lives in one frozen
dataclass read exactly once, at app construction, and is handed to the routes
and to the template environment. An operator who edits the environment of a
running process has not changed the posture of the app that is serving, and
that is the intended behaviour rather than an oversight.

**2. With the flag off, nothing is observable - and that is asserted, not
hoped.** `GET /` is absent from the route table (the landing route is
registered conditionally, so the OpenAPI document does not change either), the
ingest middleware is not installed at all, and each page template rendered
through the real Jinja environment is compared BYTE FOR BYTE against the same
template rendered through an environment where the banner include is the empty
string. A demo feature that cost the production path a single byte would be a
demo feature in the production path.

**3. The ingest gate is MIDDLEWARE, not a route dependency, and the difference
is the whole point.** FastAPI reads and JSON-decodes a request body before it
solves a route's dependencies. A dependency-based gate therefore refuses a
submission only after this process has parsed it, and on a malformed body with
a JSON content type it never runs at all - the decode error becomes a 422
first. The first implementation in this part was a dependency and it passed
every test written for it except the one that mattered; the middleware version
refuses before the body is touched. "This instance cannot receive real personal
data" has to be true of the reading, not only of the storing.

**4. An unset token is the safe state, and it means CLOSED.** With
`EINGANGSLOTSE_INGEST_TOKEN` absent, `POST /ingest` is refused for everybody
and the instance can accept nothing from anyone. With it set, a caller
presenting the value in the `X-Ingest-Token` header gets the normal pipeline;
the comparison is `hmac.compare_digest`, because the alternative on a public
endpoint is a timing oracle. Both refusals state their semantics in the
response body: a 403 that does not say WHY teaches an integrator to retry. The
banner text differs between the two postures, because "this instance accepts no
submissions" would be false on a token-gated box.

**5. The review actions STAY ENABLED in demo mode.** They are the thing worth
showing. A demo with greyed-out buttons demonstrates a screenshot. What makes
them harmless is not a permission check, it is the reset.

**6. The reset is a restart, by construction. There is no timer.** The
container entrypoint wipes the five state directories and rebuilds them by
running the frozen gold corpus through `run_pipeline`, `notify_case` and
`draft_case` - the same three calls `POST /ingest` makes - before uvicorn
binds. Everything a visitor did is gone the next time the service starts.

Two alternatives were rejected. An in-process timer would make the reset
something the application does to itself while serving requests, and "restart
equals reset" would stop being true by construction and start being true by
maintenance. A scheduled external job would work but is a second moving part
that every host spells differently. As it happens the free-tier hosts do the
job for free: a platform that stops an idle service and starts it again on the
next request is resetting the demo on the best possible schedule, which is why
an ephemeral filesystem is the RIGHT property here rather than a limitation to
work around.

**7. The seed is deterministic given its inputs, and the digest says which
inputs.** The clock, the placeholder token stream and the detector union are
all injected; the seed uses the DETERMINISTIC detector even on a machine that
has the `[redact]` extra, so a developer's state and the container's state
agree. What remains variable is the `event_id` of each journal event, a uuid4
the store mints. `state_digest` folds the state with those ids removed, and two
seedings of the same corpus with the same base clock produce the same digest.
The base clock defaults to the wall clock rather than to a frozen anchor, so a
fresh restart shows fresh queues instead of cases that have been waiting since
January; `--now` pins it, and that is what the idempotence test uses.

**8. The demo image installs the CORE dependencies only, and the image
documentation states that as a design property.** No `[redact]` extra, no
`[classify]` extra. The four eval gates have been extra-free since the parts
that introduced those extras - the deterministic redaction recall is 1.000
without the NER model, and no gate ever loads the classifier's embedding model
- so the container runs the same code and produces the same numbers. To keep
that from silently becoming untrue, the CI workflow asserts that
`presidio_analyzer`, `spacy`, `torch` and `sentence_transformers` are not
importable, and the image build runs `python -m eval.run`, which means an image
that cannot pass its own gate is never produced.

## Consequences

- `engine.demo` joins the coverage floor at 100 percent. It is there for the
  redaction package's reason rather than the review package's: the ingest gate
  is the single control keeping a stranger's real data out of a plaintext
  vault, and an unexercised branch in it is a lock nobody knows still turns.
- The canary sweep gains two surfaces, the landing page and the banner, plus
  the 403 body - the three things a public visitor is most likely to see and
  the three written last.
- The plaintext-store caveat is now conditional in writing: it is acceptable
  BECAUSE the seeded data is synthetic AND the ingest gate keeps real data out.
  `deploy/README.md` states both halves in one paragraph, because removing
  either one silently is exactly how this would go wrong.
- The unit picker's demo-grade role model (C-5) is now also stated on the
  landing page, which is the first thing a visitor reads rather than the fourth.
- A demo instance still has no authentication in front of the review actions.
  That is acceptable only because a restart erases what they do and the data
  they act on is synthetic; it is not a step toward the Berechtigungskonzept and
  must not be read as one.
- Hosting is free-tier only by user constraint. `render.yaml` targets Render's
  free web-service plan; no `fly.toml` ships, because Fly's own pricing page on
  2026-08-13 states there is no free tier for new organizations, and a config
  whose happy path is a bill is a trap rather than an option.
