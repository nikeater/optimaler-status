"""Two languages, resolved on the server, carried by a cookie, no JavaScript.

**Why a table and not two template trees.** Every visitor-facing page here is
one template with one structure; a second tree of `landing.en.html` files would
be the same markup twice, and the second copy is the one that stops being
edited. So the markup stays single and every sentence in it is a key into
:data:`TABLE`, whose values are pairs - German first, English second. A key that
exists in one language and not the other is not representable, which is a
stronger guarantee than a test comparing two dictionaries could give.

**Why the server and not the browser.** `?lang=en` sets a cookie and redirects
back to the same URL without the parameter; the cookie then governs every later
request. A `<select>` with an `onchange` handler would be one line shorter and
would exclude everybody browsing with scripting off - on the surface of a
project whose whole posture is that a public-administration UI must work
without it. The redirect also keeps the address bar clean, so a visitor who
copies a URL hands over a page and not a language preference.

**What is translated and what is deliberately not.**

* Visitor-facing pages are translated in full: the landing page, the tour, the
  intake surface including its hints and its refusals, the pipeline narration,
  the disclaimer page and the inbox chrome.
* The caseworker screens (`/review*`, `/metrics`) STAY GERMAN in both settings
  and carry one English line saying so. They are the working surface of a
  German agency; a half-translated administrative vocabulary
  ("Nachforderung", "Bekanntgabefiktion") would be less usable than the German,
  not more, and a competition judge needs to see the real screen rather than a
  rendering of it.
* Message bodies, gap sentences and letter texts are never translated. They
  come from versioned configuration, they are legal-text artifacts, and a
  translated Verwaltungsakt draft would be a different document.

The default is German and stays German: an unknown, missing or malformed value
resolves to it rather than to the browser's `Accept-Language`, because a
deployment of a German administrative system should not change language because
somebody's laptop was bought abroad.
"""

from __future__ import annotations

from dataclasses import dataclass

from markupsafe import Markup

#: The two languages, German first. The order is the order of every pair in
#: :data:`TABLE`, so it is load-bearing rather than cosmetic.
LANGUAGES = ("de", "en")

DEFAULT_LANGUAGE = "de"

#: The query parameter that switches, and the cookie that remembers.
LANG_PARAM = "lang"
LANG_COOKIE = "eingangslotse_lang"

#: A year. Long enough that a returning visitor keeps their choice, short
#: enough to be a preference rather than a permanent record. Nothing personal
#: is stored: the value is either ``de`` or ``en``.
LANG_COOKIE_MAX_AGE = 31_536_000


def resolve_language(*candidates: str | None) -> str:
    """The first candidate this module knows, or German.

    Same discipline as ``api.review.resolve_unit`` and
    ``engine.demo.personas.PersonaSet.get``: an unknown value in a bookmarked
    URL or a stale cookie is not an error and must never half-apply. It simply
    is not a language this deployment speaks.
    """
    for candidate in candidates:
        if candidate in LANGUAGES:
            return candidate
    return DEFAULT_LANGUAGE


@dataclass(frozen=True)
class PageContext:
    """What every rendered page knows about the request that asked for it.

    Two fields and no more. ``lang`` is the resolved language; ``here`` is the
    current path with its query string minus the language parameter, which is
    what the toggle links append to. Nothing about the visitor is carried, and
    nothing here reaches a template that is not one of those two.
    """

    lang: str = DEFAULT_LANGUAGE
    here: str = "/"

    @property
    def index(self) -> int:
        return LANGUAGES.index(self.lang)

    @property
    def is_english(self) -> bool:
        return self.lang == "en"

    def t(self, key: str, **values: object) -> str:
        """One phrase in this page's language.

        An unknown key returns the key itself rather than raising: a typo must
        not turn a citizen-facing page into a 500. It cannot go unnoticed
        either - ``tests/test_i18n.py`` walks every template for ``t("...")``
        and fails on a key the table does not carry.
        """
        pair = TABLE.get(key)
        if pair is None:  # pragma: no cover - the template sweep prevents it
            return key
        text = pair[self.index]
        return text.format(**values) if values else text

    def m(self, key: str, **values: object) -> Markup:
        """The same phrase, for the ones that carry inline markup.

        A handful of sentences need a ``<strong>`` or a ``<code>`` in the
        middle, and splitting each of those into three keys would make the
        table unreadable and the translation worse. They are marked safe HERE
        rather than with Jinja's ``|safe`` filter, and the difference is not
        cosmetic: ``Markup.format`` ESCAPES the values it interpolates, so a
        case id or a unit name substituted into one of these sentences is
        escaped even though the sentence around it is not. ``|safe`` on the
        formatted result would have escaped neither.

        ``tests/test_i18n.py`` asserts the two halves of the rule: every phrase
        containing a ``<`` is rendered through this method, and no phrase
        rendered through ``t()`` contains one.
        """
        text = self.t(key)
        return Markup(text).format(**values) if values else Markup(text)

    def switch_href(self, language: str) -> str:
        """Where the toggle for ``language`` points: here, plus ``?lang=``."""
        separator = "&" if "?" in self.here else "?"
        return f"{self.here}{separator}{LANG_PARAM}={language}"


#: The German page context, which is what a template rendered without one gets.
GERMAN = PageContext()


def strip_language(path: str, query: str) -> str:
    """``here``: the current URL without the language parameter.

    Built from the path and the raw query string rather than by re-encoding a
    parsed mapping, so a parameter this module does not know about survives the
    round trip exactly as it arrived.
    """
    kept = [
        pair
        for pair in query.split("&")
        if pair and not pair.startswith(f"{LANG_PARAM}=")
    ]
    return path + ("?" + "&".join(kept) if kept else "")


# --------------------------------------------------------------- the table ---

