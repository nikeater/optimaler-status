from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from api import demo as demo_view
from api import inbox as inbox_view
from api import landing as landing_view
from api import review as review_view
from api.i18n import (
    LANG_COOKIE,
    LANG_COOKIE_MAX_AGE,
    LANG_PARAM,
    LANGUAGES,
    PageContext,
    resolve_language,
    strip_language,
)
from api.metrics import (
    STATIC_DIR,
    current_view,
    render_page,
    render_panel,
    set_demo_posture,
)
from engine.config_loader import ConfigBundle, load_config
from engine.demo import INGEST_HEADER, DemoPosture, demo_posture
from engine.demo import gegenpartei as gegenpartei_form
from engine.demo.personas import (
    CHANNEL_EMAIL,
    CHANNEL_FORM,
    DEMO_SUBMISSION_PREFIX,
    build_form_submission,
    build_letter_submission,
    demo_personas,
)
from engine.demo.store import DemoStore, DemoSubmission, new_token
from engine.dispatch import dispatch_dir
from engine.draft import DraftStore, default_draft_store, draft_case
from engine.draft.projection import DraftOutcome, facts_from
from engine.extract import TextExtractor, build_extractor
from engine.journal import (
    InMemoryJournalStore,
    JournalStore,
    JsonlJournalStore,
    derive_case_state,
)
from engine.notify import Outbox, default_outbox, notify_case
from engine.notify.projection import NotifyOutcome
from engine.pipeline import PipelineResult, run_pipeline
from engine.redact import (
    Detector,
    InMemoryVaultStore,
    JsonlVaultStore,
    RedactionRefusedError,
    VaultStore,
    text_seal_detector,
)
from engine.review import (
    OVERRIDE_ESCALATION,
    OVERRIDE_TIER,
    ConfirmOutcome,
    ReviewActionError,
    confirm_case,
    escalate_case,
    override_case,
)
from eval.harness import DEFAULT_GOLD_DIR

JOURNAL_DIR_ENV = "EINGANGSLOTSE_JOURNAL_DIR"
VAULT_DIR_ENV = "EINGANGSLOTSE_VAULT_DIR"

#: Set to "0" to seal free text with the deterministic union alone. Default on:
#: prose is where bare person names live, and no regular expression finds them.
TEXT_NER_ENV = "EINGANGSLOTSE_TEXT_NER"

#: The caching policy every response carries (part 17b). Deliberately NOT
#: ``no-store``: what this closes is the heuristic freshness window, not the
#: browser's disk. See :func:`_mount_cache_control`.
CACHE_CONTROL_HEADER = "cache-control"
NO_CACHE = "no-cache"

INVALID_SUBMISSION = "invalid submission"

#: What the intake page says when the boundary refused the submission. A real
#: behaviour and the strongest one this system has: nothing was journaled, no
#: case exists, and the page says which KINDS were still findable and where -
#: never the residue itself. A translation key since part 16: the sentence is
#: the same refusal in either language.
REFUSED_REDACTION = demo_view.REFUSED_REDACTION_KEY

#: And when the envelope itself did not validate.
REFUSED_ENVELOPE = demo_view.REFUSED_ENVELOPE_KEY


@dataclass(frozen=True)
class IngestOutcome:
    """Everything one ingest produced: the run, the receipts, the letters."""

    result: PipelineResult
    notified: NotifyOutcome
    drafted: DraftOutcome


def default_journal() -> JournalStore:
    """In-memory store, or a JSONL store when the env var points somewhere."""
    directory = os.environ.get(JOURNAL_DIR_ENV)
    if directory:
        return JsonlJournalStore(directory)
    return InMemoryJournalStore()


def default_vault() -> VaultStore:
    """In-memory vault, or a file-backed one when the env var points somewhere.

    Mirrors :func:`default_journal` deliberately: the two stores have the same
    lifecycle and the same production replacement, and an operator should not
    have to learn two conventions.
    """
    directory = os.environ.get(VAULT_DIR_ENV)
    if directory:
        return JsonlVaultStore(directory)
    return InMemoryVaultStore()


@lru_cache(maxsize=1)
def default_text_detector() -> Detector:
    """The union that seals free text in PRODUCTION (ADR-019, ruling 2).

    This is the one place where the running service is deliberately STRONGER
    than the gate. The gate seals prose with the deterministic recognizers alone,
    because a gate whose value depends on whether an optional wheel is installed
    is not a gate; the corpus generator asserts at build time that deterministic
    sealing leaves every gold letter verification-clean, so nothing is being
    hidden by that choice. A real letter is a different matter: it carries bare
    person names in the middle of a sentence, which no regular expression finds,
    so production adds the model member when the ``[redact]`` extra is there.

    Cached, because building it loads a spaCy model. ``EINGANGSLOTSE_TEXT_NER=0``
    turns it off for an operator who wants the two paths identical.
    """
    return text_seal_detector(with_ner=os.environ.get(TEXT_NER_ENV, "1") != "0")


