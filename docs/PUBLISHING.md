# Publishing this repository and hosting the demo

Everything in this document is for a HUMAN to run. The build that produced
these files created no GitHub repository, added no git remote, pushed nothing
and created no hosting account, on purpose: publishing is a decision, and the
decision is yours.

Two hard constraints are baked into what follows. **Zero cost**: every step
below is on a free plan, and where a service is no longer free that is said in
plain words instead of being left for you to discover at a payment screen.
**Nothing real reaches the demo**: the hosted instance refuses `POST /ingest`
outright, which is what makes a plaintext development vault acceptable in
public.

---

## Part 0. Preflight, before anything is public

Run these from the repository root. All of them are green as of part 11; run
them again anyway, because the point of a preflight is that it was run.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m corpus.generator.build --out corpus/gold/v4 --check
.\.venv\Scripts\python.exe -m corpus.pii_golden.build --check
.\.venv\Scripts\python.exe -m eval.score_fit --check
.\.venv\Scripts\python.exe -m eval.run
.\.venv\Scripts\python.exe -m schemas.export_json_schema
git status --porcelain          # must print nothing
```

Then read these four things with your own eyes:

1. **The sampling salt.** `config/scoring/scoring_v1.yaml` carries
   `audit_sampling.salt: eingangslotse-stichprobe-2026-demo`. It is a DEMO
   value and the file says so in its own `note:` field: replace it before a
   pilot, record it in the operational documentation, and keep the real one out
   of a public repository. It is safe to publish this one - the audit sample
   rate is 0.0, so it currently draws nothing, and a salt whose only job is to
   make a draw recomputable is worthless once rotated. **Rotate it per
   deployment.**
2. **No other credential-shaped value.** A scan of all 864 tracked files for
   AWS keys, GitHub personal-access and OAuth tokens, Slack tokens, OpenAI
   keys, Google API keys, Stripe keys, private-key blocks, JWTs and URLs with
   embedded credentials found nothing. The only credential-NAMED assignments in
   the repository are that salt, the same salt quoted in this document, and
   three test salts in `tests/`.
3. **No state directory is tracked.** `git ls-files | Select-String
   "vault|journal|outbox"` must return only source files. The vault's
   development backend is plaintext JSONL; a state directory in a public
   repository publishes whatever that instance sealed.
4. **The corpus is synthetic.** It is - `corpus/generator/` builds it from
   scenario specs and the manifest is checksummed - but say it out loud once
   before making it public, because "the test data is fake" is the assumption
   everything else here rests on.

---

## Part 1. Create the repository and push

Replace `OWNER` with your GitHub username or organization throughout.

```powershell
# One-time, if the GitHub CLI is not authenticated yet. It opens a browser.
gh auth login

# From the repository root. --public is the point of the exercise; --source=.
# uses the existing local history, and --push does the first push.
gh repo create OWNER/eingangslotse `
  --public `
  --source=. `
  --remote=origin `
  --push `
  --description "Two-plane triage assistant for inbound public-administration items: deterministic decisions, an append-only journal, and identity sealed at ingest."
```

If you would rather do it by hand: create an empty public repository named
`eingangslotse` at <https://github.com/new> (no README, no .gitignore, no
license - this repository has all three), then:

```powershell
git remote add origin https://github.com/OWNER/eingangslotse.git
git push -u origin main
```

Immediately after the first push:

- open the **Actions** tab and watch `gate` run. It has never executed - GitHub
  Actions cannot be run locally - so this is its first evidence. It runs the
  full gate plus a container build and smoke test, and takes roughly ten to
  fifteen minutes;
- check that GitHub shows **EUPL-1.2** in the sidebar. If it says "View
  license" without a name, the `LICENSE` file was modified; it must be the
  official text byte for byte;
- set the repository **description** and **topics** if you did not pass
  `--description` (suggested topics: `public-administration`, `govtech`,
  `fastapi`, `eupl`, `gdpr`, `ai-act`, `german`).

Optional, and worth it: **Settings -> Branches -> Add branch protection rule**
for `main`, requiring the `gate` check to pass. The CI is the credibility
argument; a red main branch removes it.

---

## Part 2. Host the demo on Render (free)

Render is the primary target because it has a real free plan for Docker web
services. Terms below were checked on 2026-08-13 against
<https://render.com/docs/free> and they change; re-read that page.

