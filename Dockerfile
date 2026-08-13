# EingangsLotse - one slim image, core dependencies only.
#
# WHAT IS DELIBERATELY NOT IN HERE, and why it is a design property rather than
# a degradation:
#
#   * the [redact] extra (presidio-analyzer, spaCy, de_core_news_lg). The
#     deterministic recognizers seal every gold letter clean on their own, and
#     the measured redaction recall of 1.000 is the DETERMINISTIC number - the
#     gate has never depended on the model. Installing it would add roughly
#     600 MB of wheels and a model download to improve one thing the demo does
#     not do: read a real letter with a bare person name in the middle of a
#     sentence. A production deployment that ingests real prose adds it; see
#     docs/BUILD.md.
#   * the [classify] extra (sentence-transformers, torch). The zero-shot unit
#     classifier is LOG-ONLY, is never auto-loaded, and part 06 measured the
#     torch image at over 2 GB. No gate loads an embedding model, so no image
#     that only has to pass the gate needs one.
#
# The result is that the container runs the same code the four eval gates run,
# with the same numbers, on a base image small enough for a free tier.
#
# Base: python:3.13-slim. The project pins requires-python >= 3.12 and the
# development venv on the machine this was built from is CPython 3.13.14, so
# 3.13 is the version the pins are actually exercised against.

# ----------------------------------------------------------------- builder ---
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# gcc is here for the rare source-only wheel and stays in the BUILDER stage.
# The final image gets a compiled virtualenv and no compiler.
RUN apt-get update \
 && apt-get install --no-install-recommends -y build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# The CORE dependencies and nothing else - not the project itself.
#
# Installing the project would put a second copy of `engine`, `api`, `schemas`
# and `eval` into site-packages, where it could shadow the source tree the
# runtime stage actually serves from, and it would not help: the service also
# needs `corpus/`, `config/` and `ui/`, which are data rather than packages and
# are resolved relative to the working directory. So the venv carries libraries
# and /app carries the program, which is also why a source-only change does not
# re-resolve scikit-learn.
COPY pyproject.toml ./
RUN python -c "import tomllib;d=tomllib.load(open('pyproject.toml','rb'));print(chr(10).join(d['project']['dependencies']))" > requirements.txt \
 && cat requirements.txt \
 && pip install --upgrade pip \
 && pip install -r requirements.txt

# ------------------------------------------------------------------ runtime ---
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # The state directories. All five, because engine.demo.seed refuses a
    # half-configured environment on purpose (a journal on a volume and the
    # drafts in the container's filesystem is a demo that loses half of itself
    # on the first restart).
    EINGANGSLOTSE_STATE_DIR=/var/lib/eingangslotse \
    EINGANGSLOTSE_JOURNAL_DIR=/var/lib/eingangslotse/journal \
    EINGANGSLOTSE_VAULT_DIR=/var/lib/eingangslotse/vault \
    EINGANGSLOTSE_OUTBOX_DIR=/var/lib/eingangslotse/outbox \
    EINGANGSLOTSE_DRAFTS_DIR=/var/lib/eingangslotse/drafts \
    EINGANGSLOTSE_DISPATCH_DIR=/var/lib/eingangslotse/dispatch \
    # The [redact] extra is not installed, so the NER member of the detector
    # union cannot load anyway. Saying so explicitly means the log line reads
    # "off because the operator said so" rather than "off, reason unknown", and
    # it makes the running service seal prose exactly the way the seed did.
    EINGANGSLOTSE_TEXT_NER=0 \
    # The public posture. ON in this image: an image whose whole purpose is a
    # hosted demonstration should not need an operator to remember the flag.
    # A deployment that wants the plain app sets it to 0.
    EINGANGSLOTSE_DEMO_MODE=1 \
    PORT=8000

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Only what the service runs. No tests, no .git, no docs (see .dockerignore).
COPY schemas/ ./schemas/
COPY engine/ ./engine/
COPY api/ ./api/
COPY eval/ ./eval/
COPY corpus/ ./corpus/
COPY config/ ./config/
COPY ui/ ./ui/
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh
COPY pyproject.toml README.md LICENSE ./

# The metrics page renders the report `python -m eval.run` wrote and computes
# nothing itself, so the image bakes one in. Two things fall out of that: the
# hosted demo shows real measured numbers the moment it boots instead of a
# "run the eval" hint, and THE BUILD FAILS IF THE GATE FAILS - eval.run exits
# non-zero on a false clear, a redaction miss, a moved structured item or an
# unreasoned anomaly flag. An image that cannot pass its own gate is not built.
RUN python -m eval.run

# Non-root, with a fixed uid so a bind-mounted volume has a predictable owner.
# Not --system: a system account is capped at uid 999 on Debian and useradd
# warns rather than failing, which is the shape of a problem that surfaces
# three deployments later.
RUN useradd --uid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
 && mkdir -p "$EINGANGSLOTSE_STATE_DIR" \
 && chown -R app:app "$EINGANGSLOTSE_STATE_DIR" /app \
 && chmod +x /app/deploy/entrypoint.sh
USER app

EXPOSE 8000

# /healthz and not /health: the healthcheck runs every 30 seconds forever and
# must stay a constant, while /health reads the config bundle to answer which
# versions this process is running. start-period covers the boot-time seed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz').read()"

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