def sanitize_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Reduce pydantic errors to location and type. Never the input value.

    ``ValidationError.errors()`` carries ``input`` (the offending value) and
    ``msg`` (which quotes it for several error types), so neither survives this
    function. What remains - where in the document, and which rule - is what a
    caller needs to fix a submission and is not about a person.
    """
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):  # pragma: no cover - defensive
            continue
        location = error.get("loc", ())
        sanitized.append(
            {
                "loc": [str(part) for part in location],
                "type": str(error.get("type", "unknown")),
            }
        )
    return sanitized


def page_context(request: Request) -> PageContext:
    """The language this request is answered in, and the URL to come back to.

    Read from the cookie only. The ``?lang=`` parameter never reaches a route:
    :func:`_mount_language` intercepts it, writes the cookie and redirects, so
    by the time a page renders there is exactly one place the language can come
    from. ``here`` is the current path with its query minus that parameter,
    which is what the header's two toggle links append to.
    """
    return PageContext(
        lang=resolve_language(request.cookies.get(LANG_COOKIE)),
        here=strip_language(request.url.path, request.url.query),
    )


def create_app(
    config: ConfigBundle | None = None,
    journal: JournalStore | None = None,
    vault: VaultStore | None = None,
    text_detector: Detector | None = None,
    outbox: Outbox | None = None,
    drafts: DraftStore | None = None,
    live_extractor: TextExtractor | None = None,
) -> FastAPI:
    """Build the app; tests inject their own config, journal, vault and union."""
    bundle = config or load_config()
    store = journal or default_journal()
    identity_vault = vault or default_vault()
    prose_detector = text_detector or default_text_detector()
    applicant_outbox = outbox or default_outbox()
    draft_store = drafts or default_draft_store()
    # Which reader of prose this process runs (part 12), resolved ONCE, here,
    # for the same reason as the demo posture below: an extractor that could
    # change between two requests would be an extractor whose version stamp
    # lies about one of them. The default is replay and no endpoint is probed,
    # so a service configured for live mode still starts with the model off -
    # what a missing model costs is discards toward tier 3, per ADR-020.
    extractor = live_extractor or build_extractor(bundle)
    # The demo posture, read from the environment ONCE, here (part 11). Not
    # per request: a posture that could change between the banner and the
    # ingest check would be a posture that says one thing and does another.
    demo_posture.cache_clear()
    posture = demo_posture()
    set_demo_posture(posture)
    # The demo journey's TTL store (part 13). Constructed ONLY on a demo
    # instance: with the flag off there is no store object in this process at
    # all, which is what "no store exists" has to mean to be worth asserting.
    demo_store = DemoStore() if posture.enabled else None
    app = FastAPI(
        title="EingangsLotse",
        version="0.1.0",
        summary="Two-plane triage assistant (S1 walking skeleton)",
    )
    app.state.config = bundle
    app.state.journal = store
    app.state.vault = identity_vault
    app.state.outbox = applicant_outbox
    app.state.drafts = draft_store
    app.state.demo = posture
    app.state.demo_store = demo_store
    app.state.extractor = extractor

    @app.exception_handler(RequestValidationError)
    async def _request_validation(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """FastAPI's own 422, with the echoed request body removed.

        The default handler returns every error with its ``input``, which for a
        malformed submission is the submission. That is a redaction leak in the
        one code path nobody reads, so it is replaced rather than trusted.
        """
        return JSONResponse(
            status_code=422,
            content={
                "detail": INVALID_SUBMISSION,
                "errors": sanitize_errors(error.errors()),
            },
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "versions": bundle.version_stamp().model_dump(mode="json"),
            "redaction_policy_id": bundle.redaction.policy_id,
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """The container healthcheck. Deliberately the emptiest route here.

        ``/health`` already exists and answers a different question - which
        config versions is this process running - by touching the config
        bundle. A container healthcheck runs every few seconds forever and must
        stay a constant: an orchestrator that restarts a service because a YAML
        loader got slow is an orchestrator making an outage out of nothing.
        """
        return {"status": "ok"}

    def run_ingest(payload: Mapping[str, Any]) -> IngestOutcome:
        """The whole of what ``POST /ingest`` does, as one callable.

        Extracted (part 13) so the demo intake page can be the AUTHORIZED
        SERVER-SIDE CALLER of exactly this machinery rather than a second
        implementation of it. There is one sealing path, one validation path
        and one journal; a demo that ran its own would be a demo of its own.
        """
        result = run_pipeline(
            payload,
            config=bundle,
            journal=store,
            vault=identity_vault,
            text_detector=prose_detector,
            live_extractor=extractor,
        )
        case_id = result.decision.case_id
        # The projection worker, inline and after the fact: the journal is
        # already complete for this case, so the fold sees RECEIVED and ROUTED
        # and owes exactly the two ADR-005 messages. A second POST of the same
        # submission re-derives the same notifications and sends neither.
        notified = notify_case(
            store.read(case_id),
            config=bundle,
            journal=store,
            outbox=applicant_outbox,
        )
        # Drafting, after the decision and after the receipt (ADR-003, ADR-023).
        # Also inline, also a fold over the journal the pipeline just wrote -
        # but this one reads the vault, so what it produces is PII-bearing and
        # goes into the draft store rather than into any response.
        drafted = draft_case(
            store.read(case_id),
            config=bundle,
            journal=store,
            vault=identity_vault,
            drafts=draft_store,
            facts=facts_from(result.extractions),
        )
        return IngestOutcome(result=result, notified=notified, drafted=drafted)

    @app.post("/ingest", status_code=201)
    def ingest(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            outcome = run_ingest(payload)
        except ValidationError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": INVALID_SUBMISSION,
                    "errors": sanitize_errors(
                        error.errors(include_input=False, include_url=False)
                    ),
                },
            ) from error
        except RedactionRefusedError as error:
            # The boundary could not verify the working copy clean. Nothing was
            # journaled, no case exists, and the response names kinds and paths
            # but never the residue it found.
            raise HTTPException(status_code=422, detail=error.as_payload()) from error
        result, notified, drafted = outcome.result, outcome.notified, outcome.drafted
        decision = result.decision
        return {
            "case_id": decision.case_id,
            "envelope_id": decision.envelope_id,
            "procedure_id": result.procedure_id,
            "tier": int(decision.tier),
            "pre_downgrade_tier": int(decision.pre_downgrade_tier),
            "routed_unit_id": decision.routed_unit_id,
            "clear_cut": result.clear_cut,
            "completeness_verdict": result.evidence.completeness.verdict.value,
            "gaps": [
                {"requirement_id": gap.requirement_id, "status": gap.status.value}
                for gap in result.evidence.completeness.gaps
            ],
            "reasons": [
                {
                    "kind": reason.kind.value,
                    "rule_id": reason.rule_id,
                    "detail": reason.detail,
                }
                for reason in decision.reasons
            ],
            "redaction_verified": result.envelope.redaction_verified,
            "notifications": [
                {
                    "template_id": event.template_id,
                    "informational_only": event.informational_only,
                }
                for event in notified.events
            ],
            # Which drafts exist, never what they say: the letters carry the
            # applicant's re-hydrated identity and live behind /drafts/{case_id}.
            "drafts": [
                {
                    "draft_id": record.draft_id,
                    "kind": record.kind,
                    "template_id": record.template_id,
                    "resolved_tokens": record.resolved_tokens,
                }
                for record in drafted.drafts
            ],
        }

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics(request: Request) -> HTMLResponse:
        """The metrics panel; 200 with a refresh hint when no report exists."""
        return HTMLResponse(render_page(current_view(), page_context(request)))

    @app.get("/metrics/panel", response_class=HTMLResponse)
    def metrics_panel(request: Request) -> HTMLResponse:
        """The panel fragment htmx swaps in; identical markup, no chrome."""
        return HTMLResponse(render_panel(current_view(), page_context(request)))

    @app.get("/cases/{case_id}")
    def read_case(case_id: str) -> dict[str, Any]:
        events = store.read(case_id)
        if not events:
            raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
        state = derive_case_state(case_id, events)
        return {
            "case_id": case_id,
            "state": state.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
        }

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox(request: Request) -> HTMLResponse:
        """The simulated applicant inbox: everything that was delivered.

        Read-only, and there is no route that is not. A notification on this
        path never passes a human (ADR-005), so a send or edit control here
        would quietly turn a Realakt into something a caseworker approved.
        """
        return HTMLResponse(
            inbox_view.render_page(
                inbox_view.build_view(applicant_outbox), page_context(request)
            )
        )

    @app.get("/inbox/{case_id}")
    def read_inbox(case_id: str) -> dict[str, Any]:
        """One case's messages as JSON, for the demo and for tests."""
        entries = applicant_outbox.entries(case_id)
        if not entries:
            raise HTTPException(
                status_code=404, detail=f"no notification for case: {case_id}"
            )
        return {
            "case_id": case_id,
            "notifications": [inbox_view.as_payload(entry) for entry in entries],
        }

    @app.get("/drafts/{case_id}")
    def read_drafts(case_id: str, unit: str | None = None) -> dict[str, Any]:
        """One case's prepared letters, with their text. Behind the unit picker.

        **This route returns re-hydrated identity data** - that is what a draft
        is - which makes it the only API surface in the project that does, and
        part 10 is where it stops being open. A caller must name an
        organizational unit the taxonomy knows; anything else is 403. That is
        the DEMO form of the Berechtigungskonzept, not its implementation:
        there is no identity provider here, the unit is a query parameter, and
        a real deployment replaces this check with the agency's IdP before any
        real data exists (C-5, pilot scope). The same deployment questions
        apply to the draft store as to the vault (encryption at rest,
        retention, erasure - docs/vault-dpia-input.md).

        Read-only like ``/inbox``, and for a stronger reason: a confirm or send
        control here would turn a prepared draft into a dispatched
        Verwaltungsakt without the human step ADR-003 exists to guarantee. The
        confirm step lives on the review UI, and it journals.
        """
        if review_view.resolve_unit(bundle, unit) is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "prepared letters carry re-hydrated identity data; name an "
                    "organizational unit with ?unit=<unit_id> (demo role model, "
                    "C-5: a real deployment puts an identity provider here)"
                ),
            )
        records = draft_store.records(case_id)
        if not records:
            raise HTTPException(status_code=404, detail=f"no draft for case: {case_id}")
        return {
            "case_id": case_id,
            "note": "prepared for human confirmation; nothing here has been "
            "dispatched, and nothing here is a Verwaltungsakt",
            "drafts": [record.model_dump(mode="json") for record in records],
        }

    _mount_review(
        app,
        bundle=bundle,
        journal=store,
        vault=identity_vault,
        drafts=draft_store,
        demo_store=demo_store,
    )
    _mount_language(app)
    _mount_ingest_gate(app, posture=posture)
    _mount_landing(app, posture=posture)
    _mount_demo_journey(
        app,
        posture=posture,
        bundle=bundle,
        journal=store,
        outbox=applicant_outbox,
        demo_store=demo_store,
        run_ingest=run_ingest,
    )
    # LAST, and the position is the design: Starlette builds the stack so that
    # the middleware registered last is the outermost one, which is the only
    # place from which a header reaches the responses the middleware above
    # return WITHOUT calling a route - the language 303 and the ingest 403.
    _mount_cache_control(app)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def _mount_language(app: FastAPI) -> None:
    """``?lang=`` sets the cookie and redirects back (part 16).

    Middleware and not a per-route parameter, for two reasons. Every HTML route
    would otherwise have to grow the same three lines and the same branch
    returning a redirect instead of a page - eleven copies of one decision, and
    the twelfth route is the one somebody forgets. And a switch that lives
    before routing works on any URL the toggle can appear on, including the
    ones that carry their own query (``?unit=``, ``?persona=``, ``?highlight=``),
    which the redirect preserves verbatim.

    Registered UNCONDITIONALLY, unlike the ingest gate and the demo pages: the
    language machinery is not demo surface. What it does not do outside demo
    mode is link anything demo - that is the header's business, and it is
    gated there.

    303 rather than 302: the parameter has done its work, the browser must GET
    the clean URL, and a reload must not re-apply anything.
    """

    @app.middleware("http")
    async def language_cookie(request: Request, call_next: Any) -> Any:
        chosen = request.query_params.get(LANG_PARAM)
        if request.method != "GET" or chosen not in LANGUAGES:
            return await call_next(request)
        response = RedirectResponse(
            url=strip_language(request.url.path, request.url.query) or "/",
            status_code=303,
        )
        response.set_cookie(
            LANG_COOKIE,
            chosen,
            max_age=LANG_COOKIE_MAX_AGE,
            path="/",
            httponly=True,
            samesite="lax",
        )
        return response