**Credit card:** Render's own documentation mentions a payment method only in
connection with exceeding free allowances, and the documented behaviour of
going over WITHOUT a card on file is that free services are suspended rather
than billed. This repository did not verify the signup flow end to end. **If
the signup asks you for a card, stop and re-read the current terms before
entering one** - nothing here needs a paid plan.

1. Sign in at <https://dashboard.render.com/> with your GitHub account and
   grant it access to the `eingangslotse` repository.
2. **New -> Blueprint** (<https://dashboard.render.com/select-repo?type=blueprint>).
   Pick the repository. Render reads `render.yaml` from the root and offers one
   service, `eingangslotse-demo`: a **Free** web service, Docker runtime,
   region **Frankfurt**, health check `/healthz`.
3. Apply. The first build takes several minutes - it installs the dependencies
   and then runs `python -m eval.run` inside the image, so the build itself
   fails if the gate fails.
4. When it is live, go to **Environment** and set
   `EINGANGSLOTSE_REPO_URL` to `https://github.com/OWNER/eingangslotse`
   (the blueprint ships the `OWNER` placeholder, and the landing page links to
   it).
5. **Do not set `EINGANGSLOTSE_INGEST_TOKEN`.** Its absence is the setting:
   with no token, `POST /ingest` is refused for everybody and the instance
   cannot receive a submission from anybody. Only set it if you have a reason
   you can write down, and then generate it properly and treat it as a secret:

   ```powershell
   # 32 random bytes as hex. Paste the output into Render's Environment tab,
   # never into a file in the repository.
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
   ```

### Verify the deployment, in this order

Replace `URL` with the address Render assigned
(`https://eingangslotse-demo.onrender.com` or similar).

| Check | Expected |
| --- | --- |
| `URL/healthz` | `{"status":"ok"}` |
| `URL/` | The landing page, with the red synthetic-data banner at the top |
| `URL/review` | The queue overview, **101 offene(r) Vorgang**, banner present |
| `URL/metrics` | "Gate bestanden", gold dir `corpus/gold/v4`, 101 items |
| `URL/inbox` | 197 messages |
| `POST URL/ingest` with any body | **403**, with a body explaining that ingest is disabled |
| Render logs at boot | `entrypoint: seeding demo state from the frozen gold corpus`, then 101 / 197 / 60 / 0 |

```powershell
# The one that matters. Anything other than 403 means the demo is open.
curl.exe -s -o NUL -w "%{http_code}`n" -X POST -H "Content-Type: application/json" -d "{}" URL/ingest
```

Then confirm a case in `/review`, watch the open count drop to 100, use
Render's **Manual Deploy -> Restart service**, and watch it go back to 101.
That is the reset model working: the entrypoint rebuilds the whole state from
the frozen corpus on every boot, so nothing a visitor does survives a restart.

### What to expect from the free plan, and what to tell visitors

- The service **spins down after 15 minutes** with no traffic and takes about a
  minute to come back. The first click on a link that has been idle overnight
  is slow, and Render shows its own loading page meanwhile. Put "first load may
  take a minute" next to the link wherever you share it.
- Free web services get **no persistent disk**, which is exactly right here:
  every spin-down is a free reset.
- 750 free instance hours per workspace per calendar month. An idle demo will
  not come close.

---

## Part 3. Alternatives, honestly

- **Locally**: `docker compose up --build`, then <http://localhost:8000/>. Free,
  no account, and the option to give a reviewer who would rather run it than
  click a link. This is the path that was actually verified during part 11.
- **Fly.io**: no `fly.toml` is shipped, because Fly's own pricing page on
  2026-08-13 states there is no free tier for new organizations. See
  `deploy/README.md`.
- **Hugging Face Spaces (Docker SDK)**: documented as free on CPU basic with no
  payment method, and ephemeral storage suits the reset model. It would need
  two changes to the image (the container runs as uid 1000, and the port comes
  from `app_port` in the Space README metadata). Unverified; see
  `deploy/README.md`.

---

## Part 4. After it is public

- Put the demo URL in the README where it says `DEMO URL` and push. The landing
  page's repository link comes from `EINGANGSLOTSE_REPO_URL` on the host, not
  from the README, so both places need doing.
- Watch the first `gate` run to completion before telling anyone about the
  repository.
- If you later publish on **openCode**, REUSE-style per-file license annotation
  is the known refinement (ADR-006) and the published known-errors log per
  release is the open half of compliance item P-12.
