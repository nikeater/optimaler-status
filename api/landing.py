"""The demo landing page and the disclaimer page it points at.

Both registered only when ``EINGANGSLOTSE_DEMO_MODE=1`` (``engine/demo/mode.py``).
Outside demo mode ``GET /`` stays a 404 exactly as it has been since part 01,
and ``GET /hinweise`` is not in the route table at all - a landing page and a
notice about a demonstration instance are things a PUBLIC instance needs, and a
developer running the app locally has the OpenAPI docs.

Same rule as every other page module here: nothing on either page is computed
from state. The two facts they do state - which corpus the demo was seeded from
and whether ingest is reachable at all - come from the posture and the gold
directory, and both of them have to be true or the page is a lie on the one
screen a visitor is most likely to read.

**Part 16 split the two.** The landing page opens with the animated pipeline
hero and says what the system is; the disclaimer page carries what the banner
used to repeat above every screen - the deployment's own notice verbatim, the
reset model, the ingest posture, the licence and the accessibility summary. The
ribbon links it from every demo page.

The notes below are TRANSLATION KEYS rather than sentences. The view decides
which posture applies; the template decides which language to say it in.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.i18n import PageContext
from api.metrics import render_template
from engine.demo import DemoPosture

#: What the pages say about ingest when the instance accepts nothing at all.
INGEST_CLOSED_NOTE = "landing.ingest.closed"

#: And when a deployment kept a token-gated ingest open.
INGEST_TOKEN_NOTE = "landing.ingest.token"


@dataclass(frozen=True)
class LandingView:
    """Everything the landing template renders."""

    repo_url: str
    gold_dir: str
    ingest_note_key: str


@dataclass(frozen=True)
class HinweiseView:
    """Everything the disclaimer page renders.

    It carries no sentence of its own: the deployment's notice comes off the
    posture (``demo.banner``, a Jinja global), and every other line is a
    translation key resolved by the template.
    """

    repo_url: str
    gold_dir: str
    ingest_note_key: str


def _ingest_note(posture: DemoPosture) -> str:
    return INGEST_TOKEN_NOTE if posture.ingest_token else INGEST_CLOSED_NOTE


def build_view(posture: DemoPosture, *, gold_dir: str) -> LandingView:
    """The landing view for this posture."""
    return LandingView(
        repo_url=posture.repo_url,
        gold_dir=gold_dir,
        ingest_note_key=_ingest_note(posture),
    )


def build_hinweise_view(posture: DemoPosture, *, gold_dir: str) -> HinweiseView:
    """The disclaimer view for this posture."""
    return HinweiseView(
        repo_url=posture.repo_url,
        gold_dir=gold_dir,
        ingest_note_key=_ingest_note(posture),
    )


def render_page(view: LandingView, page: PageContext | None = None) -> str:
    """The whole landing page."""
    return render_template("landing.html", view, page)


def render_hinweise(view: HinweiseView, page: PageContext | None = None) -> str:
    """The whole disclaimer page."""
    return render_template("hinweise.html", view, page)