def _mount_cache_control(app: FastAPI) -> None:
    """``Cache-Control: no-cache`` on every response (part 17b).

    The app sent no caching header at all until this existed, and "no header"
    does not mean "do not cache". The static mount sends ``ETag`` and
    ``Last-Modified``, so a browser is entitled to HEURISTIC caching: it
    invents a freshness window from the file's age and reuses
    ``/static/system.css`` without asking anyone. Observed in production on the
    day part 17 shipped - a visitor's browser rendered the NEW markup with the
    PREVIOUS deploy's stylesheet, which is a page nobody wrote, nobody can
    reproduce from the repository and nobody can debug from the server logs.
    For a demonstration that redeploys up to the day it is judged, that is a
    defect class rather than a nuisance.

    ``no-cache`` and NOT ``no-store``. The two are easy to confuse and only one
    of them is right here: ``no-cache`` lets the browser keep the bytes and
    obliges it to REVALIDATE before every use, while ``no-store`` forbids
    keeping them at all. Since the static mount already answers a conditional
    request with a 304 and no body, revalidation costs one small round trip and
    a redeploy is picked up on the very next request; ``no-store`` would throw
    that economy away and buy nothing back. ``max-age`` is not added either -
    any value above zero is the window this exists to close - and no static URL
    is versioned, because a content-hashed asset pipeline is a build-step design
    decision and this defect does not require one.

    Registered UNCONDITIONALLY, like the language switch and unlike the ingest
    gate: how long a browser may keep a response is not demo surface. And
    registered LAST, which under Starlette's stack order makes it the OUTERMOST
    middleware, so it sees every response the app produces on the way out -
    rendered pages, the static mount, ``/healthz``, a 404, and the two
    short-circuits that never reach a route at all: the ``?lang=`` 303 and the
    demo instance's ingest 403.

    ``setdefault`` rather than an assignment: no response sets the header today,
    but the one that some day needs its own policy should be able to state it
    and keep it.
    """

    @app.middleware("http")
    async def cache_control(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault(CACHE_CONTROL_HEADER, NO_CACHE)
        return response


def _mount_ingest_gate(app: FastAPI, *, posture: DemoPosture) -> None:
    """Refuse ``POST /ingest`` on a demo instance BEFORE the body is read.

    Middleware and not a route dependency, and the difference is the whole
    point of the gate. FastAPI reads and JSON-decodes the request body before
    it solves a route's dependencies, so a dependency-based check refuses a
    stranger's submission only AFTER this process has parsed it - and on a
    malformed body with a JSON content type it never gets to run at all: the
    decode error becomes a 422 first. Middleware runs before routing, before
    the body is touched, which is what "this instance cannot receive real
    personal data" has to mean to be worth saying.

    Registered only in demo mode, so with the flag off the middleware stack is
    the part-10 middleware stack and the request path is unchanged.
    """
    if not posture.enabled:
        return

    @app.middleware("http")
    async def gate_ingest(request: Request, call_next: Any) -> Any:
        if request.method == "POST" and request.url.path == "/ingest":
            verdict = posture.check_ingest(request.headers.get(INGEST_HEADER))
            if not verdict.allowed:
                return JSONResponse(status_code=403, content={"detail": verdict.detail})
        return await call_next(request)


def _mount_landing(app: FastAPI, *, posture: DemoPosture) -> None:
    """Register ``GET /`` - but only on a demo instance (part 11, ruling 3).

    Registered conditionally rather than answering differently, so that "with
    the flag off nothing observable changes" holds at the level of the route
    table and not only at the level of a response body: outside demo mode this
    app has no ``/`` and returns the same 404 it has returned since part 01.
    """
    if not posture.enabled:
        return

    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request) -> HTMLResponse:
        """What this is, what it guarantees, and what this instance is not."""
        return HTMLResponse(
            landing_view.render_page(
                landing_view.build_view(posture, gold_dir=str(DEFAULT_GOLD_DIR)),
                page_context(request),
            )
        )

    @app.get("/hinweise", response_class=HTMLResponse)
    def hinweise(request: Request) -> HTMLResponse:
        """The disclaimer page the ribbon links from every demo page.

        Demo surface like the landing page and for the same reason: a notice
        about a demonstration instance is meaningless on an instance that is
        not one, and "with the flag off nothing observable changes" has to hold
        at the level of the route table.
        """
        return HTMLResponse(
            landing_view.render_hinweise(
                landing_view.build_hinweise_view(
                    posture, gold_dir=str(DEFAULT_GOLD_DIR)
                ),
                page_context(request),
            )
        )


