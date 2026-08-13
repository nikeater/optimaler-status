"""The demo landing page: one screenful of what this is, and what it is not.

Registered only when ``EINGANGSLOTSE_DEMO_MODE=1`` (``engine/demo/mode.py``).
Outside demo mode ``GET /`` stays a 404 exactly as it has been since part 01 -
a landing page is a thing a PUBLIC instance needs, and a developer running the
app locally has the OpenAPI docs.

Same rule as every other page module here: nothing on this page is computed
from state. The two facts it does state - which corpus the demo was seeded from
and whether ingest is reachable at all - come from the posture and the gold
directory, and both of them have to be true or the page is a lie on the one
screen a visitor is most likely to read.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.metrics import environment
from api.review import PICKER_NOTE
from engine.demo import DemoPosture

#: What the page says about ingest when the instance accepts nothing at all.
#: Plain text, no markup: the template escapes it, as it escapes everything.
INGEST_CLOSED_NOTE = (
    "Der Eingang ist gesperrt: POST /ingest antwortet mit 403, ohne die "
    "Anfrage zu lesen. Diese Instanz kann keinen Antrag entgegennehmen - auch "
    "keinen echten, versehentlich abgeschickten."
)

#: And when a deployment kept a token-gated ingest open.
INGEST_TOKEN_NOTE = (
    "Der Eingang ist nur mit dem Token dieser Bereitstellung erreichbar "
    "(Kopfzeile X-Ingest-Token); ohne Token antwortet POST /ingest mit 403. "
    "Senden Sie auch mit Token keine echten Daten: der Ablagespeicher dieser "
    "Demo ist unverschluesselt."
)


@dataclass(frozen=True)
class LandingView:
    """Everything the landing template renders."""

    repo_url: str
    gold_dir: str
    ingest_note: str
    picker_note: str = PICKER_NOTE


def build_view(posture: DemoPosture, *, gold_dir: str) -> LandingView:
    """The landing view for this posture."""
    return LandingView(
        repo_url=posture.repo_url,
        gold_dir=gold_dir,
        ingest_note=(INGEST_TOKEN_NOTE if posture.ingest_token else INGEST_CLOSED_NOTE),
    )


def render_page(view: LandingView) -> str:
    """The whole page."""
    return environment().get_template("landing.html").render(view=view)