#: Every phrase this project renders in two languages, German first.
#:
#: Grouped by page, in the order the pages are read. Keys are dotted and name
#: the place rather than the sentence, so a re-wording is a value change and a
#: re-structuring is a key change - which is exactly the distinction a reviewer
#: of a diff wants to see.
TABLE: dict[str, tuple[str, str]] = {
    # ----------------------------------------------------------- the chrome --
    "chrome.subtitle": (
        "Triage-Assistent für Verwaltungsverfahren",
        "Triage assistant for administrative procedures",
    ),
    "chrome.skip": ("Direkt zum Inhalt", "Skip to content"),
    "chrome.home": ("Zur Startseite", "To the start page"),
    "chrome.language": ("Sprache", "Language"),
    "chrome.language.current": ("aktuelle Sprache", "current language"),
    "chrome.menu": ("Menü", "Menu"),
    "chrome.menu.label": ("Hauptmenü", "Main menu"),
    "chrome.nav.home": ("Start", "Start"),
    "chrome.nav.tour": ("Rundgang", "Guided tour"),
    "chrome.nav.intake": ("Antrag stellen", "Submit an application"),
    "chrome.nav.review": ("Sachbearbeitung", "Caseworker screens"),
    "chrome.nav.inbox": ("Postfach", "Applicant inbox"),
    "chrome.nav.metrics": ("Eval-Metriken", "Evaluation metrics"),
    "chrome.nav.hinweise": ("Hinweise", "About this demo"),
    "chrome.nav.source": ("Quellcode", "Source code"),
    "ribbon.label": ("Hinweis zur Demo-Instanz", "Note about this demo instance"),
    "ribbon.text": ("Demo - synthetische Daten.", "Demo - synthetic data."),
    "ribbon.more": ("Mehr dazu", "Learn more"),
    "footer.real": (
        "Dieser Rundgang läuft durch die ECHTE Verarbeitung. Es gibt keinen "
        "Demo-Pfad neben der Anlage: dieselbe Versiegelung, dieselbe Prüfung, "
        "dasselbe Journal. Was Sie hier auslösen, steht danach genauso in der "
        "Sachbearbeitung wie jeder andere Eingang.",
        "This walkthrough runs through the REAL processing. There is no demo "
        "path beside the system: the same sealing, the same validation, the "
        "same journal. What you trigger here lands in the caseworker queues "
        "exactly like any other incoming case.",
    ),
    "footer.no_message": (
        "Diese Seiten erzeugen keine Nachricht an antragstellende Personen. "
        "Eingangsbestätigung und Zwischenstand entstehen automatisch aus dem "
        "Journal und durchlaufen keine Prüfung (ADR-005); <code>/inbox</code> "
        "ist und bleibt reine Ansicht, auch hier.",
        "These pages cannot produce a message to an applicant. The receipt and "
        "the status note are automatic projections of the case journal and "
        "pass no human review (ADR-005); <code>/inbox</code> is and stays "
        "read-only, here as everywhere.",
    ),
    "footer.a11y": (
        "Barrierefreiheit: Selbsteinschätzung nach EN 301 549 / WCAG 2.1 AA in "
        "<code>docs/accessibility-selfcheck.md</code>. Eine externe Prüfung "
        "nach BITV 2.0 hat nicht stattgefunden und ist Pilotvoraussetzung "
        "(P-15).",
        "Accessibility: self-assessment against EN 301 549 / WCAG 2.1 AA in "
        "<code>docs/accessibility-selfcheck.md</code>. No external BITV 2.0 "
        "test has been performed; it is a pilot prerequisite (P-15).",
    ),
    "footer.source": (
        "Quellcode und vollständige technische Spezifikation:",
        "Source code and the full technical specification:",
    ),
    "footer.license": ("Lizenz: EUPL-1.2.", "Licence: EUPL-1.2."),
    # The one English line the caseworker screens carry, and only in English
    # mode. It is a `lang="en"` element inside a German document on purpose.
    "review.english_note": (
        "The caseworker screens are shown in German - the working language of "
        "the agency this system is built for.",
        "The caseworker screens are shown in German - the working language of "
        "the agency this system is built for.",
    ),
    # ------------------------------------------------------- the phase strip --
    "phase.antrag": ("Antrag", "Application"),
    "phase.maschine": ("Maschine", "Machine"),
    "phase.sachbearbeitung": ("Sachbearbeitung", "Caseworker"),
    "phase.label": ("Rundgang in drei Phasen", "The walkthrough in three phases"),
    "phase.current": ("aktuelle Phase", "current phase"),
    "phase.done": ("abgeschlossen", "completed"),
    "phase.number": ("Phase {number}", "Phase {number}"),
    # ---------------------------------------------------------- the landing --
    "landing.title": ("EingangsLotse - Demo", "EingangsLotse - demo"),
    "landing.headline": (
        "Eingänge sortieren, ohne über Menschen zu entscheiden",
        "Sorting incoming cases without deciding about people",
    ),
    "landing.lead": (
        "Ein Eingangs-Assistent für Massenverfahren der öffentlichen "
        "Verwaltung: Er liest einen Eingang, belegt jede Aussage, ordnet ihn "
        "einer Einheit zu und entscheidet nach einer versionierten "
        "Entscheidungstabelle, ob ein Mensch ihn sehen muss. Er entscheidet "
        "nichts über Menschen - er sortiert Arbeit und legt offen, warum.",
        "An intake assistant for high-volume German public administration "
        "procedures. It reads an incoming case, proves every extracted value "
        "against the document it came from, routes it to an organisational "
        "unit, and lets a versioned decision table decide whether a human has "
        "to look. It decides nothing about people - it sorts work and shows "
        "its reasoning.",
    ),
    "landing.cta.tour": ("Zum Rundgang", "Take the guided tour"),
    "landing.cta.intake": ("Antrag stellen", "Submit an application"),
    "landing.hero.label": (
        "Der Weg eines Eingangs durch die Anlage, in fünf Schritten",
        "The path of an incoming case through the system, in five steps",
    ),
    "landing.hero.desc": (
        "Ein Antrag kommt an, die Identitätsangaben werden versiegelt, die "
        "Entscheidungstabelle stuft den Vorgang ein, er landet in der "
        "Warteschlange einer Einheit, und die Eingangsbestätigung geht zurück "
        "an die antragstellende Person.",
        "An application arrives, its identity fields are sealed, the decision "
        "table assigns a tier, the case lands in an organisational unit's "
        "queue, and the receipt travels back to the applicant.",
    ),
    "landing.hero.s1": ("Eingang", "Arrival"),
    "landing.hero.s2": ("Versiegelung", "Sealing"),
    "landing.hero.s3": ("Entscheidung", "Decision"),
    "landing.hero.s4": ("Warteschlange", "Queue"),
    "landing.hero.s5": ("Antwort", "Reply"),
    # The five stage captions. Real text in the document, one sentence each,
    # faded in and out by the same keyframes that drive their stage - so the
    # sentence a visitor reads is always the stage they are looking at, and a
    # reader who never sees the motion still gets all five (see demo.css).
    "landing.hero.c1": (
        "Ein Antrag kommt an - als Formular, als E-Mail oder als Scan.",
        "An application arrives - as a form, an e-mail or a scan.",
    ),
    "landing.hero.c2": (
        "Alle personenbezogenen Angaben werden sofort versiegelt; die Maschine "
        "bekommt sie nie zu sehen.",
        "Every identity-bearing value is sealed immediately; the machine never "
        "gets to see it.",
    ),
    "landing.hero.c3": (
        "Eine versionierte Entscheidungstabelle prüft Vollständigkeit und "
        "Zuständigkeit und begründet jede Zeile.",
        "A versioned decision table checks completeness and responsibility, "
        "and justifies every row.",
    ),
    "landing.hero.c4": (
        "Der Vorgang landet in der Warteschlange des zuständigen Referats und "
        "wartet auf einen Menschen.",
        "The case lands in the responsible unit's queue and waits for a human.",
    ),
    "landing.hero.c5": (
        "Die antragstellende Person erhält automatisch eine "
        "Eingangsbestätigung - aus einer Vorlage, nie aus einem Modell.",
        "The applicant automatically receives a receipt - rendered from a "
        "template, never from a model.",
    ),
    "landing.hero.pause": (
        "Animation anhalten",
        "Pause the animation",
    ),
    "landing.hero.paused": (
        "Angehalten: alle fünf Schritte stehen still und alle fünf Sätze "
        "stehen untereinander.",
        "Paused: all five steps stand still and all five sentences are shown "
        "one under the other.",
    ),
    "landing.hero.caption.label": (
        "Was in diesem Schritt passiert",
        "What happens at this step",
    ),
    "landing.start.heading": ("Fangen Sie hier an", "Start here"),
    "landing.start.lead": (
        "Der <strong>Rundgang</strong> erzählt das ganze System von der ersten "
        "Einreichung bis zum geschlossenen Kreis - in sechs Schritten, jeder "
        "mit einem Link auf die Stelle, an der er tatsächlich passiert. Er ist "
        "für Besucherinnen und Besucher gedacht, die dieses Projekt noch nie "
        "gesehen haben.",
        "The <strong>guided tour</strong> walks the whole system end to end, "
        "from the first submission to the closed loop - six steps, each one "
        "linking to the page where that step really happens. It is written for "
        "somebody who has never seen this project.",
    ),
    "landing.start.jump": (
        "Oder direkt an eine Stelle springen:",
        "Or jump straight in:",
    ),
    "landing.card.intake.title": ("Antrag stellen", "Submit an application"),
    "landing.card.intake.body": (
        "Sie stellen als erfundene Person einen Antrag und sehen danach "
        "Schritt für Schritt, was die Anlage damit macht. Es ist derselbe Weg, "
        "den jeder andere Eingang nimmt.",
        "You submit an application as an openly fictional applicant and then "
        "watch, step by step, what the system does with it. It is the same "
        "path every other incoming case takes.",
    ),
    "landing.card.review.title": ("Sachbearbeitung", "Caseworker screens"),
    "landing.card.review.body": (
        "Warteschlangen je Einheit, die Vorgangsansicht mit allen Belegen und "
        "die drei Aktionen (bestätigen, umsteuern, eskalieren). Jede Aktion "
        "schreibt ein Ereignis ins Journal; nichts wird überschrieben.",
        "Queues per organisational unit, the case view with every piece of "
        "evidence, and the three actions (confirm, re-route, escalate). Each "
        "action appends a journal event; nothing is ever overwritten.",
    ),
    "landing.card.metrics.title": ("Eval-Metriken", "Evaluation metrics"),
    "landing.card.metrics.body": (
        "Die Zahlen des letzten Eval-Laufs über dem eingefrorenen Goldsatz, "
        "inklusive der vier Gates. Die Seite rechnet nichts selbst.",
        "The numbers from the last evaluation run over the frozen gold corpus, "
        "including the four gates. The page computes nothing itself.",
    ),
    "landing.card.inbox.title": ("Postfach", "Applicant inbox"),
    "landing.card.inbox.body": (
        "Was eine antragstellende Person erhalten hätte. Nur Ansicht, ohne "
        "jede Bedienung.",
        "What an applicant would have received. Read-only, with no controls at all.",
    ),
    "landing.card.health.title": ("Betriebspunkte", "Operational endpoints"),
    "landing.card.health.body": (
        "<code>/health</code> nennt die Konfigurationsversionen dieses "
        "Prozesses, <code>/healthz</code> ist die Betriebsprüfung des "
        "Containers.",
        "<code>/health</code> reports the configuration versions this process "
        "is running; <code>/healthz</code> is the container healthcheck.",
    ),
    "landing.promises.heading": ("Die drei Zusagen", "The three guarantees"),
    "landing.promise.valve.title": (
        "Zwei Ebenen, eine Richtung",
        "Two planes, one direction",
    ),
    "landing.promise.valve.body": (
        "Die Evidenzebene ist probabilistisch und darf irren: sie extrahiert, "
        "ordnet zu, findet Lücken. Die Entscheidungsebene ist deterministisch "
        "und liest nur, was belegt ist. Unsicherheit kann einen Vorgang nur zu "
        "einem Menschen schieben, nie von ihm weg - das ist das Einwegventil "
        "(ADR-004), und es wird bei jedem Commit gegen die echte "
        "Entscheidungstabelle geprüft.",
        "The evidence plane is probabilistic and is allowed to be wrong: it "
        "extracts, routes and finds gaps. The decision plane is deterministic "
        "and reads only what has been proven. Uncertainty can push a case "
        "towards a human and never away from one - that is the one-way valve "
        "(ADR-004), and it is checked against the real decision table on every "
        "commit.",
    ),
    "landing.promise.model.title": (
        "Kein Modelltext an Bürgerinnen und Bürger",
        "No model-written text reaches a citizen",
    ),
    "landing.promise.model.body": (
        "Kein Satz, den ein Sprachmodell erzeugt hat, erreicht eine "
        "antragstellende Person. Benachrichtigungen entstehen aus Vorlagen "
        "einer versionierten Konfiguration; Entwürfe warten auf die "
        "Bestätigung eines Menschen. Ein Modell darf lesen, nie schreiben.",
        "No sentence produced by a language model ever reaches an applicant. "
        "Notifications are rendered from templates in versioned configuration; "
        "drafts wait for a human to confirm them. A model may read and may "
        "never write.",
    ),
    "landing.promise.seal.title": (
        "Identität wird am Eingang versiegelt",
        "Identity is sealed at the boundary",
    ),
    "landing.promise.seal.body": (
        "Identitätsbezogene Felder werden versiegelt, bevor die Arbeitskopie "
        "entsteht; alles danach sieht Platzhalter. Erst beim Rendern eines "
        "Briefes wird zurückbefüllt. Der Redaktions-Recall wird gemessen und "
        "ist ein Gate.",
        "Identity-bearing fields are sealed before the working copy exists; "
        "everything downstream sees placeholders. Re-hydration happens only "
        "when a letter is rendered. Redaction recall is measured and is a "
        "release gate.",
    ),
    "landing.instance.heading": (
        "Was diese Instanz ist und was nicht",
        "What this instance is, and what it is not",
    ),
    "landing.instance.synthetic": (
        "Alle Daten sind synthetisch und stammen aus dem eingefrorenen "
        "Goldsatz <code>{gold_dir}</code>. Es gibt hier keine echte Person und "
        "keinen echten Vorgang.",
        "All data is synthetic and comes from the frozen gold corpus "
        "<code>{gold_dir}</code>. There is no real person and no real case "
        "here.",
    ),
    "landing.instance.reset": (
        "Der Datenbestand wird bei jedem Neustart gelöscht und aus dem "
        "Goldsatz neu aufgebaut. Was Sie hier bestätigen oder umsteuern, "
        "verschwindet damit wieder - deshalb sind die Aktionen offen.",
        "The whole state is deleted on every restart and rebuilt from the gold "
        "corpus. Whatever you confirm or re-route here disappears with it - "
        "which is what makes leaving the actions open harmless.",
    ),
    "landing.instance.storage": (
        "Der Ablagespeicher dieser Demo ist unverschlüsseltes JSONL. Das ist "
        "genau deshalb vertretbar, weil er nur synthetische Daten enthält und "
        "der Eingang gesperrt ist; ein Produktivbetrieb ersetzt ihn durch den "
        "verschlüsselten Speicher aus <code>docs/vault-dpia-input.md</code>.",
        "The demo's storage backend is unencrypted JSONL. That is defensible "
        "precisely because it holds nothing but synthetic data and because "
        "ingest is closed; a production deployment replaces it with the "
        "encrypted store described in <code>docs/vault-dpia-input.md</code>.",
    ),
    "landing.ingest.closed": (
        "Der Eingang ist gesperrt: POST /ingest antwortet mit 403, ohne die "
        "Anfrage zu lesen. Diese Instanz kann keinen Antrag entgegennehmen - "
        "auch keinen echten, versehentlich abgeschickten.",
        "Ingest is closed: POST /ingest answers 403 without reading the "
        "request. This instance cannot accept an application - not even a real "
        "one, submitted by accident.",
    ),
    "landing.ingest.token": (
        "Der Eingang ist nur mit dem Token dieser Bereitstellung erreichbar "
        "(Kopfzeile X-Ingest-Token); ohne Token antwortet POST /ingest mit 403. "
        "Senden Sie auch mit Token keine echten Daten: der Ablagespeicher "
        "dieser Demo ist unverschlüsselt.",
        "Ingest is reachable only with this deployment's token (header "
        "X-Ingest-Token); without it POST /ingest answers 403. Do not send real "
        "data even with the token: this demo's storage backend is "
        "unencrypted.",
    ),
    "landing.instance.more": (
        "Alles dazu auf einer Seite:",
        "All of it on one page:",
    ),
    # --------------------------------------------------------- the disclaimer --
    "hinweise.title": (
        "EingangsLotse - Hinweise zur Demo",
        "EingangsLotse - about this demo",
    ),
    "hinweise.headline": (
        "Hinweise zu dieser Demo-Instanz",
        "About this demonstration instance",
    ),
    "hinweise.lead": (
        "Diese Seite sammelt, was sonst in einem Banner über jeder Seite "
        "stünde: woher die Daten kommen, was diese Instanz annehmen kann, was "
        "ein Neustart tut, unter welcher Lizenz der Quellcode steht und wie es "
        "um die Barrierefreiheit bestellt ist.",
        "This page collects what used to sit in a banner above every screen: "
        "where the data comes from, what this instance can accept, what a "
        "restart does, the licence the source is published under, and where "
        "the accessibility posture stands.",
    ),
    "hinweise.banner.heading": (
        "Der vollständige Hinweis",
        "The full notice",
    ),
    "hinweise.banner.note": (
        "Dieser Text ist der Wortlaut, den die Bereitstellung selbst führt; er "
        "steht unverändert hier und ist in beiden Sprachfassungen derselbe.",
        "This is the wording the deployment itself carries. It is reproduced "
        "verbatim and is the same text in both language settings.",
    ),
    "hinweise.synthetic.heading": (
        "Keine echten Daten - niemals",
        "No real data - ever",
    ),
    "hinweise.synthetic.body": (
        "Senden Sie hier nichts, was zu einem Menschen gehört. Kein Name, kein "
        "Geburtsdatum, keine Versicherungsnummer, keine Anschrift. Der "
        "Ablagespeicher dieser Demo ist unverschlüsseltes JSONL; er ist genau "
        "deshalb vertretbar, weil er ausschließlich Erfundenes enthält. Die "
        "erfundenen Personen auf der Antragsseite sind zusätzlich daraufhin "
        "geprüft, dass keiner ihrer Werte im Goldsatz oder im "
        "Kanarienvogel-Satz vorkommt.",
        "Do not send anything here that belongs to a person. No name, no date "
        "of birth, no insurance number, no address. The demo's storage backend "
        "is unencrypted JSONL, and it is defensible precisely because it holds "
        "nothing but invented values. The fictional applicants on the intake "
        "page are additionally checked to make sure none of their values "
        "occurs in the gold corpus or in the redaction canary set.",
    ),
    "hinweise.gold": (
        "Der gesamte Datenbestand stammt aus dem eingefrorenen Goldsatz "
        "<code>{gold_dir}</code>. Ein Goldsatz wird nie bearbeitet: eine "
        "falsche Kennzeichnung wird durch einen neuen, versionierten Satz "
        "abgelöst, nicht an Ort und Stelle korrigiert.",
        "The entire state comes from the frozen gold corpus "
        "<code>{gold_dir}</code>. A gold set is never edited: a label that "
        "turns out to be wrong is superseded by a new versioned set rather "
        "than corrected in place.",
    ),
    "hinweise.ingest.heading": ("Der Eingang", "The ingest endpoint"),
    "hinweise.ingest.middleware": (
        "Die Prüfung ist eine Middleware und keine Routen-Abhängigkeit. Das "
        'ist der Unterschied zwischen "abgelehnt" und "gar nicht gelesen": '
        "ein Framework dekodiert den Anfragekörper, bevor es die "
        "Abhängigkeiten einer Route auflöst, und eine Ablehnung danach wäre "
        "eine Ablehnung, nach der dieser Prozess die Einreichung einer fremden "
        "Person bereits gelesen hat.",
        "The check is middleware, not a route dependency, and that is the "
        'difference between "refused" and "never read": a framework '
        "decodes the request body before it solves a route's dependencies, so "
        "a refusal at that point would be a refusal issued after this process "
        "has already read a stranger's submission.",
    ),
    "hinweise.reset.heading": ("Zurücksetzen durch Neustart", "Reset by restart"),
    "hinweise.reset.body": (
        "Der Datenbestand wird bei jedem Start gelöscht und aus dem Goldsatz "
        "neu aufgebaut; zwei Einspielungen desselben Satzes mit derselben "
        "Basiszeit erzeugen denselben Zustand. Genau deshalb sind die "
        "Aktionen der Sachbearbeitung offen: was Sie bestätigen, umsteuern "
        "oder eskalieren, verschwindet beim nächsten Start. Der "
        "Zwischenspeicher der Antragsseite lebt ausschließlich im "
        "Arbeitsspeicher, hält eine Eingabe nur kurze Zeit und überlebt keinen "
        "Prozess.",
        "The state is deleted on every start and rebuilt from the gold corpus; "
        "two seedings of the same corpus with the same base clock produce the "
        "same state. That is exactly why the caseworker actions are left open: "
        "whatever you confirm, re-route or escalate is gone at the next start. "
        "The intake page's scratch store lives in memory only, holds an entry "
        "for a short time and survives no process.",
    ),
    "hinweise.licence.heading": ("Lizenz und Quellcode", "Licence and source"),
    "hinweise.licence.body": (
        "Der Quellcode steht unter der EUPL-1.2. Im Repository liegen die "
        "Architekturentscheidungen, die technische Spezifikation, die "
        "bekannten Fehler und die Barrierefreiheits-Selbsteinschätzung.",
        "The source is published under the EUPL-1.2. The repository carries "
        "the architecture decision records, the technical specification, the "
        "known errors and the accessibility self-assessment.",
    ),
    "hinweise.licence.unset": (
        "Für diese Bereitstellung ist keine Quellcode-Adresse konfiguriert "
        "(<code>EINGANGSLOTSE_REPO_URL</code>). Deshalb steht hier kein Link "
        "statt eines Links, der ins Leere führt.",
        "This deployment has no source address configured "
        "(<code>EINGANGSLOTSE_REPO_URL</code>). There is therefore no link "
        "here rather than a link that goes nowhere.",
    ),
    "hinweise.a11y.heading": ("Barrierefreiheit", "Accessibility"),
    "hinweise.a11y.body": (
        "Es gibt eine Selbsteinschätzung nach EN 301 549 V3.2.1 / WCAG 2.1 AA "
        "in <code>docs/accessibility-selfcheck.md</code>. Sie ist eine "
        "Selbsteinschätzung und kein Test nach BITV 2.0: geprüft wurde von der "
        "umsetzenden Person, keine Nutzerin und kein Nutzer mit einer "
        "Behinderung hat diese Seiten bedient, und keine assistive Technologie "
        "ist dagegen gelaufen. Eine Erklärung zur Barrierefreiheit nach "
        "par. 12b BGG darf daraus NICHT abgeleitet werden.",
        "A self-assessment against EN 301 549 V3.2.1 / WCAG 2.1 AA lives in "
        "<code>docs/accessibility-selfcheck.md</code>. It is a "
        "self-assessment and not a BITV 2.0 test: it was written by the "
        "implementing engineer, no person with a disability has used these "
        "pages, and no assistive technology has been run against them. An "
        "accessibility statement under par. 12b BGG may NOT be derived from "
        "it.",
    ),
    "hinweise.a11y.mechanical": (
        "Was maschinell prüfbar ist, wird bei jedem Commit geprüft: ein "
        "Sprungziel als erstes fokussierbares Element jeder Seite, ein "
        "Beschriftungselement zu jedem Bedienelement, eine Tabellenüberschrift "
        "zu jeder Tabelle, keine Bedeutung allein durch Farbe, ein Fokusrahmen, "
        "der umgestaltet und nie entfernt wird, und die Umbruchregeln für 320 "
        "Bildpunkte Breite. Was ein statischer Test nicht entscheiden kann, "
        "steht im Dokument als offen und nicht als bestanden.",
        "Everything a machine can check is checked on every commit: a skip "
        "link as the first focusable element of every page, a label for every "
        "control, a caption for every table, no meaning carried by colour "
        "alone, a focus ring that is restyled and never removed, and the "
        "reflow rules for a 320 pixel viewport. What a static test cannot "
        "decide is listed in the document as open rather than as passed.",
    ),
    "hinweise.back": ("Zurück zur Startseite", "Back to the start page"),
    # ------------------------------------------- shared vocabulary of a case --
    "tier.1": ("Tier 1 - klar und vollständig", "Tier 1 - clear and complete"),
    "tier.2": (
        "Tier 2 - zuordenbar, unvollständig",
        "Tier 2 - routable, incomplete",
    ),
    "tier.3": ("Tier 3 - vollständige Prüfung", "Tier 3 - full human review"),
    "kind.VSNR": ("Versicherungsnummer", "Insurance number"),
    "kind.GEBDAT": ("Geburtsdatum", "Date of birth"),
    "kind.ADDR": ("Anschrift", "Address"),
    "kind.NAME": ("Name", "Name"),
    "kind.ORG": ("Organisation / Auftraggeber", "Organisation / client"),
    "kind.BNR": ("Betriebsnummer", "Employer registration number"),
    "kind.IBAN": ("Kontoverbindung", "Bank account"),
    "kind.STID": ("Steuer-Identifikationsnummer", "Tax identification number"),
    "kind.AKTZ": ("Aktenzeichen", "File reference"),
    "kind.EMAIL": ("E-Mail-Adresse", "E-mail address"),
    "kind.TEL": ("Telefonnummer", "Telephone number"),
    "kind.TEXT": ("sonstiger Identitätsbezug", "other identifying content"),
    "queue.clearing": (
        "Zentrale Klärung (par. 16 Abs. 2 SGB I)",
        "Central clearing queue (par. 16 Abs. 2 SGB I)",
    ),
    "picker.note": (
        "Rollenwahl ist eine Demo-Funktion ohne Anmeldung: die Einheit steht "
        "in der Adresszeile. Ein echtes Berechtigungskonzept mit "
        "Identitätsanbieter ist Pilotvoraussetzung (C-5) und existiert hier "
        "nicht.",
        "Picking a unit is a demo affordance with no sign-in: the unit sits in "
        "the address bar. A real authorisation model with an identity provider "
        "is a pilot prerequisite (C-5) and does not exist here.",
    ),
    "channel.fit_connect": (
        "Formular (FIT-Connect)",
        "Form (FIT-Connect)",
    ),
    "channel.email": (
        "E-Mail (simulierter Adapter)",
        "E-mail (simulated adapter)",
    ),
    "channel.note.fit_connect": (
        "Ein strukturierter Eingang, wie ihn eine FIT-Connect-Zustellung "
        "liefert. Die identitätsbezogenen Felder werden als PFADE versiegelt, "
        "bevor die Arbeitskopie entsteht.",
        "A structured submission, of the shape a FIT-Connect delivery "
        "produces. The identity-bearing fields are sealed by PATH, before the "
        "working copy exists.",
    ),
    "channel.note.email": (
        "SIMULIERTER Adapter: es wird kein Postfach abgerufen, keine Mail "
        "empfangen und keine Adresse betrieben. Ihr Text geht direkt in "
        "dieselbe Verarbeitung, die ein echter Adapter beliefern würde - der "
        "Adapter selbst ist Pilotumfang (P-14). Im Freitext findet die "
        "Erkennerunion die Identitätsangaben und versiegelt sie SPANNE FÜR "
        "SPANNE.",
        "SIMULATED adapter: no mailbox is polled, no mail is received and no "
        "address is operated. Your text goes straight into the same processing "
        "a real adapter would feed - the adapter itself is pilot scope (P-14). "
        "In free text the detector union finds the identity values and seals "
        "them SPAN BY SPAN.",
    ),
    # ------------------------------------------------------------- the tour --
    "tour.title": ("EingangsLotse - Rundgang", "EingangsLotse - guided tour"),
    "tour.headline": (
        "Rundgang: von der Einreichung bis zum geschlossenen Kreis",
        "The tour: from submission to the closed loop",
    ),
    "tour.overview.heading": ("Die sechs Schritte", "The six steps"),
    "tour.lead": (
        "Dieser Rundgang erzählt das System einmal ganz durch: was das Problem "
        "ist, was beim Einreichen passiert, was die Maschine daraus macht, wie "
        "ein Mensch entscheidet, was die antragstellende Person davon sieht - "
        "und warum man dem Gezeigten trauen kann. Jeder Schritt verlinkt die "
        "Stelle, an der er tatsächlich stattfindet. Rechnen Sie mit zehn "
        "Minuten.",
        "This tour walks the whole system once, end to end: what the problem "
        "is, what happens when something is submitted, what the machine makes "
        "of it, how a human decides, what the applicant sees of it - and why "
        "any of it can be trusted. Every step links to the page where it "
        "really happens. About ten minutes.",
    ),
    "tour.toc.label": ("Schritte des Rundgangs", "Steps of the tour"),
    "tour.toc.1": (
        "Das Problem und die Antwort in zwei Ebenen",
        "The problem, and a two-plane answer",
    ),
    "tour.toc.2": ("Phase 1: Sie reichen etwas ein", "Phase 1: you submit something"),
    "tour.toc.3": (
        "Phase 2: Was die Maschine daraus gemacht hat",
        "Phase 2: what the machine made of it",
    ),
    "tour.toc.4": (
        "Phase 3: Ein Mensch entscheidet",
        "Phase 3: a human decides",
    ),
    "tour.toc.5": (
        "Der Kreis schließt sich im Postfach",
        "The loop closes in the inbox",
    ),
    "tour.toc.6": ("Warum man dem trauen kann", "Why any of this can be trusted"),
    "tour.s1.p1": (
        "In Massenverfahren der öffentlichen Verwaltung entscheidet nicht die "
        "schwierige Akte über den Rückstand, sondern die Menge: "
        "hunderttausende Eingänge, von denen die meisten einfach sind, müssen "
        "sortiert werden, bevor irgendjemand fachlich arbeiten kann. Der "
        "teuerste Fehler dabei ist immer derselbe - ein Vorgang wird als "
        '"kein Mensch nötig" abgelegt, der einen Menschen gebraucht hätte.',
        "Mass administrative procedures are broken by volume, not by hard "
        "cases: hundreds of thousands of submissions, most of them simple, "
        "have to be sorted before anybody can do substantive work at all. And "
        "the expensive failure is always the same one - something gets cleared "
        'as "no human needed" that needed a human.',
    ),
    "tour.s1.p2": (
        "Die Antwort dieses Systems sind zwei getrennte Ebenen: eine "
        "<strong>Evidenzebene</strong>, die probabilistisch sein darf - sie "
        "liest, extrahiert, ordnet zu, findet Lücken - und eine "
        "<strong>Entscheidungsebene</strong>, die deterministisch ist und "
        "ausschließlich liest, was belegt ist. Zwischen beiden liegt ein "
        "Einwegventil: Unsicherheit kann einen Vorgang nur zu einem Menschen "
        "schieben, nie von ihm weg. Das ist keine Absichtserklärung, sondern "
        "eine Eigenschaft, die bei jedem Commit gegen die echte "
        "Entscheidungstabelle und die 101 echten Evidenzsätze des "
        "eingefrorenen Satzes geprüft wird (ADR-004).",
        "The answer here is two separate planes: an "
        "<strong>evidence plane</strong> that is allowed to be probabilistic - "
        "it reads, extracts, routes and finds gaps - and a "
        "<strong>decision plane</strong> that is deterministic and reads only "
        "what has been proven. Between them sits a one-way valve: uncertainty "
        "can push a case towards a human and never away from one. That is not "
        "a statement of intent but a property, checked on every commit against "
        "the real decision table and the 101 real evidence records of the "
        "frozen corpus (ADR-004).",
    ),
    "tour.s1.w1": (
        "<strong>Der Assistent entscheidet nichts über Menschen.</strong> Er "
        "entscheidet, wie genau ein Mensch hinsehen muss - Tier 1 klar, Tier 2 "
        "unvollständig, Tier 3 vollständige Prüfung - und begründet jede "
        "dieser Einstufungen zeilenweise.",
        "<strong>The assistant decides nothing about people.</strong> It "
        "decides how closely a human has to look - tier 1 clear, tier 2 "
        "incomplete, tier 3 full review - and justifies each of those "
        "classifications line by line.",
    ),
    "tour.s1.w2": (
        "<strong>Kein Satz eines Sprachmodells erreicht eine antragstellende "
        "Person.</strong> Nachrichten entstehen aus Vorlagen einer "
        "versionierten Konfiguration, Entwürfe warten auf die Bestätigung "
        "eines Menschen.",
        "<strong>No sentence written by a language model reaches an "
        "applicant.</strong> Messages are rendered from templates in versioned "
        "configuration; drafts wait for a human to confirm them.",
    ),
    "tour.s2.p1": (
        "Auf der Antragsseite wählen Sie eine von vier <em>erfundenen</em> "
        "Personen - offenkundig erfundene Namen, damit ein Bildschirmfoto "
        "dieser Demo niemals wie ein echter Vorgang aussieht - und bearbeiten deren "
        "vorausgefüllten Antrag. Sie dürfen ihn kaputt machen: eine Rentenart "
        "eintragen, die es nicht gibt, den Rentenbeginn zwanzig Jahre in die "
        "Zukunft schieben, den Auslandsbezug einschalten. Jede dieser "
        "Änderungen löst ein anderes, echtes Verhalten der Anlage aus.",
        "On the intake page you pick one of four openly <em>fictional</em> "
        "applicants - Mustermann-class names, so that a screenshot of this "
        "demo can never look like a real case - and edit their prefilled "
        "application. You are meant to break it: enter a pension type that "
        "does not exist, push the pension start date twenty years into the "
        "future, switch on the cross-border flag. Each of those changes "
        "triggers a different, real behaviour of the system.",
    ),
    "tour.s2.p2": (
        "Das Formular geht durch <strong>dieselbe</strong> Verarbeitung wie "
        "jeder andere Eingang: dieselbe Versiegelung, dieselbe Prüfung, "
        "dasselbe Journal. Es gibt keinen Demo-Pfad daneben.",
        "The form goes through <strong>the same</strong> processing as any "
        "other incoming case: the same sealing, the same validation, the same "
        "journal. There is no demo path beside it.",
    ),
    "tour.s2.cta": ("Antrag stellen (Phase 1)", "Submit an application (phase 1)"),
    "tour.s2.closed": (
        "<strong>Auf dieser Instanz ist der Eingang gesperrt.</strong> Ohne "
        "konfiguriertes Ingest-Token antwortet <code>POST /ingest</code> jedem "
        "Aufrufer mit 403, ohne die Anfrage zu lesen - auch der Antragsseite "
        "selbst, die kein eigenes Privileg hat, sondern nur ein Token. Das ist "
        "der sichere Zustand und kein Fehler. Die Seite ist trotzdem begehbar: "
        "Sie sehen dort die Personen, das Formular und die Hinweise, nur der "
        "Absende-Knopf fehlt.",
        "<strong>Ingest is closed on this instance.</strong> With no ingest "
        "token configured, <code>POST /ingest</code> answers every caller with "
        "403 without reading the request - including the intake page itself, "
        "which holds no privilege of its own, only a token. That is the safe "
        "state and not a fault. The page is still walkable: the applicants, "
        "the form and the hints are all there, only the submit button is not.",
    ),
    "tour.s2.cta_closed": (
        "Antragsseite ansehen (ohne Absenden)",
        "View the intake page (without submitting)",
    ),
    "tour.s3.p1": (
        "Die Glasrohr-Ansicht zeigt sieben Schritte in der Reihenfolge, in der "
        "sie gelaufen sind: Eingang, Versiegelung, Extraktion, Evidenz, "
        "Entscheidung, Nachricht, Warteschlange. Jede Zahl darauf kommt aus "
        "dem Journal des Vorgangs. Die Seite rechnet nichts nach - sie kann "
        "dem, was tatsächlich passiert ist, gar nicht widersprechen.",
        "The glass pipeline shows seven stages in the order they ran: arrival, "
        "sealing, extraction, evidence, decision, message, queue. Every number "
        "on it comes from that case's journal. The page recomputes nothing - "
        "it cannot contradict what actually happened.",
    ),
    "tour.s3.seeded_case": (
        "Damit die sieben Schritte begehbar sind, <em>bevor</em> Sie selbst "
        "etwas eingereicht haben, zeigt dieser Rundgang einen Vorgang aus dem "
        "eingefrorenen Goldsatz: einen Antrag auf Regelaltersrente, bei dem "
        "der Rentenbeginn fehlt. Ergebnis: <strong>{tier}</strong>, zugeordnet "
        "an <strong>{unit}</strong>.",
        "So that the seven stages are walkable <em>before</em> you have "
        "submitted anything, this tour points at a case from the frozen gold "
        "corpus: an old-age pension application that arrived without its start "
        "date. Result: <strong>{tier}</strong>, routed to "
        "<strong>{unit}</strong>.",
    ),
    "tour.s3.cta": (
        "Die sieben Schritte an diesem Vorgang ansehen",
        "See the seven stages on that case",
    ),
    "tour.watch": ("Worauf Sie dort achten sollten", "What to look for there"),
    "tour.s3.w1": (
        "<strong>Schritt b, Versiegelung.</strong> Name, Geburtsdatum, "
        "Versicherungsnummer und Anschrift sind ersetzt, bevor die "
        "Arbeitskopie entstand. Die Tabelle nennt die ART des Versiegelten und "
        "die Anzahl - niemals einen Wert. Eine zweite, unabhängige "
        "Erkennerrunde über die Arbeitskopie hat nichts mehr gefunden; das ist "
        "die Nachprüfung.",
        "<strong>Stage b, sealing.</strong> Name, date of birth, insurance "
        "number and address were replaced before the working copy existed. The "
        "table names the KIND of what was sealed and how many - never a value. "
        "A second, independent detector pass over the working copy found "
        "nothing; that is the verification.",
    ),
    "tour.s3.w2": (
        "<strong>Schritt c, Extraktion.</strong> Jede ausgelesene Angabe trägt "
        "eine Fundstelle, die doppelt geprüft wurde: das wörtliche Zitat UND "
        "die Zeichenposition, unabhängig voneinander gegen dieselbe "
        "Textfassung. Wer das nicht besteht, wird verworfen statt repariert - "
        "und ein verworfener Wert schiebt den Vorgang Richtung Tier 3.",
        "<strong>Stage c, extraction.</strong> Every extracted value carries a "
        "span that was verified twice: the literal quote AND the character "
        "offsets, independently, against the same text. Anything that fails is "
        "discarded rather than repaired - and a discarded value pushes the "
        "case towards tier 3.",
    ),
    "tour.s3.w3": (
        "<strong>Schritt d, Evidenz.</strong> Die gemeldete Lücke steht mit "
        "dem Satz da, den die Nachforderung dazu stellen würde. Der Satz kommt "
        "aus der Verfahrenskonfiguration, nicht aus einem Modell.",
        "<strong>Stage d, evidence.</strong> The reported gap is shown "
        "together with the sentence the request for information would use. "
        "That sentence comes from the procedure configuration, not from a "
        "model.",
    ),
    "tour.s3.w4": (
        "<strong>Schritt e, Entscheidung.</strong> Die Begründungen der "
        "Entscheidungstabelle stehen in Auswertungsreihenfolge, mit der "
        'Zeilen-ID, die gegriffen hat. Das ist der Punkt, an dem "erklärbar" '
        "aufhört, ein Versprechen zu sein.",
        "<strong>Stage e, decision.</strong> The decision table's reasons are "
        "listed in evaluation order, with the id of the row that fired. That "
        'is the point where "explainable" stops being a promise.',
    ),
    "tour.s4.p1": (
        "Jetzt wechseln Sie die Rolle. Die Sachbearbeitungsoberfläche zeigt "
        "Warteschlangen je Organisationseinheit, ältester Vorgang zuerst, plus "
        "eine Zentrale Klärung für alles, was keine Regel zuordnen konnte "
        "(par. 16 Abs. 2 S. 1 SGB I verpflichtet die unzuständige Stelle zur "
        "unverzüglichen Weiterleitung). In der Vorgangsansicht stehen "
        "sämtliche Belege über den drei Aktionen: bestätigen, umsteuern, "
        "eskalieren.",
        "Now you change hats. The caseworker surface shows queues per "
        "organisational unit, oldest case first, plus a central clearing queue "
        "for anything no rule could route (par. 16 Abs. 2 S. 1 SGB I obliges "
        "the wrong authority to forward without delay). The case view puts "
        "every piece of evidence above the three actions: confirm, re-route, "
        "escalate.",
    ),
    "tour.s4.p2": (
        "Jede Aktion schreibt ein <em>neues</em> Journalereignis. Keine ändert "
        "ein altes und keine löscht eines: eine Korrektur ist ein Eintrag "
        "neben der alten Entscheidung, nicht an ihrer Stelle (ADR-008). "
        "Deshalb gibt es auch keinen Knopf, der eine Bestätigung rückgängig "
        "macht.",
        "Each action appends a <em>new</em> journal event. None edits an old "
        "one and none deletes one: a correction is an entry beside the earlier "
        "decision, never in its place (ADR-008). Which is also why there is no "
        "button that undoes a confirmation.",
    ),
    "tour.s4.cta": ("Zur Warteschlange {unit}", "To the {unit} queue"),
    "tour.s4.direct": (
        "Oder direkt in die Vorgangsansicht desselben Vorgangs:",
        "Or straight into the case view of the same case:",
    ),
    "tour.s4.case": ("Vorgang {case_id}", "Case {case_id}"),
    "tour.s4.review": ("Zur Sachbearbeitung", "To the caseworker screens"),
    "tour.s4.w1": (
        "<strong>Das Umsteuern verlangt eine Begründung, das Eskalieren "
        "nicht.</strong> Diese Asymmetrie ist dieselbe wie beim Einwegventil: "
        "vor die sichere Richtung - mehr Prüfung - gehört keine Hürde.",
        "<strong>Re-routing demands a reason, escalating does not.</strong> "
        "The asymmetry is the one-way valve again: nothing may stand in front "
        "of the safe direction, which is more human review.",
    ),
    "tour.s4.w2": (
        "<strong>Der Ähnlichkeitsvorschlag steht sichtbar da und hat nichts "
        "entschieden.</strong> Er ist eingerahmt und beschriftet, damit ihm "
        "widersprochen werden kann - nicht, damit er mitentscheidet (ADR-021).",
        "<strong>The similarity suggestion is visible and decided "
        "nothing.</strong> It is fenced and labelled so that it can be "
        "contradicted, not so that it can vote (ADR-021).",
    ),
    "tour.s4.w3": (
        "<strong>Im Journal steht keine natürliche Person.</strong> Akteur ist "
        "immer das System oder eine Organisationseinheit; ein Feld für eine "
        "benannte Person gibt es nicht (BPersVG par. 80 Abs. 1 Nr. 21).",
        "<strong>No natural person appears in the journal.</strong> The actor "
        "is always the system or an organisational unit; there is no field for "
        "a named individual (BPersVG par. 80 Abs. 1 Nr. 21).",
    ),
    "tour.s4.w4": (
        "<strong>Vor dem Bestätigen steht die Fristarithmetik.</strong> Vier "
        "Tage Bekanntgabefiktion (par. 37 Abs. 2 SGB X), dann die Antwortfrist, "
        "beides auf den nächsten Werktag geschoben (par. 26 Abs. 3 SGB X) - "
        "mit dem Feiertagssatz, der dabei verwendet wird, im Klartext daneben.",
        "<strong>The deadline arithmetic comes before the confirm "
        "button.</strong> Four days of statutory service fiction (par. 37 Abs. "
        "2 SGB X), then the response period, both moved to the next working "
        "day (par. 26 Abs. 3 SGB X) - with the holiday set that was used "
        "spelled out beside it.",
    ),
    "tour.s5.p1": (
        "Das simulierte Postfach zeigt, was die antragstellende Person "
        "tatsächlich erhalten hätte: die Eingangsbestätigung und - nach der "
        "Zuordnung - den Zwischenstand. Beide entstehen automatisch als "
        "Projektion des Vorgangsjournals, beide sind rein informatorisch "
        "(Realakt, kein Verwaltungsakt, ADR-005), und beider Text stammt "
        "vollständig aus einer versionierten Konfigurationsdatei. Kein "
        "Sprachmodell ist daran beteiligt.",
        "The simulated inbox shows what the applicant would actually have "
        "received: the receipt and - after routing - the status note. Both are "
        "automatic projections of the case journal, both are purely "
        "informational acts with no legal consequence (ADR-005), and every "
        "word of both comes from a versioned configuration file. No language "
        "model is involved.",
    ),
    "tour.s5.p2": (
        "Genau deshalb gibt es auf dieser Seite <em>keine</em> Bedienung: kein "
        "Formular, keine Schaltfläche, nichts, womit jemand eine Nachricht "
        "auslösen, ändern oder erneut senden könnte. Alles mit "
        "Verfahrensfolge geht den schriftformgebundenen Weg (par. 36a SGB I) "
        "und nicht über diese Seite.",
        "Which is exactly why that page has <em>no</em> controls: no form, no "
        "button, nothing with which anybody could trigger, edit or re-send a "
        "message. Anything with procedural consequence goes the written-form "
        "route (par. 36a SGB I) and not through this page.",
    ),
    "tour.s5.cta": ("Zum Postfach (nur Ansicht)", "To the inbox (read-only)"),
    "tour.s6.p1": (
        "Eine Demo, die nur gut aussieht, ist ein Prospekt. Was diese hier von "
        "einem Prospekt unterscheidet, sind fünf Dinge, die alle nachprüfbar "
        "sind:",
        "A demo that only looks good is a brochure. Five things separate this "
        "one from a brochure, and all five are checkable:",
    ),
    "tour.s6.d1.title": ("Alle Daten sind synthetisch", "All data is synthetic"),
    "tour.s6.d1.body": (
        "Der gesamte Bestand stammt aus dem eingefrorenen Goldsatz "
        "<code>{gold_dir}</code>. Es gibt hier keine echte Person und keinen "
        "echten Vorgang. Die erfundenen Personen der Antragsseite sind "
        "zusätzlich daraufhin geprüft, dass keiner ihrer Werte irgendwo im "
        "Goldsatz oder im Kanarienvogel-Satz vorkommt - sonst könnte eine "
        "Leck-Prüfung ein Leck nicht von einer Persona unterscheiden.",
        "The whole state comes from the frozen gold corpus "
        "<code>{gold_dir}</code>. There is no real person and no real case "
        "here. The fictional applicants on the intake page are additionally "
        "checked to make sure none of their values occurs anywhere in the gold "
        "corpus or in the canary set - otherwise a leak sweep could not tell a "
        "leak from a persona.",
    ),
    "tour.s6.d2.title": (
        "Der Eingang ist bewusst verschlossen",
        "Ingest is deliberately closed",
    ),
    "tour.s6.d2.body": (
        "<code>POST /ingest</code> antwortet ohne gültiges Token mit 403, und "
        "zwar <em>bevor</em> der Anfragekörper gelesen wird - die Prüfung ist "
        "eine Middleware und keine Routen-Abhängigkeit, weil ein Framework den "
        "Körper sonst zuerst dekodiert. Diese Instanz kann keinen echten, "
        "versehentlich abgeschickten Antrag entgegennehmen.",
        "<code>POST /ingest</code> answers 403 without a valid token, and it "
        "does so <em>before</em> the request body is read - the check is "
        "middleware and not a route dependency, because a framework would "
        "otherwise decode the body first. This instance cannot receive a real "
        "application submitted by accident.",
    ),
    "tour.s6.d3.title": ("Vier Gates, bei jedem Commit", "Four gates, every commit"),
    "tour.s6.d3.body": (
        "Der Eval-Lauf über dem eingefrorenen Satz bricht ab, wenn eines von "
        "vier Dingen sich bewegt: die False-Clear-Rate (Budget null, "
        "dauerhaft), der Redaktions-Recall der deterministischen Erkenner, die "
        "Regressions-Identität der rein strukturierten Teilmenge, und die "
        "Lesbarkeit jeder Scorer-Markierung. Die Zahlen des letzten Laufs "
        "stehen auf der Metrikseite - die Seite rechnet sie nicht, sie zeigt "
        "sie.",
        "The evaluation run over the frozen corpus fails if any of four things "
        "moves: the false-clear rate (budget zero, permanently), the redaction "
        "recall of the deterministic recognizers, the regression identity of "
        "the purely structured subset, and the readability of every scorer "
        "flag. The numbers of the last run are on the metrics page - which "
        "does not compute them, it shows them.",
    ),
    "tour.s6.d4.title": (
        "Ein Neustart setzt alles zurück",
        "A restart resets everything",
    ),
    "tour.s6.d4.body": (
        "Der Bestand wird beim Start aus dem Goldsatz neu aufgebaut. Was Sie "
        "hier bestätigen oder umsteuern, verschwindet damit wieder - deshalb "
        "sind die Aktionen überhaupt offen. Der Zwischenspeicher der "
        "Antragsseite lebt ausschließlich im Arbeitsspeicher und überlebt "
        "keinen Prozess.",
        "The state is rebuilt from the gold corpus at every start. Whatever "
        "you confirm or re-route here disappears with it - which is the only "
        "reason those actions can be left open at all. The intake page's "
        "scratch store lives in memory alone and survives no process.",
    ),
    "tour.s6.d5.title": (
        "Der Quellcode ist offen und vollständig dokumentiert",
        "The source is open and fully documented",
    ),
    "tour.s6.d5.body": (
        "Lizenz EUPL-1.2. Architekturentscheidungen, die Spezifikation, die "
        "bekannten Fehler und die Barrierefreiheits-Selbsteinschätzung liegen "
        "im Repository.",
        "Licence EUPL-1.2. The architecture decision records, the "
        "specification, the known errors and the accessibility self-assessment "
        "are in the repository.",
    ),
    "tour.s6.cta": ("Zu den Eval-Metriken", "To the evaluation metrics"),
    "tour.s6.a11y": (
        "Die Barrierefreiheits-Selbsteinschätzung nach EN 301 549 / WCAG 2.1 "
        "AA ist eine <strong>Selbsteinschätzung</strong> und kein Test nach "
        "BITV 2.0. Eine externe Prüfung durch eine akkreditierte Stelle hat "
        "nicht stattgefunden und ist Pilotvoraussetzung. Das steht hier und "
        "nicht in einer Fußnote, weil ein System, das Vertrauenswürdigkeit "
        "behauptet, über seine eigenen Ränder ehrlich sein muss.",
        "The accessibility self-assessment against EN 301 549 / WCAG 2.1 AA is "
        "a <strong>self-assessment</strong> and not a BITV 2.0 test. No "
        "external audit by an accredited body has taken place, and one is a "
        "pilot prerequisite. It says so here and not in a footnote, because a "
        "system that claims trustworthiness has to be honest about its own "
        "edges.",
    ),
    "tour.ingest.open": (
        "Diese Bereitstellung nimmt Anträge entgegen: Sie können den Rundgang "
        "mit Ihrem EIGENEN Vorgang laufen, von der Einreichung bis zur "
        "Eingangsbestätigung im Postfach.",
        "This deployment accepts submissions: you can walk the tour with YOUR "
        "OWN case, from the submission to the receipt in the inbox.",
    ),
    "tour.ingest.closed": (
        "Diese Bereitstellung nimmt zurzeit keine Anträge entgegen: ohne "
        "konfiguriertes Ingest-Token ist der Eingang für jeden Aufrufer "
        "gesperrt, auch für die Antragsseite selbst. Der Rundgang funktioniert "
        "trotzdem vollständig - die Schritte 3 bis 6 laufen über den "
        "eingefrorenen Goldsatz, der beim Start eingespielt wurde.",
        "This deployment currently accepts no submissions: with no ingest "
        "token configured, ingest is closed to every caller, including the "
        "intake page itself. The tour still works in full - steps 3 to 6 run "
        "over the frozen gold corpus that was seeded at start-up.",
    ),
    "tour.seeded": (
        "Der Vorgang, auf den dieser Schritt zeigt, stammt aus dem "
        "eingefrorenen Goldsatz und nicht aus einer Eingabe von Ihnen. Deshalb "
        "fehlt dort die Gegenüberstellung von eingegebenem Wert und "
        "Arbeitskopie: dieser Zwischenspeicher hält ausschließlich, was eine "
        "Besucherin oder ein Besucher selbst getippt hat, und zwar nur für "
        "kurze Zeit im Arbeitsspeicher. Alles Übrige - Versiegelung, "
        "Fundstellen, Lücken, Zuordnung, Entscheidung, Nachrichten - kommt aus "
        "dem Journal und steht vollständig da.",
        "The case this step points at comes from the frozen gold corpus and "
        "not from anything you typed. That is why the side-by-side comparison "
        "of typed value and working copy is missing over there: that scratch "
        "store holds only what a visitor typed themselves, and only for a "
        "short time, in memory. Everything else - sealing, spans, gaps, "
        "routing, decision, messages - comes from the journal and is fully "
        "there.",
    ),
    "tour.unseeded": (
        "Auf dieser Instanz ist kein Goldsatz eingespielt, deshalb gibt es "
        "hier keinen vorbereiteten Vorgang zum Mitlaufen. Stellen Sie einen "
        "Antrag (Schritt 2) oder spielen Sie den Bestand mit dem Befehl "
        "python -m engine.demo.seed ein; die Sachbearbeitungsoberfläche ist in "
        "beiden Fällen erreichbar.",
        "No gold corpus has been seeded on this instance, so there is no "
        "prepared case to walk along with. Submit an application (step 2) or "
        "seed the state with the command python -m engine.demo.seed; the "
        "caseworker surface is reachable either way.",
    ),
    # ----------------------------------------------------------- the intake --
    "intake.title": (
        "EingangsLotse - Antrag stellen (Phase 1)",
        "EingangsLotse - submit an application (phase 1)",
    ),
    "intake.headline": (
        "Phase 1: Sie stellen einen Antrag",
        "Phase 1: you submit an application",
    ),
    "intake.what.heading": ("Was hier passiert", "What happens here"),
    "intake.ingest.open": (
        "Ihr Antrag geht durch dieselbe Verarbeitung wie jeder andere Eingang: "
        "versiegeln, Arbeitskopie, Auslesen, Belegen, Entscheiden, "
        "Benachrichtigen. Diese Seite legt das Token dieser Bereitstellung "
        "serverseitig bei; der rohe Endpunkt POST /ingest bleibt für direkte "
        "Aufrufer mit 403 gesperrt.",
        "Your application goes through the same processing as any other "
        "incoming case: seal, working copy, extract, prove, decide, notify. "
        "This page presents the deployment's own token server-side; the raw "
        "endpoint POST /ingest stays closed with a 403 for direct callers.",
    ),
    "intake.ingest.closed": (
        "Diese Instanz nimmt keine Anträge entgegen: es ist kein Ingest-Token "
        "konfiguriert, und ohne Token ist der Eingang für jeden Aufrufer "
        "gesperrt - auch für diese Seite. Das ist der sichere Zustand und kein "
        "Fehler. Die Phasen 2 und 3 können Sie trotzdem begehen: der "
        "Datenbestand aus dem eingefrorenen Goldsatz steht in der "
        "Sachbearbeitung und im Postfach.",
        "This instance accepts no applications: no ingest token is configured, "
        "and without one ingest is closed to every caller - this page "
        "included. That is the safe state and not a fault. Phases 2 and 3 are "
        "still walkable: the state seeded from the frozen gold corpus is in "
        "the caseworker queues and in the inbox.",
    ),
    "intake.refusal.heading": (
        "Der Eingang wurde abgelehnt",
        "The submission was refused",
    ),
    "intake.persona.heading": (
        "Erfundene Person wählen",
        "Choose a fictional applicant",
    ),
    "intake.persona.chosen": ("gewählt", "chosen"),
    "intake.persona.expectation": ("Erwartetes Ergebnis:", "Expected outcome:"),
    "intake.channel.heading": ("Weg wählen", "Choose a channel"),
    "intake.form.heading": (
        "Formular bearbeiten und absenden",
        "Edit the form and submit",
    ),
    "intake.letter.heading": (
        "Anschreiben bearbeiten und absenden",
        "Edit the letter and submit",
    ),
    "intake.form.legend": ("Angaben von {name}", "What {name} submitted"),
    "intake.body.label": ("Ihr Anschreiben", "Your letter"),
    "intake.body.help": (
        "Frei bearbeitbar. Alles, was Sie hier hineinschreiben, geht durch die "
        "Erkennerunion: was wie eine Versicherungsnummer, ein Name, eine "
        "Anschrift oder ein Geburtsdatum aussieht, wird versiegelt, bevor die "
        "Arbeitskopie entsteht.",
        "Freely editable. Everything you write here goes through the detector "
        "union: whatever looks like an insurance number, a name, an address or "
        "a date of birth is sealed before the working copy exists.",
    ),
    "intake.submit": ("Antrag absenden", "Submit the application"),
    "intake.closed_button": (
        "Absenden ist auf dieser Instanz nicht möglich: der Eingang ist gesperrt.",
        "Submitting is not possible on this instance: ingest is closed.",
    ),
    "intake.sealed_as": ("wird versiegelt: {kind}", "will be sealed: {kind}"),
    "intake.name.legend": ("Name der antragstellenden Person", "Applicant's name"),
    "intake.date.hint": (
        "Datumsformat JJJJ-MM-TT, falls Ihr Browser keine Kalenderauswahl anbietet.",
        "Date format YYYY-MM-DD if your browser offers no date picker.",
    ),
    "intake.select.hint": (
        "Die Auswahl kommt aus der Verfahrenskonfiguration; sie füllt das Feld "
        "genau so, wie Tippen es füllen würde.",
        "The options come from the procedure configuration; picking one fills "
        "the field exactly as typing would.",
    ),
    "intake.required.note": (
        "Was hier schon ausgefüllt ist, ist ein Pflichtfeld. Leeren Sie eines "
        "und versuchen Sie abzuschicken: Ihr Browser markiert das Feld rot und "
        "sendet nichts. Ausgenommen sind genau die drei Felder, deren Löschung "
        "die Hinweise unten vorschlagen - Versicherungsnummer, Auftraggeber "
        "und Vorname: sie lassen sich leer abschicken, weil sonst ein Hinweis "
        "auf dieser Seite etwas empfehlen würde, was dieselbe Seite blockiert. "
        "Diese Prüfung findet im Browser statt, ohne JavaScript; die "
        "Vollständigkeitsprüfung der Anlage läuft danach und noch einmal "
        "getrennt davon.",
        "What is already filled in here is a required field. Empty one and try "
        "to submit: your browser marks the field red and sends nothing. The "
        "exceptions are exactly the three fields the suggestions below ask you "
        "to delete - insurance number, client and given name: those can be sent "
        "empty, because otherwise a suggestion on this page would recommend "
        "something the same page blocks. That check happens in the browser, "
        "without JavaScript; the system's own completeness check runs "
        "afterwards and separately from it.",
    ),
    "intake.required.error": (
        "Diese Angabe fehlt. Ohne sie wird der Antrag nicht abgeschickt.",
        "This answer is missing. The application will not be sent without it.",
    ),
    "intake.attachments.legend": (
        "Anlagen (PDF, simuliert)",
        "Enclosures (PDF, simulated)",
    ),
    "intake.attachments.note": (
        "Wählen Sie aus, was die antragstellende Person beilegt. Die Dokumente "
        "sind vorbereitet und synthetisch; ihre Namen sind die, unter denen die "
        "Rentenversicherung diese Unterlagen tatsächlich anfordert. Ein "
        "angehaktes Dokument wird zu einer echten Anlage der Einreichung: es "
        "wird versiegelt, bekommt eine Textebene und läuft durch dieselbe "
        "Verarbeitung wie das Formular. Im nächsten Schritt sehen Sie die "
        "Arbeitskopie jedes Dokuments.",
        "Choose what the applicant encloses. The documents are prepared and "
        "synthetic; their names are the ones the German pension insurance "
        "really asks for these papers under. A ticked document becomes a real "
        "attachment on the submission: it is sealed, gets a text layer and goes "
        "through the same processing as the form. The next step shows you the "
        "working copy of each document.",
    ),
    "intake.attachments.no_upload": (
        "Eigene Dateien lassen sich hier bewusst nicht hochladen. Ein "
        "Upload-Feld auf einer öffentlichen Demo wäre ein Eingangsweg an der "
        "Schwärzungsgrenze vorbei, und die Grenze ist der ganze Gegenstand "
        "dieser Anlage. Die Dokumente oben gehören zu der erfundenen Person, "
        "die Sie gewählt haben, und zu keinem Menschen.",
        "There is deliberately no way to upload a file of your own here. An "
        "upload control on a public demo would be an ingest path around the "
        "redaction boundary, and that boundary is what this whole system is "
        "about. The documents above belong to the fictional applicant you "
        "picked and to nobody.",
    ),
    "intake.hints.heading": (
        "Was Sie ausprobieren können",
        "What you can try",
    ),
    "intake.hints.lead": (
        "Ändern Sie die Angaben ruhig. Jede der folgenden Änderungen löst ein "
        "ANDERES, echtes Verhalten der Anlage aus - und Sie sehen im nächsten "
        "Schritt genau, welches.",
        "Go ahead and change things. Each of the following produces a "
        "DIFFERENT, real behaviour of the system - and the next step shows you "
        "exactly which one.",
    ),
    "intake.refused.redaction": (
        "Der Eingang wurde ABGELEHNT. Die Schwärzungsprüfung konnte die "
        "Arbeitskopie nicht als sauber bestätigen: nach dem Versiegeln waren "
        "noch identitätsbezogene Angaben auffindbar. Es wurde nichts "
        "gespeichert, kein Vorgang angelegt und kein Journaleintrag "
        "geschrieben. Unten steht, welche ART von Angabe an welcher Stelle "
        "gefunden wurde - der gefundene Wert selbst steht bewusst nirgends.",
        "The submission was REFUSED. The redaction check could not confirm the "
        "working copy clean: after sealing, identity-bearing content was still "
        "findable. Nothing was stored, no case was created and no journal "
        "entry was written. Below is which KIND of content was found and "
        "where - the value itself deliberately appears nowhere.",
    ),
    "intake.refused.envelope": (
        "Der Eingang wurde ABGELEHNT: die Einreichung entspricht nicht dem "
        "erwarteten Aufbau. Unten steht, an welcher Stelle und welche Regel - "
        "der eingegebene Wert steht bewusst nirgends, auch nicht in einer "
        "Fehlermeldung.",
        "The submission was REFUSED: it does not match the expected structure. "
        "Below is where and which rule - the submitted value deliberately "
        "appears nowhere, not even in an error message.",
    ),
    # --------------------------------------------------------- the pipeline --
    "pipeline.title": (
        "EingangsLotse - Was mit Ihrem Antrag passiert ist",
        "EingangsLotse - what happened to your application",
    ),
    "pipeline.headline": (
        "Phase 2: Was mit Ihrem Antrag passiert ist",
        "Phase 2: what happened to your application",
    ),
    "pipeline.overview.heading": ("Überblick", "Overview"),
    "pipeline.overview.body": (
        "Sieben Schritte, in der Reihenfolge, in der sie gelaufen sind. Jede "
        "Zahl und jeder Satz auf dieser Seite kommt aus dem Journal Ihres "
        "Vorgangs oder aus einer Ablage, die es ohnehin gibt. Diese Seite "
        "rechnet nichts nach: sie kann dem, was tatsächlich passiert ist, "
        "nicht widersprechen.",
        "Seven stages, in the order they ran. Every number and every sentence "
        "on this page comes from your case's journal or from a store that "
        "exists anyway. This page recomputes nothing: it cannot contradict "
        "what actually happened.",
    ),
    "pipeline.sampled": (
        "Dieser Vorgang wurde zufällig zur Qualitätssicherung ausgewählt. Das "
        "ist KEIN Auffälligkeitsbefund: die Ziehung hängt allein an der "
        "Vorgangskennung und sagt nichts über Sie oder Ihren Antrag aus.",
        "This case was drawn at random for quality assurance. That is NOT an "
        "anomaly finding: the draw depends on the case id alone and says "
        "nothing about you or your application.",
    ),
    "pipeline.expired": (
        "Die Arbeitskopie zu diesem Vorgang wird nicht mehr vorgehalten. Der "
        "Zwischenspeicher dieser Demo hält sie nur für kurze Zeit und "
        "ausschließlich im Arbeitsspeicher; alles Übrige auf dieser Seite "
        "kommt aus dem Journal und bleibt lesbar.",
        "The working copy for this case is no longer held. This demo's scratch "
        "store keeps it for a short time and in memory only; everything else "
        "on this page comes from the journal and stays readable.",
    ),
    "pipeline.a.heading": ("a) Eingang", "a) Arrival"),
    "pipeline.a.body": (
        "Ihr Antrag ist angekommen und hat eine Vorgangskennung bekommen; ab "
        "hier ist jeder Schritt einzeln nachlesbar.",
        "Your application arrived and was given a case id; from here on every "
        "step can be read individually.",
    ),
    "pipeline.a.case": ("Vorgang", "Case"),
    "pipeline.a.channel": ("Weg", "Channel"),
    "pipeline.a.received": ("Eingang am", "Received at"),
    "pipeline.a.as": ("Eingereicht als", "Submitted as"),
    "pipeline.a.persona": ("erfundene Person", "fictional applicant"),
    "pipeline.a.events": ("Ereignisse im Journal", "Journal events"),
    "pipeline.b.heading": ("b) Versiegelung", "b) Sealing"),
    "pipeline.b.seal_sentence": (
        "Die Maschine hat Ihren Namen nie gesehen. Was Sie oben eingegeben "
        "haben, wurde am Eingang versiegelt - bevor die Arbeitskopie entstand, "
        "auf der alles Weitere rechnet.",
        "The machine never saw your name. What you entered above was sealed at "
        "the boundary - before the working copy existed, which is what "
        "everything downstream computes on.",
    ),
    "pipeline.b.sealed": ("Versiegelte Werte", "Values sealed"),
    "pipeline.b.verified": ("Nachprüfung", "Verification"),
    "pipeline.b.verified.yes": (
        "bestanden - ein zweiter, unabhängiger Erkennerlauf über die "
        "Arbeitskopie fand nichts mehr",
        "passed - a second, independent detector pass over the working copy "
        "found nothing",
    ),
    "pipeline.b.verified.no": (
        "nicht bestanden oder nicht protokolliert",
        "not passed, or not recorded",
    ),
    "pipeline.b.pairs.heading": (
        "Ihre Eingabe und das, was die Maschine bekommen hat",
        "What you typed, and what the machine received",
    ),
    "pipeline.b.pairs.body": (
        "Links steht, was Sie selbst eingetippt haben. Rechts steht, was an "
        "dieser Stelle in der Arbeitskopie steht. Der Klartext wurde nicht aus "
        "dem Tresor geholt, um diese Tabelle zu bauen - er stammt aus Ihrer "
        "eigenen Eingabe von eben und wird nur für kurze Zeit im "
        "Arbeitsspeicher gehalten.",
        "On the left is what you typed. On the right is what stands in that "
        "place in the working copy. The plain text was not fetched from the "
        "vault to build this table - it comes from your own input a moment ago "
        "and is held in memory for a short time only.",
    ),
    "pipeline.b.pairs.caption": (
        "Gegenüberstellung: eingegebener Wert und Platzhalter",
        "Side by side: the value you entered and the placeholder",
    ),
    "pipeline.b.pairs.col1": ("Angabe", "Field"),
    "pipeline.b.pairs.col2": ("Von Ihnen eingegeben", "Entered by you"),
    "pipeline.b.pairs.col3": ("In der Arbeitskopie", "In the working copy"),
    "pipeline.b.pairs.none": (
        "kein Platzhalter dieser Art in der Arbeitskopie",
        "no placeholder of that kind in the working copy",
    ),
    "pipeline.b.pairs.sr": (
        "Platzhalter der Art {kind}",
        "placeholder of kind {kind}",
    ),
    "pipeline.b.letter.heading": (
        "Ihr Anschreiben, vorher und nachher",
        "Your letter, before and after",
    ),
    "pipeline.b.letter.body": (
        "Dieselben Zeichen, einmal wie Sie sie geschrieben haben und einmal "
        "wie die Maschine sie bekommen hat. Im Fließtext wird SPANNE FÜR "
        "SPANNE versiegelt, nicht der ganze Absatz: ein Brief ohne seine "
        "Verben ließe sich nicht triagieren.",
        "The same characters, once as you wrote them and once as the machine "
        "received them. In prose the sealing happens SPAN BY SPAN and not by "
        "the paragraph: a letter without its verbs could not be triaged.",
    ),
    "pipeline.b.letter.yours": ("Was Sie geschrieben haben", "What you wrote"),
    "pipeline.b.letter.machine": (
        "Was die Maschine gelesen hat",
        "What the machine read",
    ),
    "pipeline.b.copy.heading": (
        "Die Arbeitskopie, wie sie weitergereicht wurde",
        "The working copy, as it was handed on",
    ),
    "pipeline.b.copy.body": (
        "Jede Stelle, an der ein Platzhalter steht, hat vorher eine Ihrer "
        "Angaben getragen. Alles danach - Ableitung, Auslesen, Regeln, "
        "Entscheidung, Scorer - hat ausschließlich diese Fassung gelesen.",
        "Every place where a placeholder stands carried one of your values "
        "before. Everything after that - derivation, extraction, rules, "
        "decision, scorer - read this version and nothing else.",
    ),
    # Part 20 renamed these three from `pipeline.b.kinds.*` and dropped a
    # fourth. The table counts spans PER TEXT PART and always did; calling its
    # first column "Art" made the citizen page ask for `kind.part-text-0` and
    # print the key. See `api/review.py::sealed_text_parts`.
    "pipeline.b.parts.caption": (
        "Wie viele Stellen je Textteil versiegelt wurden. Werte erscheinen hier nie.",
        "How many spans were sealed in each text part. Values never appear here.",
    ),
    "pipeline.b.parts.col1": ("Textteil", "Text part"),
    "pipeline.b.parts.col2": ("Versiegelte Stellen", "Sealed spans"),
    "pipeline.c.heading": ("c) Extraktion", "c) Extraction"),
    "pipeline.c.body": (
        "Jetzt wird gelesen, was in der Arbeitskopie steht - und jede gefundene "
        "Angabe muss zweifach belegt sein: das Zitat UND die Zeichenposition, "
        "unabhängig voneinander gegen dieselbe Textfassung geprüft.",
        "Now what stands in the working copy is read - and every value found "
        "must be proven twice: the quote AND the character offsets, checked "
        "independently against the same text.",
    ),
    "pipeline.c.found": ("Gefundene Felder", "Fields found"),
    "pipeline.c.none": ("keine", "none"),
    "pipeline.c.discarded": ("Verworfen", "Discarded"),
    "pipeline.c.discarded.note": (
        "ein VERWORFENER Vorschlag ist ein Wert, dessen Fundstelle die Prüfung "
        "nicht bestanden hat. Er wird nicht übernommen und schiebt den Vorgang "
        "Richtung Tier 3.",
        "a DISCARDED proposal is a value whose span failed verification. It is "
        "not taken over and it pushes the case towards tier 3.",
    ),
    "pipeline.c.no_extraction": (
        "Aus diesem Anschreiben wurde nichts ausgelesen, und das ist kein "
        "Fehler dieser Seite. Der Leser für Freitext ist in dieser "
        "Bereitstellung ein REPLAY aufgezeichneter Modellausgaben (ADR-028): "
        "zu einem Brief, den Sie gerade selbst geschrieben haben, gibt es "
        "keine Aufzeichnung. Ein Modell raten zu lassen und das Ergebnis nicht "
        "belegen zu können, wäre die schlechtere Antwort - der Vorgang geht "
        "deshalb unvollständig zu einem Menschen.",
        "Nothing was extracted from this letter, and that is not a fault of "
        "this page. The reader for free text in this deployment is a REPLAY of "
        "recorded model output (ADR-028): for a letter you just wrote "
        "yourself, there is no recording. Letting a model guess and being "
        "unable to prove the result would be the worse answer - so the case "
        "goes to a human incomplete.",
    ),
    "pipeline.c.spans.caption": (
        "Fundstellen: Teil und Zeichenbereich, nie der Wert",
        "Spans: part and character range, never the value",
    ),
    "pipeline.c.spans.col1": ("Feld", "Field"),
    "pipeline.c.spans.col2": ("Textteil", "Text part"),
    "pipeline.c.spans.col3": ("Zeichenbereich", "Character range"),
    "pipeline.c.spans.col4": ("Prüfart", "Match mode"),
    "pipeline.c.spans.structured": ("strukturiertes Feld", "structured field"),
    "pipeline.c.spans.range": ("{start} bis {end}", "{start} to {end}"),
    "pipeline.c.spans.nospan": ("ohne Textfundstelle", "no text span"),
    "pipeline.d.heading": ("d) Evidenz", "d) Evidence"),
    "pipeline.d.body": (
        "Aus den belegten Werten wird die Beweislage: welches Verfahren, was "
        "fehlt, wer zuständig ist. Alles davon ist nachlesbar begründet - "
        "nichts davon ist schon eine Entscheidung.",
        "The proven values become the evidence: which procedure, what is "
        "missing, who is responsible. All of it is justified in readable form "
        "- and none of it is a decision yet.",
    ),
    "pipeline.d.procedure": ("Verfahren", "Procedure"),
    "pipeline.d.procedure.none": ("nicht abgeleitet", "not derived"),
    "pipeline.d.derived": (
        "abgeleitet aus: {source}; Kanalhinweis: {hint}",
        "derived from: {source}; channel hint: {hint}",
    ),
    "pipeline.d.unknown": ("unbekannt", "unknown"),
    "pipeline.d.nohint": ("keiner", "none"),
    "pipeline.d.completeness": ("Vollständigkeit", "Completeness"),
    "pipeline.d.clearcut": ("Klarfall", "Clear-cut"),
    "pipeline.d.yes": ("ja", "yes"),
    "pipeline.d.no": ("nein", "no"),
    "pipeline.d.unchecked": ("nicht geprüft", "not evaluated"),
    "pipeline.d.gaps.heading": (
        "Was fehlt, und was Sie dazu gefragt werden",
        "What is missing, and what you will be asked",
    ),
    "pipeline.d.gaps.caption": (
        "Gemeldete Lücken mit dem Satz, den die Nachforderung dazu stellt",
        "Reported gaps with the sentence the request for information uses",
    ),
    "pipeline.d.gaps.col1": ("Angabe", "Field"),
    "pipeline.d.gaps.col2": ("Status", "Status"),
    "pipeline.d.gaps.col3": ("Formulierung im Schreiben", "Wording in the letter"),
    "pipeline.d.routing.heading": ("Zuordnung", "Routing"),
    "pipeline.d.routing.body": (
        "<strong>Zugeordnet wurde: {unit}.</strong> Das ist die Antwort der "
        "Entscheidungsebene und die einzige, die den Vorgang tatsächlich in "
        "eine Warteschlange gelegt hat. Die Tabelle darunter zeigt ALLE "
        "Belege, auch die unterlegenen.",
        "<strong>Routed to: {unit}.</strong> That is the decision plane's "
        "answer and the only one that actually put the case into a queue. The "
        "table below shows ALL the evidence, including the proposals that "
        "lost.",
    ),
    "pipeline.d.routing.nounit": ("keine Einheit", "no unit"),
    "pipeline.d.routing.caption": (
        "Zuordnungsbelege mit Quelle; die zugeordnete Einheit ist gekennzeichnet",
        "Routing evidence with its source; the routed unit is marked",
    ),
    "pipeline.d.routing.col1": ("Einheit", "Unit"),
    "pipeline.d.routing.col2": ("Quelle", "Source"),
    "pipeline.d.routing.col3": ("Regel", "Rule"),
    "pipeline.d.routing.col4": ("Konfidenz", "Confidence"),
    "pipeline.d.routing.routed": ("zugeordnet", "routed"),
    "pipeline.e.heading": ("e) Entscheidung", "e) Decision"),
    "pipeline.e.body": (
        "Die Entscheidungsebene liest nur, was belegt ist, und wertet eine "
        "versionierte Tabelle Zeile für Zeile aus. Sie entscheidet nichts über "
        "Sie: sie entscheidet, wie genau ein Mensch hinsehen muss.",
        "The decision plane reads only what has been proven and evaluates a "
        "versioned table row by row. It decides nothing about you: it decides "
        "how closely a human has to look.",
    ),
    "pipeline.e.result": ("Ergebnis", "Result"),
    "pipeline.e.unit": ("Zugeordnete Einheit", "Routed unit"),
    "pipeline.e.unit.none": (
        "keine (Zentrale Klärung)",
        "none (central clearing queue)",
    ),
    "pipeline.e.reasons.caption": (
        "Begründungen der Entscheidungstabelle, in Auswertungsreihenfolge",
        "The decision table's reasons, in evaluation order",
    ),
    "pipeline.e.reasons.col1": ("Art", "Kind"),
    "pipeline.e.reasons.col2": ("Zeile", "Row"),
    "pipeline.e.reasons.col3": ("Begründung", "Reason"),
    "pipeline.e.reasons.none": (
        "Keine Begründung protokolliert.",
        "No reason was recorded.",
    ),
    "pipeline.e.valve": (
        "Das Einwegventil hat gegriffen: die Auffälligkeit hat den Vorgang von "
        "Tier {before} auf Tier {after} geschoben. Umgekehrt geht es nicht - "
        "keine Regel dieses Systems kann ein Tier senken.",
        "The one-way valve fired: the anomaly moved the case from tier "
        "{before} to tier {after}. It does not work the other way - no rule in "
        "this system can lower a tier.",
    ),
    "pipeline.e.log_only": (
        "Der Schattenscorer läuft im Modus log_only: er hat den Vorgang "
        "markiert und seinen Grund genannt, aber KEIN Tier bewegt. Das "
        "Einwegventil (ADR-004) lässt Unsicherheit ohnehin nur in eine "
        "Richtung wirken - zu einem Menschen hin, nie von ihm weg.",
        "The shadow scorer runs in log_only mode: it flagged the case and "
        "named its reason, but moved NO tier. The one-way valve (ADR-004) lets "
        "uncertainty act in one direction only anyway - towards a human, never "
        "away from one.",
    ),
    "pipeline.e.anomaly.heading": ("Auffälligkeitsprüfung", "Anomaly check"),
    "pipeline.e.anomaly.score": ("Score", "Score"),
    "pipeline.e.anomaly.flagged": ("Markiert", "Flagged"),
    "pipeline.e.anomaly.mode": ("Betriebsart", "Mode"),
    "pipeline.e.anomaly.body": (
        "Eine Markierung ist eine Beobachtung am VORGANG gegen einen "
        "Referenzbestand, kein Befund über eine Person. Der Scorer rechnet "
        "außerdem auf nichts, was Sie identifiziert: Geburtsdatum, "
        "Versicherungsnummer und Anschrift waren zu diesem Zeitpunkt längst "
        "versiegelt.",
        "A flag is an observation about the CASE against a reference "
        "population, not a finding about a person. The scorer also computes on "
        "nothing that identifies you: date of birth, insurance number and "
        "address had long been sealed by then.",
    ),
    "pipeline.e.anomaly.noreasons": (
        "Keine Merkmalsbegründung protokolliert.",
        "No feature reason was recorded.",
    ),
    "pipeline.e.anomaly.none": (
        "Keine Auffälligkeitsprüfung protokolliert.",
        "No anomaly check was recorded.",
    ),
    "pipeline.f.heading": ("f) Nachricht", "f) Message"),
    "pipeline.f.body": (
        "Was Sie als antragstellende Person bekommen hätten. Diese Nachrichten "
        "entstehen automatisch aus dem Journal und durchlaufen keine "
        "menschliche Prüfung - genau deshalb gibt es hier und im Postfach "
        "keine Bedienung, mit der jemand eine davon auslösen, ändern oder "
        "erneut senden könnte.",
        "What you, as the applicant, would have received. These messages are "
        "produced automatically from the journal and pass no human review - "
        "which is exactly why neither this page nor the inbox has any control "
        "with which somebody could trigger, edit or re-send one.",
    ),
    "pipeline.f.caption": (
        "Zugestellte Nachrichten zu diesem Vorgang",
        "Messages delivered for this case",
    ),
    "pipeline.f.col1": ("Vorlage", "Template"),
    "pipeline.f.col2": ("Betreff", "Subject"),
    "pipeline.f.col3": ("Zugestellt", "Delivered"),
    "pipeline.f.link": ("Im Postfach nachlesen:", "Read it in the inbox:"),
    "pipeline.f.link.inbox": ("Postfach (nur Ansicht)", "Inbox (read-only)"),
    "pipeline.f.link.json": (
        "nur dieser Vorgang als JSON",
        "this case alone, as JSON",
    ),
    "pipeline.f.none": (
        "Zu diesem Vorgang wurde keine Nachricht zugestellt.",
        "No message was delivered for this case.",
    ),
    "pipeline.g.heading": ("g) Warteschlange", "g) Queue"),
    "pipeline.g.body": (
        "Ihr Vorgang liegt jetzt in der Warteschlange von <strong>{queue}"
        "</strong> und wartet auf einen Menschen. Im nächsten Schritt sind Sie "
        "dieser Mensch.",
        "Your case is now in the <strong>{queue}</strong> queue, waiting for a "
        "human. In the next step, you are that human.",
    ),
    "pipeline.g.note": (
        "Die Warteschlange ist nach Alter geordnet, ältester Vorgang zuerst. "
        "Ihr Vorgang wird dort gekennzeichnet, damit Sie ihn finden - die "
        "Kennzeichnung ist reine Anzeige und verändert die Reihenfolge nicht.",
        "The queue is ordered by age, oldest case first. Your case is marked "
        "there so that you can find it - the marker is display only and "
        "changes no ordering.",
    ),
    "pipeline.g.handover": (
        "Weiter zu Phase 3: Vorgang bearbeiten",
        "On to phase 3: work the case",
    ),
    "pipeline.g.after": (
        "Dort können Sie bestätigen, umsteuern oder eskalieren. Jede dieser "
        "Aktionen schreibt ein neues Journalereignis; keine ändert ein altes. "
        "Danach schließt sich der Kreis im",
        "There you can confirm, re-route or escalate. Each of those actions "
        "appends a new journal event; none edits an old one. After that, the "
        "loop closes in the",
    ),
    "pipeline.g.after.link": (
        "Postfach dieses Vorgangs",
        "inbox for this case",
    ),
    # ------------------------------------------------------------ the inbox --
    "inbox.title": ("EingangsLotse - Postfach", "EingangsLotse - applicant inbox"),
    "inbox.headline": (
        "Simuliertes Postfach der antragstellenden Person",
        "The applicant's simulated inbox",
    ),
    "inbox.lead": (
        "Was eine antragstellende Person erhalten hätte. Diese Seite zeigt nur "
        "an, was tatsächlich zugestellt wurde; sie erzeugt nichts und ändert "
        "nichts.",
        "What an applicant would have received. This page only displays what "
        "was actually delivered; it produces nothing and changes nothing.",
    ),
    "inbox.realakt": (
        "Beide Nachrichtenarten sind <strong>rein informatorisch</strong> "
        "(ADR-005): Eingangsbestätigung und Zwischenstand sind Realakte, keine "
        "Verwaltungsakte. Sie entstehen automatisch aus dem Vorgangsjournal, "
        "sie durchlaufen keine Prüfung durch einen Menschen, und sie enthalten "
        "weder eine Rechtsfolge noch eine Aufforderung. Alles mit "
        "Verfahrensfolge geht den schriftformgebundenen Weg (par. 36a SGB I) "
        "und nicht über diese Seite.",
        "Both message kinds are <strong>purely informational</strong> "
        "(ADR-005): the receipt and the status note are factual acts, not "
        "administrative acts. They are produced automatically from the case "
        "journal, they pass no human review, and they carry neither a legal "
        "consequence nor a demand. Anything with procedural consequence goes "
        "the written-form route (par. 36a SGB I) and not through this page.",
    ),
    "inbox.messages": ("Nachrichten", "Messages"),
    # The reload affordance (part 17). A LINK, not a form and not a button:
    # this page has neither and gains neither (ADR-005). Messages arrive here
    # as a consequence of actions taken on other screens, so a reader does
    # need a way to ask again - and a plain anchor to the same URL is that,
    # with no control semantics at all.
    "inbox.reload": ("Neu laden", "Reload"),
    "inbox.rendered_at": (
        "Stand dieser Anzeige: {at} (Serveruhr beim Rendern).",
        "This view was rendered at {at} (server clock).",
    ),
    "inbox.empty": (
        "Noch keine Nachricht zugestellt. Sobald ein Vorgang eingeht, entstehen "
        "Eingangsbestätigung und - nach der Zuordnung - der Zwischenstand.",
        "No message delivered yet. As soon as a case arrives, the receipt and "
        "- after routing - the status note are produced.",
    ),
    "inbox.count": (
        "{count} Nachricht(en) in {cases} Vorgang/Vorgängen. Speicher:",
        "{count} message(s) across {cases} case(s). Store:",
    ),
    "inbox.case": ("Vorgang {case_id}", "Case {case_id}"),
    "inbox.subject": ("Betreff", "Subject"),
    "inbox.template": ("Vorlage", "Template"),
    "inbox.cause": ("Anlass", "Cause"),
    "inbox.cause.event": ("Journalereignis", "Journal event"),
    "inbox.channel": ("Weg", "Channel"),
    "inbox.delivered": ("Zugestellt", "Delivered"),
    "inbox.nothing": (
        "Keine Nachricht zu diesem Vorgang.",
        "No message for this case.",
    ),
    "inbox.footer": (
        "Der Nachrichtentext stammt vollständig aus "
        "<code>config/notifications/notifications_v1.yaml</code>. Es wird kein "
        "Sprachmodell verwendet (Art. 50 KI-VO, C-13), und es wird nichts aus "
        "dem Identitäts-Tresor nachgeladen.",
        "The message text comes entirely from "
        "<code>config/notifications/notifications_v1.yaml</code>. No language "
        "model is involved (Art. 50 AI Act, C-13) and nothing is re-loaded "
        "from the identity vault.",
    ),
    "inbox.german_note": (
        "Die Nachrichtentexte selbst bleiben deutsch: sie stammen aus einer "
        "versionierten Konfiguration und sind Rechtstexte, keine "
        "Oberflächentexte.",
        "The message bodies themselves stay German: they come from versioned "
        "configuration and are legal-text artifacts rather than interface "
        "text.",
    ),
}


def phrase(key: str, lang: str = DEFAULT_LANGUAGE, **values: object) -> str:
    """One phrase, without a page context. For call sites outside a request."""
    return PageContext(lang=lang).t(key, **values)