def _mount_demo_journey(
    app: FastAPI,
    *,
    posture: DemoPosture,
    bundle: ConfigBundle,
    journal: JournalStore,
    outbox: Outbox,
    demo_store: DemoStore | None,
    run_ingest: Callable[[Mapping[str, Any]], IngestOutcome],
) -> None:
    """The three-phase guided showcase (part 13). Demo instances only.

    Registered conditionally, exactly like the landing page and for the same
    reason: outside demo mode there is no ``/demo`` in the route table, none in
    the OpenAPI document and no demo store in the process. "Nothing observable
    changes" has to hold at the level of what an integrator reads, not only at
    the level of a response body.

    **This is the authorized server-side caller of the token-gated ingest.** It
    presents the deployment's own token to the same :meth:`DemoPosture
    .check_ingest` the middleware calls, and then runs the app's own
    ``run_ingest``. It does not go around the gate: with no token configured
    the verdict is refused, the page says so in words, and no submission is
    read. What is different from a stranger's POST is who is holding the token,
    not what the gate does with it.
    """
    if not posture.enabled or demo_store is None:
        return
    personas = demo_personas()

    @app.get("/demo/rundgang", response_class=HTMLResponse)
    def demo_tour(request: Request) -> HTMLResponse:
        """The tour: the whole system in six steps, for a first-time visitor.

        Registered with the rest of the demo surface and therefore absent -
        route table and OpenAPI document alike - whenever the flag is off. It
        reads the journal once, for the seeded case it points at, and derives
        nothing else.
        """
        page = page_context(request)
        return HTMLResponse(
            demo_view.render_tour(
                demo_view.build_tour_view(
                    journal,
                    config=bundle,
                    posture=posture,
                    gold_dir=str(DEFAULT_GOLD_DIR),
                    page=page,
                ),
                page,
            )
        )

    @app.get("/demo/antrag", response_class=HTMLResponse)
    def demo_intake(
        request: Request, persona: str | None = None, kanal: str | None = None
    ) -> HTMLResponse:
        """Phase 1: pick a fictional applicant and edit their submission."""
        page = page_context(request)
        return HTMLResponse(
            demo_view.render_intake(
                demo_view.build_intake_view(
                    posture,
                    personas,
                    persona_id=persona,
                    channel=kanal,
                    config=bundle,
                    page=page,
                ),
                page,
            )
        )

    @app.post("/demo/antrag")
    async def demo_submit(request: Request) -> Response:
        """Phase 1 -> 2. One submission, through the one ingest path."""
        form = await form_fields(request)
        page = page_context(request)
        # The same fallback the GET side uses, so a submission that names no
        # persona lands on the one the page was showing (`demo_view
        # .LEAD_PERSONA`) rather than on the config file's first entry.
        chosen = personas.get(form.get("persona")) or demo_view.default_persona(
            personas
        )
        channel = demo_view.resolve_channel(form.get("kanal"))
        body = form.get("body", chosen.letter)

        def refused(message: str, details: Sequence[str] = ()) -> HTMLResponse:
            # ``message`` is a translation key for the two refusals this page
            # owns and a ready-made sentence for the posture's own 403 detail.
            # Both work: an unknown key resolves to itself (``api/i18n.py``),
            # which is exactly what a sentence that is already a sentence needs.
            return HTMLResponse(
                demo_view.render_intake(
                    demo_view.build_intake_view(
                        posture,
                        personas,
                        persona_id=chosen.persona_id,
                        channel=channel,
                        values=form,
                        body=body,
                        error_key=message,
                        error_details=details,
                        config=bundle,
                        page=page,
                    ),
                    page,
                ),
                status_code=200,
            )

        verdict = posture.check_ingest(posture.ingest_token)
        if not verdict.allowed:
            return refused(verdict.detail)
        now = datetime.now(UTC)
        submission_id = f"{DEMO_SUBMISSION_PREFIX}-{uuid4().hex[:12]}"
        # PART 20: the e-mail branch below is unreachable and deliberately
        # UNCHANGED. `demo_view.resolve_channel` offers one channel since the
        # user's decision of 2026-08-18, so `channel` is always the form here -
        # including for a POST that carries the old `kanal=email`, which is the
        # point. Nothing is deleted: uncommenting the chooser in
        # `ui/templates/demo_intake.html` and putting `CHANNEL_EMAIL` back into
        # `demo_view.OFFERED_CHANNELS` makes this glue live again as it stands.
        payload = (
            build_letter_submission(
                chosen, body, submission_id=submission_id, submitted_at=now.isoformat()
            )
            if channel == CHANNEL_EMAIL
            else build_form_submission(
                chosen, form, submission_id=submission_id, submitted_at=now.isoformat()
            )
        )
        try:
            outcome = run_ingest(payload)
        except RedactionRefusedError as error:
            # A real behaviour worth showing rather than hiding: the boundary
            # could not verify the working copy clean, so nothing was journaled
            # and no case exists. The findings name kinds and paths and never
            # the residue that caused them.
            return refused(REFUSED_REDACTION, _finding_lines(error))
        except ValidationError as error:  # pragma: no cover - see the comment
            # Defensive, and honestly so: this page BUILDS the envelope and the
            # visitor only fills leaf values, so no edit on the form can make
            # the submission malformed. The branch exists because `run_ingest`
            # is shared with `POST /ingest`, where a caller controls the whole
            # document - and because a 500 on a citizen-facing page is the one
            # answer this surface may never give. The reachable refusal is the
            # redaction one above, and it is tested.
            return refused(
                REFUSED_ENVELOPE,
                tuple(
                    f"{'.'.join(entry['loc'])}: {entry['type']}"
                    for entry in sanitize_errors(
                        error.errors(include_input=False, include_url=False)
                    )
                ),
            )
        demo_store.put(
            DemoSubmission.from_envelope(
                outcome.result.envelope,
                persona_id=chosen.persona_id,
                persona_label=chosen.display_name,
                channel=channel,
                created_at=now,
                echo=(
                    ()
                    if channel == CHANNEL_EMAIL
                    else demo_view.echo_values(chosen, form)
                ),
                echo_body=body if channel == CHANNEL_EMAIL else "",
            ),
            now=now,
        )
        # PART 19: par. 7a Abs. 4 SGB IV hears the other side, so a
        # Statusfeststellung that named an Auftraggeber earns a statement
        # request. Recorded in the RAM store and NOWHERE ELSE - no journal
        # event exists for it and inventing one would be a contract change
        # (ADR-036). `statement_request` reads the procedure the pipeline just
        # derived rather than deciding one, and answers None for every case
        # that has no second party to hear.
        hearing = gegenpartei_form.statement_request(
            chosen,
            form,
            token=new_token(),
            case_id=outcome.result.decision.case_id,
            procedure_id=outcome.result.procedure_id,
            now=now,
        )
        if hearing is not None:
            demo_store.request_statement(hearing, now=now)
        # 303, like every other POST here: the submission appended to the
        # journal, and a browser reload must not append a second one.
        return RedirectResponse(
            url=f"/demo/case/{outcome.result.decision.case_id}/pipeline",
            status_code=303,
        )

    @app.get("/demo/gegenpartei", response_class=HTMLResponse)
    def demo_gegenpartei(request: Request, zeichen: str | None = None) -> HTMLResponse:
        """The counterparty surface: the visitor answers as the Auftraggeber.

        A token this process does not hold is NOT a 404. The page it renders is
        the explanation of what this surface is plus the way to earn a request,
        which is the same "never half-select something, never a stack trace"
        rule the persona picker, the unit picker and the language switch follow
        - and it is also the only honest answer, because an expired request and
        a made-up one are indistinguishable here by design.
        """
        page = page_context(request)
        return HTMLResponse(
            demo_view.render_gegenpartei(
                demo_view.build_gegenpartei_view(
                    posture,
                    demo_store.link_by_token(zeichen or ""),
                    token=zeichen or "",
                    config=bundle,
                    page=page,
                ),
                page,
            )
        )

    @app.post("/demo/gegenpartei")
    async def demo_statement(request: Request) -> Response:
        """The statement, through the ONE ingest path the intake uses.

        No second sealing, no side channel and no privilege of its own: the
        same authorized server-side caller pattern, the same
        ``posture.check_ingest``, the same ``run_ingest``, one call. What the
        counterparty submits becomes its own case - sealed, redacted,
        span-verified, routed, journaled - and the demo store learns nothing
        about it except which two case ids belong together.
        """
        form = await form_fields(request)
        page = page_context(request)
        token = form.get(demo_view.GEGENPARTEI_PARAM, "")
        now = datetime.now(UTC)
        link = demo_store.link_by_token(token, now=now)
        if link is None or link.answered:
            # An expired request and an answered one both mean "there is
            # nothing to submit here", and both are 303 to the page that says
            # which: a POST that re-rendered a form would offer to send a
            # second statement for a request that already has one.
            return RedirectResponse(
                url=demo_view.gegenpartei_href(token), status_code=303
            )
        statement = gegenpartei_form.statement_form(link)
        body = form.get("body", gegenpartei_form.statement_prose(link))

        def refused(message: str, details: Sequence[str] = ()) -> HTMLResponse:
            return HTMLResponse(
                demo_view.render_gegenpartei(
                    demo_view.build_gegenpartei_view(
                        posture,
                        link,
                        token=token,
                        values=form,
                        body=body,
                        error_key=message,
                        error_details=details,
                        config=bundle,
                        page=page,
                    ),
                    page,
                ),
                status_code=200,
            )

        verdict = posture.check_ingest(posture.ingest_token)
        if not verdict.allowed:
            return refused(verdict.detail)
        payload = gegenpartei_form.build_statement_submission(
            statement,
            form,
            submission_id=(
                f"{gegenpartei_form.STATEMENT_SUBMISSION_PREFIX}-{uuid4().hex[:12]}"
            ),
            submitted_at=now.isoformat(),
            body=body,
        )
        try:
            outcome = run_ingest(payload)
        except RedactionRefusedError as error:
            return refused(REFUSED_REDACTION, _finding_lines(error))
        case_id = outcome.result.decision.case_id
        demo_store.put(
            DemoSubmission.from_envelope(
                outcome.result.envelope,
                persona_id=statement.persona_id,
                persona_label=statement.display_name,
                channel=CHANNEL_FORM,
                created_at=now,
                echo=demo_view.echo_values(statement, form),
            ),
            now=now,
        )
        demo_store.record_statement(
            token,
            statement_case_id=case_id,
            answers=gegenpartei_form.statement_answers(form),
            now=now,
        )
        return RedirectResponse(url=f"/demo/case/{case_id}/pipeline", status_code=303)

    @app.get("/demo/case/{case_id}/pipeline", response_class=HTMLResponse)
    def demo_pipeline(request: Request, case_id: str) -> HTMLResponse:
        """Phase 2: the seven stages, over the journal and nothing else."""
        page = page_context(request)
        view = demo_view.build_pipeline_view(
            journal,
            config=bundle,
            case_id=case_id,
            outbox=outbox,
            store=demo_store,
            page=page,
        )
        if view is None:
            raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
        return HTMLResponse(demo_view.render_pipeline(view, page))

    @app.get("/demo/case/{case_id}/backend", response_class=HTMLResponse)
    def demo_backend(request: Request, case_id: str) -> HTMLResponse:
        """Phase 2: the seven stages, over the journal and nothing else."""
        page = page_context(request)
        view = demo_view.build_backend_view(
            journal,
            config=bundle,
            case_id=case_id,
            outbox=outbox,
            store=demo_store,
            page=page,
        )
        if view is None:
            raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
        return HTMLResponse(demo_view.render_backend(view, page))



