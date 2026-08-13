"""The public-demo posture: env-gated, default OFF, and observable nowhere else.

A hosted demonstration of this system is a URL that any stranger can POST to,
and ``POST /ingest`` seals whatever it is given into a vault whose development
backend is plaintext JSONL. Collecting one real Versicherungsnummer that way
would be a data-protection incident with a public address, so the demo posture
is a first-class part of the deployment rather than a note in a README.

Three things switch on together, and only when ``EINGANGSLOTSE_DEMO_MODE=1``:

* **ingest closes.** With no ``EINGANGSLOTSE_INGEST_TOKEN`` set, ``POST /ingest``
  is refused outright - the demo is then read-only over the seeded synthetic
  state and there is no way in. With a token set, a caller who presents it in
  the ``X-Ingest-Token`` header gets the normal pipeline; everyone else gets 403
  with the semantics spelled out in the response body.
* **every rendered page carries a banner** saying the data is synthetic, the
  instance is a demo, submissions are off and the state resets on restart.
* **``GET /`` becomes a landing page** instead of a 404.

What does NOT switch on is any change to the review actions. Confirm, re-route
and escalate stay enabled in demo mode: they are the product, they append to the
journal like everywhere else, and a restart wipes the state anyway, which is
what makes them harmless here.

**With the flag off nothing observable changes.** The posture object is still
built and still handed to the templates, and every branch it guards renders
exactly zero bytes; ``tests/test_demo_mode.py`` asserts that against the same
templates rendered with the demo include neutralised away.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

#: Read once at app startup. Anything other than ``1`` leaves the posture off -
#: a deployment that means to open a demo has to say so exactly.
DEMO_MODE_ENV = "EINGANGSLOTSE_DEMO_MODE"

#: The shared secret that re-opens ``POST /ingest`` on a demo instance. Unset is
#: the safe state and means "no submission can be accepted at all".
INGEST_TOKEN_ENV = "EINGANGSLOTSE_INGEST_TOKEN"

#: Where the repository lives, for the landing page's link. A placeholder until
#: the user has actually created the repository (docs/PUBLISHING.md).
REPO_URL_ENV = "EINGANGSLOTSE_REPO_URL"

#: Header the ingest token travels in. A header rather than a query parameter:
#: a token in a URL ends up in every access log and every browser history.
INGEST_HEADER = "X-Ingest-Token"

REPO_URL_PLACEHOLDER = "https://github.com/OWNER/eingangslotse"

#: What every page says in demo mode when ingest is fully closed. German, with
#: one English sentence, because the audience of a public demo is both.
BANNER_CLOSED = (
    "Demo-Instanz mit ausschliesslich SYNTHETISCHEN Daten. Kein Vorgang auf "
    "diesen Seiten gehoert zu einer echten Person: jede Versicherungsnummer, "
    "jeder Name und jede Anschrift ist erzeugt. Der Eingang ist gesperrt - "
    "diese Instanz nimmt keine Antraege entgegen -, und der gesamte "
    "Datenbestand wird bei jedem Neustart geloescht und neu aufgebaut. "
    "This is a public demonstration instance: all data is synthetic, "
    "submissions are disabled, and the state is reset on every restart."
)

#: The same banner for an instance that kept a token-gated ingest open. The
#: difference is not cosmetic: "no submissions accepted" would be false here.
BANNER_TOKEN_GATED = (
    "Demo-Instanz mit ausschliesslich SYNTHETISCHEN Daten. Kein Vorgang auf "
    "diesen Seiten gehoert zu einer echten Person: jede Versicherungsnummer, "
    "jeder Name und jede Anschrift ist erzeugt. Der Eingang ist nur mit "
    "Token erreichbar - senden Sie hier keine echten Daten -, und der "
    "gesamte Datenbestand wird bei jedem Neustart geloescht und neu "
    "aufgebaut. This is a public demonstration instance: all data is "
    "synthetic, ingest is token-gated, and the state is reset on every "
    "restart."
)

#: The 403 body when the instance accepts no submission at all.
INGEST_CLOSED_DETAIL = (
    "ingest is disabled on this demonstration instance: no ingest token is "
    "configured, so no submission can be accepted by any caller. The demo is "
    "read-only over a synthetic corpus. Run the project locally to use "
    "POST /ingest (see README quickstart)."
)

#: The 403 body when a token exists and the caller did not present it.
INGEST_TOKEN_DETAIL = (
    "ingest is token-gated on this demonstration instance: present the "
    f"deployment's token in the {INGEST_HEADER} header. Submissions are "
    "sealed into a vault whose demo backend is plaintext, so this instance "
    "must never receive real personal data."
)


@dataclass(frozen=True)
class IngestVerdict:
    """Whether this caller may ingest, and what to tell them if not."""

    allowed: bool
    detail: str = ""


#: The one verdict the whole non-demo world gets, allocated once so the
#: default path allocates nothing per request.
INGEST_ALLOWED = IngestVerdict(allowed=True)


@dataclass(frozen=True)
class DemoPosture:
    """The demo switches, resolved from the environment exactly once.

    Frozen and passed around rather than re-read: a posture that could change
    between the banner and the ingest check would be a posture that says one
    thing and does another.
    """

    enabled: bool = False
    ingest_token: str = ""
    repo_url: str = REPO_URL_PLACEHOLDER

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DemoPosture:
        """Build the posture. Absent, empty and anything but ``1`` are OFF."""
        source = os.environ if environ is None else environ
        return cls(
            enabled=source.get(DEMO_MODE_ENV, "").strip() == "1",
            ingest_token=source.get(INGEST_TOKEN_ENV, "").strip(),
            repo_url=source.get(REPO_URL_ENV, "").strip() or REPO_URL_PLACEHOLDER,
        )

    @property
    def ingest_open(self) -> bool:
        """True when a submission can reach the pipeline at all."""
        return not self.enabled or bool(self.ingest_token)

    @property
    def banner(self) -> str:
        """The synthetic-data banner, or the empty string outside demo mode."""
        if not self.enabled:
            return ""
        return BANNER_TOKEN_GATED if self.ingest_token else BANNER_CLOSED

    def check_ingest(self, presented: str | None) -> IngestVerdict:
        """May this caller POST a submission?

        Compared with :func:`hmac.compare_digest` rather than ``==``: the
        comparison is over a shared secret on a public endpoint, and a
        short-circuiting comparison there is a timing oracle. Cheap, and the
        habit is worth more than the microseconds.
        """
        if not self.enabled:
            return INGEST_ALLOWED
        if not self.ingest_token:
            return IngestVerdict(allowed=False, detail=INGEST_CLOSED_DETAIL)
        if presented is not None and hmac.compare_digest(presented, self.ingest_token):
            return INGEST_ALLOWED
        return IngestVerdict(allowed=False, detail=INGEST_TOKEN_DETAIL)


@lru_cache(maxsize=1)
def demo_posture() -> DemoPosture:
    """The process-wide posture, read from the environment on first use.

    Cached because the ruling is "read once at app startup": an operator who
    edits the environment of a running process has not changed the posture of
    the app that is serving. ``create_app`` clears the cache and re-reads, which
    is what makes a test that sets the variable and builds an app behave the way
    a reader expects.
    """
    return DemoPosture.from_env()
