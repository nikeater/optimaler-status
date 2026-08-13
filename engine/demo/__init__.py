"""Public-demo posture and demo-state seeding.

Two modules, deliberately separate. :mod:`engine.demo.mode` is what a running
app reads: an env-gated posture that closes ingest, banners every page and adds
a landing page, and that is OFF unless a deployment says otherwise.
:mod:`engine.demo.seed` is the CLI a deployment runs before the app starts: it
rebuilds the whole demo state from the frozen gold corpus through the real
pipeline, which is what makes "restart the service" a complete reset.

Nothing in the pipeline, the decision plane or the redaction boundary imports
this package. The demo is a deployment posture, not a mode the engine knows
about, and the flag-off identity tests in ``tests/test_demo_mode.py`` are the
statement of that.
"""

from engine.demo.mode import (
    BANNER_CLOSED,
    BANNER_TOKEN_GATED,
    DEMO_MODE_ENV,
    INGEST_CLOSED_DETAIL,
    INGEST_HEADER,
    INGEST_TOKEN_DETAIL,
    INGEST_TOKEN_ENV,
    REPO_URL_ENV,
    REPO_URL_PLACEHOLDER,
    DemoPosture,
    IngestVerdict,
    demo_posture,
)

__all__ = [
    "BANNER_CLOSED",
    "BANNER_TOKEN_GATED",
    "DEMO_MODE_ENV",
    "INGEST_CLOSED_DETAIL",
    "INGEST_HEADER",
    "INGEST_TOKEN_DETAIL",
    "INGEST_TOKEN_ENV",
    "REPO_URL_ENV",
    "REPO_URL_PLACEHOLDER",
    "DemoPosture",
    "IngestVerdict",
    "demo_posture",
]