def _finding_lines(error: RedactionRefusedError) -> tuple[str, ...]:
    """A refusal's findings as sentences. Kind, place and length, never a value.

    Read from ``as_payload`` rather than from the report objects, because that
    method is the one the API's 422 already uses and it is value-free by
    construction (``engine/redact/verify.py``).
    """
    return tuple(
        f"{finding.get('kind', 'unbekannt')} an der Stelle "
        f"{finding.get('path') or 'unbekannt'} "
        f"({finding.get('length', 0)} Zeichen, Erkenner "
        f"{finding.get('recognizer_id', 'unbekannt')})"
        for finding in error.as_payload().get("findings", [])
    )


def _mount_review(
    app: FastAPI,
    *,
    bundle: ConfigBundle,
    journal: JournalStore,
    vault: VaultStore,
    drafts: DraftStore,
    demo_store: DemoStore | None = None,
) -> None:
    """The part-10 caseworker surface: three pages and three POST verbs.

    Registered from its own function because ``create_app`` was already the
    longest thing in this module, and because the boundary is worth seeing:
    everything below reads the journal and appends to it, and nothing below
    can touch the outbox. ``/inbox`` gains no control here and never will
    (ADR-005) - a notification that a caseworker approved would not be a
    Realakt any more.

    ``demo_store`` is None everywhere except on a demo instance and is read by
    exactly one line in this whole function (part 19): the case view's
    display-only cross-link to the other party's case. It reaches the case view
    as a resolved value rather than as a store, so nothing under ``/review``
    ever queries demo state; see :class:`api.review.StatementCrossLink`.
    """

    def statement_link(case_id: str) -> review_view.StatementCrossLink | None:
        """The other end of a two-party correlation, or None. Display only."""
        if demo_store is None:
            return None
        link = demo_store.link_for_case(case_id)
        if link is None:
            return None
        asked = case_id == link.case_id
        other = link.statement_case_id if asked else link.case_id
        if not other:
            # The request went out and nothing came back. There is no second
            # case to link, and the caseworker surface says nothing at all
            # rather than announcing an absence it does not act on either.
            return None
        return review_view.StatementCrossLink(
            case_id=other,
            asked=asked,
            requested_at=link.created_at,
            answered_at=link.answered_at,
        )

    def _redirect(case_id: str, unit: str | None, **extra: str) -> RedirectResponse:
        query = {"unit": unit or "", **extra}
        pairs = "&".join(
            f"{key}={quote(value)}" for key, value in query.items() if value
        )
        # 303: the POST is done, the browser must GET the case view. Anything
        # else re-submits the action on a reload, and this action appends.
        return RedirectResponse(
            url=f"/review/case/{case_id}" + (f"?{pairs}" if pairs else ""),
            status_code=303,
        )

    @app.get("/review", response_class=HTMLResponse)
    def review_overview(request: Request, unit: str | None = None) -> HTMLResponse:
        """The queue overview, the unit picker and the P-6 / P-10 numbers."""
        return HTMLResponse(
            review_view.render_overview(
                review_view.build_overview(
                    journal,
                    config=bundle,
                    unit_id=review_view.resolve_unit(bundle, unit),
                ),
                page_context(request),
            )
        )

    @app.get("/review/queue/{queue_id}", response_class=HTMLResponse)
    def review_queue(
        request: Request,
        queue_id: str,
        unit: str | None = None,
        highlight: str = "",
    ) -> HTMLResponse:
        """One unit's open work, or the clearing queue.

        ``highlight`` is the demo tour's hand-off (part 13) and is DISPLAY
        ONLY: it marks the row a visitor's own case is on and touches neither
        the order nor the membership of the queue. Absent from every request
        that does not pass it, which is every request outside the tour.
        """
        return HTMLResponse(
            review_view.render_queue(
                review_view.build_queue_view(
                    journal,
                    config=bundle,
                    queue_id=queue_id,
                    unit_id=review_view.resolve_unit(bundle, unit),
                    highlight=highlight,
                ),
                page_context(request),
            )
        )

    @app.get("/review/case/{case_id}", response_class=HTMLResponse)
    def review_case(
        request: Request,
        case_id: str,
        unit: str | None = None,
        message: str = "",
        error: str = "",
    ) -> HTMLResponse:
        """The evidence, not a verdict: everything the machine saw and did."""
        view = review_view.build_case_view(
            journal,
            config=bundle,
            case_id=case_id,
            unit_id=review_view.resolve_unit(bundle, unit),
            drafts=drafts,
            message=message,
            error=error,
            statement=statement_link(case_id),
        )
        if view is None:
            raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
        return HTMLResponse(review_view.render_case(view, page_context(request)))

    @app.post("/review/case/{case_id}/confirm")
    async def review_confirm(case_id: str, request: Request) -> RedirectResponse:
        """Confirm -> CONFIRMED, with the dispatch facts stamped on it."""
        form = await form_fields(request)
        events = journal.read(case_id)
        if not events:
            raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
        unit = form.get("unit", "")
        try:
            outcome = confirm_case(
                events,
                config=bundle,
                journal=journal,
                unit_id=review_view.resolve_unit(bundle, unit) or "",
                drafts=drafts,
                vault=vault,
                draft_edited=bool(form.get("draft_edited")),
                rechtsfolgenhinweis=bool(form.get("rechtsfolgenhinweis")),
                dispatch=bool(form.get("dispatch")),
                note=form.get("note", ""),
                dispatch_root=dispatch_dir(),
            )
        except ReviewActionError as error:
            return _redirect(case_id, unit, error=str(error))
        return _redirect(case_id, unit, message=_confirm_message(outcome))

    @app.post("/review/case/{case_id}/override")
    async def review_override(case_id: str, request: Request) -> RedirectResponse:
        """Re-route or tier change -> OVERRIDDEN. The reason is mandatory."""
        form = await form_fields(request)
        events = journal.read(case_id)
        if not events:
            raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
        unit, field = form.get("unit", ""), form.get("field", "")
        try:
            override_case(
                events,
                config=bundle,
                journal=journal,
                unit_id=review_view.resolve_unit(bundle, unit) or "",
                field=field,
                to_value=_coerce(field, form.get("to", "")),
                reason=form.get("reason", ""),
            )
        except ReviewActionError as error:
            return _redirect(case_id, unit, error=str(error))
        return _redirect(case_id, unit, message="Korrektur im Journal vermerkt.")

    @app.post("/review/case/{case_id}/escalate")
    async def review_escalate(case_id: str, request: Request) -> RedirectResponse:
        """P-4: one click to full human review, journaled as a correction."""
        form = await form_fields(request)
        events = journal.read(case_id)
        if not events:
            raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
        unit = form.get("unit", "")
        try:
            escalate_case(
                events,
                config=bundle,
                journal=journal,
                unit_id=review_view.resolve_unit(bundle, unit) or "",
                reason=form.get("reason", ""),
            )
        except ReviewActionError as error:
            return _redirect(case_id, unit, error=str(error))
        return _redirect(
            case_id, unit, message="Vorgang zur vollständigen Prüfung eskaliert."
        )


