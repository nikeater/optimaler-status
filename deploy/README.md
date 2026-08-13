# Hosting the public demonstration instance

Zero cost is a hard constraint on this deployment: no paid plan, no paid
add-on, no "free trial that becomes a bill". Everything below is either free
today or is written down as something to verify before it costs anything.

Everything here assumes the container from the repository root `Dockerfile`.
What it is and what it deliberately leaves out is documented there; the short
version is that it installs the CORE dependencies only, which is possible
because all four eval gates are extra-free by design.

## The reset model, once, because every host section depends on it

The entrypoint (`deploy/entrypoint.sh`) wipes the five state directories and
rebuilds them from the frozen gold corpus before uvicorn binds. So:

- a restart IS a reset, by construction. There is no in-process timer and no
  scheduler anywhere in this project;
- an ephemeral filesystem is not a limitation here, it is what we want. A host
  that gives a free service no persistent disk gives this demo exactly the
  right thing;
- a host that stops an idle free service and starts it again on the next
  request is resetting the demo for free, on the best possible schedule.

Seeding takes about 6 seconds (measured in the container on a developer
machine, 101 items). Health checks need a grace period longer than that.

## The plaintext-store caveat, and why it is acceptable HERE

`JsonlVaultStore` and `JsonlDraftStore` write plaintext. They say so in their
own docstrings, and `docs/vault-dpia-input.md` says what production needs
instead (encryption at rest, retention, erasure).

**That is acceptable on this deployment for exactly two reasons, and both have
to hold.** The seeded data is synthetic - every item comes from
`corpus/gold/v4` and no real person appears in it - and the demo posture
refuses ingest, so nothing else can get in. Remove either half and the
deployment becomes a data-protection incident with a URL: an open ingest in
front of a plaintext vault would collect real personal data from strangers and
store it unencrypted. Do not set `EINGANGSLOTSE_INGEST_TOKEN` on a public
instance without a reason you can write down.

## Render (the primary target, free plan)

`render.yaml` at the repository root is a Blueprint for a **free** web service
with a Docker runtime. Click path, exact commands and what to verify after the
first deploy: `docs/PUBLISHING.md`.

Checked against Render's own documentation on 2026-08-13
(<https://render.com/docs/free>); free-tier terms move, so re-read it before
relying on any of this:

| Fact | What it means here |
|---|---|
| A free web service spins down after 15 minutes without inbound traffic | The first request after an idle night is slow |
| Spinning back up takes about a minute, with a Render loading page | Say so next to the demo link, or a visitor concludes it is broken |
| Free web services cannot attach a persistent disk | Fine. The state is rebuilt on every boot anyway |
| 750 free instance hours per workspace per calendar month | One always-idle demo will not come close |
| Bandwidth and build minutes count against workspace allowances | Exceeding them suspends free services rather than billing you, IF no payment method is on file |
| Payment method | Render's docs mention one only for overages, and the free path is documented as usable without it. **Not verified end to end by this repository** - if the signup asks for a card, stop and re-read the terms |

The build runs `python -m eval.run`, so a Render build takes a minute or two
longer than a plain `pip install` and **fails if the gate fails**. That is
deliberate: an image that cannot pass its own gate should never be deployed.

Region is `frankfurt` in `render.yaml`. The data is synthetic but shaped like
personal data, and the audience is German public administration; keeping it in
the EU is the posture a real deployment would need anyway.

## Fly.io - NOT shipped, because it is no longer free

There is no `fly.toml` in this repository, and its absence is a decision.

Checked against Fly's own pricing page on 2026-08-13
(<https://fly.io/docs/about/pricing/>): **Fly.io does not offer a free tier to
new organizations.** The old permanent allowance (three shared-cpu-1x 256 MB
Machines) survives only on deprecated legacy plans. A single shared-cpu-1x
Machine with 256 MB was priced at roughly 2 USD per month at that time, a
stopped Machine still pays for its rootfs storage, and 256 MB is in any case
too little for this image - scikit-learn, scipy and numpy are core
dependencies and the boot-time seed runs the whole corpus through the
pipeline, so 512 MB is the smallest size worth trying.

Shipping a `fly.toml` would therefore be shipping a config whose happy path is
a bill. If you are on a legacy Hobby organization that still has the free
allowance, the deployment is a normal `fly launch --no-deploy` over this
Dockerfile with `internal_port = 8000`, `auto_stop_machines = "stop"` (NOT
`"suspend"`: suspend resumes the same memory image and would not re-seed),
`min_machines_running = 0`, a `/healthz` check with a 90 second grace period
and a 512 MB VM. Verify the current terms first.

## Other zero-cost paths, and what would have to be verified

Neither of these is shipped as a config, because neither was deployed and
verified from this repository. They are written down so the next person does
not have to re-do the survey.

- **Locally, with Docker Compose.** `docker compose up --build` is free,
  needs no account, and is the honest answer for a reviewer who wants to click
  around rather than for a public link. This IS verified: see the log entry for
  the build and run transcript.
- **Hugging Face Spaces, Docker SDK.** Documented as free on CPU basic with no
  payment method, and the storage is ephemeral, which suits the reset model.
  Two things would have to change before it works and both are in the docs
  (<https://huggingface.co/docs/hub/spaces-sdks-docker>): the container runs as
  **user ID 1000**, while this image creates uid 10001, and the exposed port
  comes from `app_port` in the Space's README metadata rather than from
  `EXPOSE`. Verify the current free hardware tier before relying on it.
- Anything advertising a "free trial with credits" is not a free tier. Railway
  and Fly both fall in that class today.

## Scheduled restarts, if you ever need one

You should not: a host that sleeps idle services already resets the demo, and
a demo nobody is using does not need resetting. If a host keeps the container
up forever and you want a daily reset, use the platform's own restart
mechanism (Render: "Manual Deploy" or a scheduled restart if your plan has
one; systemd: `RuntimeMaxSec=`; Kubernetes: a CronJob that deletes the pod).

Do not add a timer inside the process. The reset would then be a thing the
application does to itself while serving requests, and "restart equals reset"
would stop being true by construction and start being true by maintenance.
