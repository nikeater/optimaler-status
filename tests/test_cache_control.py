"""Yesterday's stylesheet must never style today's page (part 17b).

The app sent no ``Cache-Control`` header at all, and "no header" is not "do not
cache": the static mount sends ``ETag`` and ``Last-Modified``, which entitles a
browser to HEURISTIC caching - a freshness window it invents from the file's
age, during which it reuses ``/static/system.css`` without asking. That is not
a hypothetical. On the day part 17 shipped, a browser rendered the new markup
against the previous deploy's stylesheet: a page that exists in no commit.

So this module measures the header matrix rather than the middleware. Every
kind of response the app can produce is asked for by HTTP and has to answer
``no-cache``:

* a rendered page, in BOTH postures - the header is not demo surface;
* the static mount, which is where the defect actually lived;
* ``/healthz``, which a container healthcheck polls forever;
* a 404, which comes from the exception handler and never reaches a route;
* the ``?lang=`` 303, which is returned by a middleware that short-circuits
  before routing - the case that pins WHERE in the stack this must sit, since
  an inner middleware would never see that response.

And the economy the choice rests on: a conditional request against the static
mount answers 304 with an empty body and still says ``no-cache``. That is the
whole argument for ``no-cache`` over ``no-store`` - revalidation costs a round
trip and no payload, so nothing is bought by forbidding the browser to store
what it already has.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from api.app import NO_CACHE, create_app
from api.metrics import set_demo_posture
from engine.config_loader import ConfigBundle
from engine.demo import DEMO_MODE_ENV, INGEST_TOKEN_ENV, DemoPosture
from engine.demo import demo_posture as posture_cache
from engine.draft import InMemoryDraftStore
from engine.journal import InMemoryJournalStore
from engine.notify import InMemoryOutbox
from engine.redact import InMemoryVaultStore, text_seal_detector

TOKEN = "cache-control-token"

#: A page, the static mount, the healthcheck and an error - on a demo instance.
DEMO_MATRIX = (
    ("/", 200),
    ("/static/system.css", 200),
    ("/healthz", 200),
    ("/keine-solche-seite", 404),
)

#: The same four kinds with the flag off, where the landing page IS the 404 and
#: the rendered page is the caseworker overview.
PLAIN_MATRIX = (
    ("/review", 200),
    ("/static/system.css", 200),
    ("/healthz", 200),
    ("/", 404),
)


@pytest.fixture(autouse=True)
def restore_posture() -> Iterator[None]:
    """Leave the process the way it was found (the part-11 fixture)."""
    yield
    posture_cache.cache_clear()
    set_demo_posture(DemoPosture())


def build_app(
    config: ConfigBundle,
    monkeypatch: pytest.MonkeyPatch,
    *,
    demo: bool = True,
) -> FastAPI:
    """The real app, on in-memory stores, with the deterministic union."""
    if demo:
        monkeypatch.setenv(DEMO_MODE_ENV, "1")
        monkeypatch.setenv(INGEST_TOKEN_ENV, TOKEN)
    else:
        monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
    return create_app(
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        text_detector=text_seal_detector(with_ner=False),
        outbox=InMemoryOutbox(),
        drafts=InMemoryDraftStore(),
    )


def build_client(
    config: ConfigBundle,
    monkeypatch: pytest.MonkeyPatch,
    *,
    demo: bool = True,
) -> TestClient:
    return TestClient(build_app(config, monkeypatch, demo=demo))


@pytest.mark.parametrize(("path", "status"), DEMO_MATRIX)
def test_a_demo_instance_says_no_cache_on_every_kind_of_response(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, path: str, status: int
) -> None:
    """Page, stylesheet, healthcheck, error - one policy, no exceptions."""
    response = build_client(config, monkeypatch).get(path)
    assert response.status_code == status, path
    assert response.headers["cache-control"] == NO_CACHE, path


@pytest.mark.parametrize(("path", "status"), PLAIN_MATRIX)
def test_the_header_is_there_with_the_flag_off_too(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, path: str, status: int
) -> None:
    """How long a browser may keep a response is not a demo posture.

    The ingest gate and the landing page are demo surface and are registered
    conditionally; this is not, and is registered unconditionally, so an
    operator running the app with the flag off gets the same guarantee.
    """
    response = build_client(config, monkeypatch, demo=False).get(path)
    assert response.status_code == status, path
    assert response.headers["cache-control"] == NO_CACHE, path


@pytest.mark.parametrize("demo", [True, False])
def test_the_language_redirect_carries_it_although_no_route_ran(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    """The case that pins where in the middleware stack this sits.

    ``?lang=`` is answered by a middleware that returns a 303 WITHOUT calling
    the route below it. A cache-control middleware registered anywhere inside
    that one would never see the response, and the redirect would go out with
    no policy on it. Checked in both postures because the language switch, like
    the header itself, is registered unconditionally.
    """
    client = build_client(config, monkeypatch, demo=demo)
    path = "/" if demo else "/review"
    switched = client.get(f"{path}?lang=en", follow_redirects=False)
    assert switched.status_code == 303
    assert switched.headers["location"] == path
    assert switched.headers["cache-control"] == NO_CACHE


def test_a_conditional_request_is_a_304_that_still_says_no_cache(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The revalidation economy, which is the whole case against ``no-store``.

    ``no-cache`` tells the browser to keep the bytes and ask before using them.
    That is only cheap if asking is cheap, so this pins what the static mount
    does with the answer: an ``If-None-Match`` carrying the ETag of the copy
    the browser already has comes back 304 with an EMPTY body - and carrying
    the header again, so the next visit revalidates as well. ``no-store`` would
    turn each of these into a full download of a stylesheet that never changed.
    """
    client = build_client(config, monkeypatch)
    first = client.get("/static/system.css")
    assert first.status_code == 200
    assert first.headers["cache-control"] == NO_CACHE
    etag = first.headers["etag"]

    again = client.get("/static/system.css", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["cache-control"] == NO_CACHE

    # The other conditional a browser sends, from the Last-Modified it stored.
    dated = client.get(
        "/static/system.css",
        headers={"If-Modified-Since": first.headers["last-modified"]},
    )
    assert dated.status_code == 304
    assert dated.headers["cache-control"] == NO_CACHE


def test_a_response_that_states_its_own_policy_keeps_it(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``setdefault``, not an assignment, and the difference is checkable.

    No response in this app sets the header today. The one that some day needs
    a policy of its own - an export whose URL already carries a content hash,
    say - must be able to state it without editing the middleware, so the
    middleware fills a gap rather than overwriting a decision. The route below
    exists only in this test; nothing registers it in the app.
    """
    app = build_app(config, monkeypatch)

    @app.get("/x-eigene-policy")
    def own_policy() -> Response:
        return Response("frozen", headers={"cache-control": "public, max-age=31536000"})

    stated = TestClient(app).get("/x-eigene-policy")
    assert stated.status_code == 200
    assert stated.headers["cache-control"] == "public, max-age=31536000"