async def form_fields(request: Request) -> dict[str, str]:
    """The submitted form, parsed without a multipart dependency.

    An HTML form with no file input posts ``application/x-www-form-urlencoded``,
    which is three lines of stdlib. FastAPI's ``Form()`` would pull in
    ``python-multipart`` for a capability this UI does not have and must not
    grow: a review screen that accepted file uploads would be an ingest path
    that bypasses the redaction boundary.
    """
    body = await request.body()
    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))


def _coerce(field: str, value: str) -> object:
    """A tier arrives as a string from a form and must be journaled as an int.

    A tier that reached the journal as ``"3"`` would compare unequal to the
    integer the decision plane wrote, and the override would look like a change
    that never happened on every page that renders it.
    """
    if field in (OVERRIDE_TIER, OVERRIDE_ESCALATION):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def _confirm_message(outcome: ConfirmOutcome) -> str:
    """What the caseworker reads back. Never a claim the journal does not hold."""
    if outcome.facts is None:
        return f"Bestätigt. Kein Versand: {outcome.dispatch_skipped or 'kein Entwurf'}."
    deadline = outcome.facts.deadline
    if deadline is None:
        return (
            f"Bestätigt und zum Versand vorgemerkt "
            f"({outcome.facts.dispatch_date.isoformat()}, "
            f"{outcome.facts.dispatch_shape})."
        )
    return (
        f"Bestätigt und zum Versand vorgemerkt "
        f"({outcome.facts.dispatch_date.isoformat()}, "
        f"{outcome.facts.dispatch_shape}). Frist läuft am "
        f"{deadline.deadline.isoformat()} ab (Bekanntgabe "
        f"{deadline.bekanntgabe_date.isoformat()}, par. 37 Abs. 2 SGB X)."
    )


app = create_app()
