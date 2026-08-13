#!/bin/sh
# Seed, then serve. This is the whole reset mechanism.
#
# There is no in-process timer and no scheduler anywhere in this project, and
# that is deliberate: a restart IS a reset by construction, because the first
# thing a fresh container does is wipe the five state directories and rebuild
# them from the frozen gold corpus. Whatever a visitor confirmed, overrode or
# escalated is gone. A host that stops an idle free-tier service and starts it
# again on the next request therefore resets the demo for free; a host that
# does not gets the same effect from a scheduled restart (deploy/README.md).
#
# The seed reads corpus/gold/ and never writes to it.

set -eu

if [ "${EINGANGSLOTSE_SKIP_SEED:-0}" = "1" ]; then
  echo "entrypoint: seeding skipped (EINGANGSLOTSE_SKIP_SEED=1)" >&2
else
  echo "entrypoint: seeding demo state from the frozen gold corpus" >&2
  python -m engine.demo.seed
fi

echo "entrypoint: starting uvicorn on 0.0.0.0:${PORT:-8000}" >&2
exec uvicorn api.app:app --host 0.0.0.0 --port "${PORT:-8000}" --no-server-header
