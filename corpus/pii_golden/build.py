"""Seeded German-PII golden set: ``python -m corpus.pii_golden.build``.

P-7 asks for a measured recall number, and a measured number needs a labelled
set. This builder writes one, under the same discipline as the triage corpus
generator (``corpus/generator/build.py``): an explicit seed, no wall clock, and
a self-check that refuses to write anything when the result disagrees with what
it claims.

What makes the labels trustworthy is that they are never searched for. Every
snippet is composed from a list of chunks, some of which are ``(kind, value)``
pairs, and the offsets fall out of the concatenation. A builder that produced
text and then ran a regular expression over it to find its own labels would be
measuring the regular expression against itself.

Three ingredient classes:

* **checksum-valid identifiers** - the Versicherungsnummern, Steuer-IDs and
  IBANs are computed with their real check digits, so the VERIFY profile has
  something to be precise about;
* **one deliberately mistyped Versicherungsnummer**, labelled as a positive.
  The REDACT profile must find it: a typo does not make a number less
  identifying, and this item is what keeps the recall-first rule honest;
* **hard negatives** - procedural dates, eight-digit amounts, Kundennummern,
  Beitragssaetze, form numbers. Zero detections are allowed on those, and the
  build aborts if any recognizer fires. Note what is deliberately NOT among
  them: a bare eleven-digit run is Steuer-ID-shaped, the recall-first profile is
  supposed to seal it even when the check digit fails, and calling that a
  negative would push the REDACT profile toward checksum gating - which is the
  one thing it must not do.

Everything in here is invented. The values are synthetic by construction and
none of them was taken from a document.

Usage::

    python -m corpus.pii_golden.build            # write items.yaml + MANIFEST
    python -m corpus.pii_golden.build --check    # verify, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path
from random import Random

import yaml

from engine.redact.detector import Detector
from engine.redact.placeholders import Kind
from engine.redact.recall import (
    DETERMINISTIC_GATE_KINDS,
    Label,
    LabelledText,
    measure,
)
from engine.redact.recognizers import (
    Profile,
    iban_check_digits,
    steuer_id_check_digit,
    vsnr_check_digit,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "corpus" / "pii_golden"
ITEMS_NAME = "items.yaml"
MANIFEST_NAME = "MANIFEST.yaml"
GENERATOR_VERSION = "pii_golden_v1"
DEFAULT_SEED = 20260811

HEADER = (
    "# Seeded German-PII golden set for the redaction recall metric (P-7).\n"
    "# GENERATED - do not edit by hand.\n"
    "# Rebuild with: python -m corpus.pii_golden.build --seed {seed}\n"
    "# Every value is synthetic. Labels are char spans produced by composition,\n"
    "# never by searching the text, so they cannot agree with a broken detector.\n"
)


class BuildError(RuntimeError):
    """Raised when the set does not measure up to what it claims."""


# ----------------------------------------------------------- value factories ---

#: Two-digit Bereichsnummern of the DRV; none below 10, so the digit run cannot
#: also read as a phone number and make the fixture about the merge rule.
BEREICH = ("11", "12", "13", "15", "16", "17", "18", "19", "21", "23", "24", "25")
LETTERS = "ABCDEFGHKLMNPRSTUVWZ"


def make_vsnr(rng: Random) -> str:
    """A Versicherungsnummer with a real Pruefziffer and a real birth date."""
    prefix = (
        f"{rng.choice(BEREICH)}"
        f"{rng.randint(1, 28):02d}{rng.randint(1, 12):02d}{rng.randint(40, 75):02d}"
        f"{rng.choice(LETTERS)}{rng.randint(0, 99):02d}"
    )
    return prefix + vsnr_check_digit(prefix)


def make_steuer_id(rng: Random) -> str:
    """An eleven-digit Steuer-ID that passes ISO 7064 MOD 11,10."""
    first_ten = f"{rng.randint(1, 9)}" + "".join(
        str(rng.randint(0, 9)) for _ in range(9)
    )
    return first_ten + steuer_id_check_digit(first_ten)


def make_iban(rng: Random, country: str = "DE") -> str:
    """A syntactically valid, mod-97-correct IBAN for a fictional account."""
    length = 18 if country == "DE" else 16
    bban = "".join(str(rng.randint(0, 9)) for _ in range(length))
    return country + iban_check_digits(country, bban) + bban


def make_betriebsnummer(rng: Random) -> str:
    """Eight digits, never leading zero (see BEREICH for the same reason)."""
    return f"{rng.randint(1, 9)}" + "".join(str(rng.randint(0, 9)) for _ in range(7))


AKTENZEICHEN = (
    "S 12 R 4711/26",
    "B 12 R 3/25 R",
    "L 4 R 812/24",
    "S 9 R 1043/26",
    "L 16 R 77/25",
)

EMAIL_LOCALS = ("a.vollbrecht", "i.wegener", "k.dombrowski", "m.thalheim", "poststelle")
EMAIL_DOMAINS = (
    "muster-beispiel.de",
    "beispielstadt-verwaltung.de",
    "nordlicht-beispiel.de",
)

TELEFONE = (
    "0431 4567890",
    "+49 30 12345678",
    "0221 9876543",
    "030 5551234",
    "+49 431 778899",
    "0511 3344556",
)

STRASSEN = (
    "Kirchgasse 2",
    "Amselweg 14",
    "Bergstrasse 7a",
    "Lindenallee 33",
    "Hafenufer 5",
    "Marktplatz 1",
    "Rosensteig 22",
)

ORTE = (
    "24103 Beispielstadt",
    "10117 Musterhausen",
    "50667 Beispieldorf",
    "70173 Musterberg",
)

ORGANISATIONEN = (
    "Nordlicht Systemhaus GmbH",
    "Thalheim Logistik AG",
    "Meyer & Sohn GmbH",
    "Ostsee Pflegedienst gGmbH",
    "Beispieltal Bau GmbH & Co. KG",
    "Musterstadt Kulturverein e. V.",
)

VORNAMEN = ("Ansgar", "Ilse", "Katarzyna", "Mehmet", "Bettina", "Jonas")
NACHNAMEN = ("Vollbrecht", "Wegener", "Dombrowski", "Thalheim", "Kruse", "Baumgart")


# ------------------------------------------------------------------ carriers ---

Chunk = str | tuple[Kind, str]


def compose(item_id: str, scenario: str, chunks: Sequence[Chunk]) -> LabelledText:
    """Build a snippet and its labels from chunks; offsets fall out of the join.

    This is the whole reason the labels are trustworthy: nothing here looks at
    the finished string.
    """
    text = ""
    labels: list[Label] = []
    for chunk in chunks:
        if isinstance(chunk, str):
            text += chunk
            continue
        kind, value = chunk
        labels.append(Label(start=len(text), end=len(text) + len(value), kind=kind))
        text += value
    return LabelledText(
        item_id=item_id, scenario=scenario, text=text, labels=tuple(labels)
    )


VSNR_CARRIERS = (
    ("Die Versicherungsnummer lautet ", "."),
    ("Bitte ordnen Sie den Vorgang der Versicherungsnummer ", " zu."),
    ("Im Antrag ist die Rentenversicherungsnummer ", " eingetragen."),
    ("Versicherungsnummer: ", ""),
    ("Der Sozialversicherungsausweis weist die Nummer ", " aus."),
    ("Zu Ihrer Versicherungsnummer ", " liegt uns bereits ein Konto vor."),
)

STEUER_CARRIERS = (
    ("Die steuerliche Identifikationsnummer lautet ", "."),
    ("Steuer-ID: ", ""),
    ("Bitte geben Sie die Identifikationsnummer ", " im Formular an."),
    ("Fuer den Rentenbezugsmitteilung wird die Steuer-ID ", " benoetigt."),
    ("Die IdNr. ", " ist beim Finanzamt hinterlegt."),
    ("Als steuerliche Identifikationsnummer wurde ", " uebermittelt."),
)

IBAN_CARRIERS = (
    ("Die Zahlung erfolgt auf das Konto ", "."),
    ("Bankverbindung: ", ""),
    ("Bitte ueberweisen Sie den Betrag auf die IBAN ", "."),
    ("Als Kontoverbindung wurde ", " angegeben."),
    ("Die Rente soll auf ", " ausgezahlt werden."),
    ("Auslandskonto: ", ""),
)

BNR_CARRIERS = (
    ("Betriebsnummer ", " des Auftraggebers."),
    ("Der Betrieb ist unter der Betriebsnummer ", " gemeldet."),
    ("Betriebsnummer: ", ""),
    ("Bitte pruefen Sie die Betriebsnummer ", " im Meldeverfahren."),
    ("Als BetrNr. ", " wurde uns die Nummer der Bundesagentur genannt."),
)

AKTZ_CARRIERS = (
    ("Aktenzeichen: ", ""),
    ("Wir beziehen uns auf das Verfahren ", " vor dem Sozialgericht."),
    ("Az. ", " - Widerspruch gegen den Bescheid."),
    ("In der Sache ", " wurde bereits entschieden."),
    ("Bitte nennen Sie bei Rueckfragen das Geschaeftszeichen ", "."),
)

EMAIL_CARRIERS = (
    ("Rueckfragen bitte an ", "."),
    ("E-Mail: ", ""),
    ("Der Antrag ging per Mail von ", " ein."),
    ("Bitte antworten Sie an ", " und nicht telefonisch."),
    ("Kontakt: ", " (Poststelle)"),
    ("Eine Kopie wurde an ", " gesendet."),
)

TEL_CARRIERS = (
    ("Telefonisch erreichbar unter ", "."),
    ("Tel. ", ""),
    ("Rueckruf erbeten unter ", " ab 9 Uhr."),
    ("Die Rufnummer lautet ", "."),
    ("Fuer Rueckfragen: ", ""),
    ("Bitte rufen Sie unter ", " zurueck."),
)

ADDR_CARRIERS = (
    ("Die Anschrift lautet ", "."),
    ("Wohnhaft ", ", seit 2019."),
    ("Anschrift: ", ""),
    ("Post bitte an ", " senden."),
)

GEBDAT_CARRIERS = (
    ("Die antragstellende Person ist geboren am ", "."),
    ("Geburtsdatum: ", ""),
    ("geb. ", ", wohnhaft in Norddeutschland."),
    ("Der Antragsteller wurde geboren am ", " und ist rentennah."),
    ("Als Geburtsdatum wurde ", " angegeben."),
    ("Geburtstag ", " laut Ausweis."),
)

ORG_CARRIERS = (
    ("Auftraggeber ist die ", "."),
    ("Der Auftrag wurde von der ", " erteilt."),
    ("Als Auftraggeber wurde die ", " benannt."),
    ("Der Vertrag besteht mit der ", " seit 2024."),
    ("Rechnungsempfaenger ist die ", "."),
    ("Beschaeftigt bei der ", ", in Teilzeit."),
)

NAME_LABELLED_CARRIERS = (
    ("Der Antrag wurde von Herrn ", " unterschrieben."),
    ("Frau ", " hat den Vorgang telefonisch angekuendigt."),
    ("Die Unterlagen wurden an Herrn ", " zurueckgesandt."),
)

NAME_BARE_CARRIERS = (
    ("", " hat den Antrag am Schalter abgegeben."),
    ("Zustaendig ist der Sachbearbeiter ", " im Referat 312."),
    ("Nach Ruecksprache mit ", " wird der Vorgang weitergeleitet."),
)

#: Every one of these must produce ZERO detections. They are the sentences a
#: gate that fires on bare dates or bare eight-digit numbers would drown in.
NEGATIVES = (
    "Der Antrag ging am 06.08.2026 in der Poststelle ein.",
    "Die Nachzahlung betraegt 12345678 Cent und wird im Folgemonat angewiesen.",
    "Kundennummer 4711 wurde im Fachverfahren vergeben.",
    "Die Rechnungsnummer lautet 20260811 und ist bereits verbucht.",
    "Der Beitragssatz betraegt 18,6 Prozent.",
    # Nine digits, deliberately not eleven. A bare ELEVEN-digit run IS
    # Steuer-ID-shaped and the recall-first REDACT profile is supposed to seal
    # it even when the check digit fails, so such a string is not a negative -
    # it is over-redaction, which this set reports as precision and never gates.
    # The two-profile split is pinned in tests/test_redact_recognizers.py.
    "Die interne Zaehlernummer 987654321 gehoert zum Postbuch.",
    "Die Taetigkeit begann am 15.01.2026 und dauert an.",
    "Der gewuenschte Rentenbeginn ist der 2026-11-01.",
    "Insgesamt 30000000 Versicherte sind von der Anpassung betroffen.",
    "Bitte antworten Sie schriftlich, nicht telefonisch.",
    "Das Formular V0027 liegt in Kopie bei.",
    "Der Bescheid wurde am 03.05.2025 zur Post gegeben.",
)


def build_items(seed: int = DEFAULT_SEED) -> list[LabelledText]:
    """Compose the whole set. A pure function of the seed."""
    rng = Random(seed)
    items: list[LabelledText] = []
    counter = _Counter()

    for index, (before, after) in enumerate(VSNR_CARRIERS):
        # The last one is deliberately mistyped: a wrong Pruefziffer does not
        # make a Versicherungsnummer less identifying, so the REDACT profile
        # must still find it.
        value = make_vsnr(rng)
        scenario = "vsnr"
        if index == len(VSNR_CARRIERS) - 1:
            value = _break_check_digit(value)
            scenario = "vsnr_mistyped"
        items.append(
            compose(counter.next(), scenario, [before, (Kind.VSNR, value), after])
        )

    for before, after in STEUER_CARRIERS:
        items.append(
            compose(
                counter.next(),
                "steuer_id",
                [before, (Kind.STID, make_steuer_id(rng)), after],
            )
        )

    for index, (before, after) in enumerate(IBAN_CARRIERS):
        country = "AT" if index == len(IBAN_CARRIERS) - 1 else "DE"
        items.append(
            compose(
                counter.next(),
                "iban_de" if country == "DE" else "iban_auslandskonto",
                [before, (Kind.IBAN, make_iban(rng, country)), after],
            )
        )

    for before, after in BNR_CARRIERS:
        items.append(
            compose(
                counter.next(),
                "betriebsnummer",
                [before, (Kind.BNR, make_betriebsnummer(rng)), after],
            )
        )

    for carrier, value in zip(AKTZ_CARRIERS, AKTENZEICHEN, strict=True):
        before, after = carrier
        items.append(
            compose(counter.next(), "aktenzeichen", [before, (Kind.AKTZ, value), after])
        )

    for before, after in EMAIL_CARRIERS:
        address = f"{rng.choice(EMAIL_LOCALS)}@{rng.choice(EMAIL_DOMAINS)}"
        items.append(
            compose(counter.next(), "email", [before, (Kind.EMAIL, address), after])
        )

    for carrier, value in zip(TEL_CARRIERS, TELEFONE, strict=True):
        before, after = carrier
        items.append(
            compose(counter.next(), "telefon", [before, (Kind.TEL, value), after])
        )

    for index, (before, after) in enumerate(ADDR_CARRIERS):
        strasse = STRASSEN[index % len(STRASSEN)]
        ort = ORTE[index % len(ORTE)]
        items.append(
            compose(
                counter.next(),
                "anschrift",
                [before, (Kind.ADDR, strasse), ", ", (Kind.ADDR, ort), after],
            )
        )
    for strasse in STRASSEN[len(ADDR_CARRIERS) :]:
        items.append(
            compose(
                counter.next(),
                "strasse_ohne_ort",
                ["Die Zustelladresse ist ", (Kind.ADDR, strasse), " im Erdgeschoss."],
            )
        )

    for index, (before, after) in enumerate(GEBDAT_CARRIERS):
        value = _birthdate(rng, iso=index % 3 == 1, short=index % 3 == 2)
        items.append(
            compose(
                counter.next(), "geburtsdatum", [before, (Kind.GEBDAT, value), after]
            )
        )

    for carrier, value in zip(ORG_CARRIERS, ORGANISATIONEN, strict=True):
        before, after = carrier
        items.append(
            compose(counter.next(), "organisation", [before, (Kind.ORG, value), after])
        )

    for index, (before, after) in enumerate(NAME_LABELLED_CARRIERS):
        value = f"{VORNAMEN[index]} {NACHNAMEN[index]}"
        items.append(
            compose(
                counter.next(), "name_mit_anrede", [before, (Kind.NAME, value), after]
            )
        )
    for index, (before, after) in enumerate(NAME_BARE_CARRIERS):
        value = f"{VORNAMEN[index + 3]} {NACHNAMEN[index + 3]}"
        items.append(
            compose(
                counter.next(), "name_ohne_anrede", [before, (Kind.NAME, value), after]
            )
        )

    items.extend(_combined_items(rng, counter))

    for text in NEGATIVES:
        items.append(compose(counter.next(), "hard_negative", [text]))
    return items


def _combined_items(rng: Random, counter: _Counter) -> list[LabelledText]:
    """Snippets carrying several identifiers, the way a real letter does."""
    return [
        compose(
            counter.next(),
            "kombiniert_antrag",
            [
                "Antrag auf Altersrente, Versicherungsnummer ",
                (Kind.VSNR, make_vsnr(rng)),
                ", geboren am ",
                (Kind.GEBDAT, _birthdate(rng)),
                ", wohnhaft ",
                (Kind.ADDR, "Amselweg 14"),
                ", ",
                (Kind.ADDR, "24103 Beispielstadt"),
                ".",
            ],
        ),
        compose(
            counter.next(),
            "kombiniert_statusfeststellung",
            [
                "Auftraggeber ist die ",
                (Kind.ORG, "Nordlicht Systemhaus GmbH"),
                ", Betriebsnummer ",
                (Kind.BNR, make_betriebsnummer(rng)),
                ", Rueckfragen an ",
                (Kind.EMAIL, "poststelle@nordlicht-beispiel.de"),
                ".",
            ],
        ),
        compose(
            counter.next(),
            "kombiniert_widerspruch",
            [
                "Widerspruch im Verfahren ",
                (Kind.AKTZ, "S 3 R 902/26"),
                ", Rueckruf unter ",
                (Kind.TEL, "0221 4455667"),
                ", Konto ",
                (Kind.IBAN, make_iban(rng)),
                ".",
            ],
        ),
        compose(
            counter.next(),
            "kombiniert_ohne_pii",
            [
                "Der Vorgang ist vollstaendig, die Frist laeuft bis zum 30.09.2026 "
                "und der Betrag von 4500 Euro wurde angewiesen."
            ],
        ),
    ]


class _Counter:
    """Stable item ids in build order."""

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> str:
        self._value += 1
        return f"pii-{self._value:04d}"


def _birthdate(rng: Random, *, iso: bool = False, short: bool = False) -> str:
    day, month, year = rng.randint(1, 28), rng.randint(1, 12), rng.randint(1940, 1975)
    if iso:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if short:
        return f"{day:02d}.{month:02d}.{year % 100:02d}"
    return f"{day:02d}.{month:02d}.{year:04d}"


def _break_check_digit(vsnr: str) -> str:
    """Move the Pruefziffer by one, so the format holds and the checksum does not."""
    return vsnr[:11] + str((int(vsnr[11]) + 1) % 10)


# -------------------------------------------------------------- self-check ---


def self_check(items: Sequence[LabelledText]) -> None:
    """Refuse to write a set that does not measure what it claims.

    Same shape as the triage corpus generator: a build that would ship a wrong
    label produces no file at all.
    """
    problems: list[str] = []
    deterministic = Detector(Profile.REDACT)
    report = measure(items, detector=deterministic)
    for kind in sorted(DETERMINISTIC_GATE_KINDS, key=lambda item: item.value):
        metrics = report.by_kind.get(kind)
        if metrics is None or metrics.label_count == 0:
            problems.append(f"{kind.value}: no labelled example in the set")
        elif metrics.recall < 1.0:
            problems.append(
                f"{kind.value}: deterministic recall {metrics.recall:.3f}, "
                f"{metrics.label_count - metrics.found_count} label(s) not covered"
            )
    for item in items:
        if item.scenario != "hard_negative":
            continue
        hits = deterministic.scan(item.text)
        if hits:
            problems.append(
                f"{item.item_id}: hard negative produced "
                f"{[hit.kind.value for hit in hits]}"
            )
    ids = [item.item_id for item in items]
    if len(ids) != len(set(ids)):
        problems.append("duplicate item ids")
    if problems:
        raise BuildError(
            "self-check failed, no golden set written:\n  " + "\n  ".join(problems)
        )


# ------------------------------------------------------------------- output ---


def render_items(items: Sequence[LabelledText], seed: int) -> str:
    """The items file as text; a pure function of (items, seed)."""
    document = {
        "policy_id": GENERATOR_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "items": [item.to_dict() for item in items],
    }
    return HEADER.format(seed=seed) + yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True, width=100
    )


def render_manifest(items: Sequence[LabelledText], seed: int, digest: str) -> str:
    """Counts and a digest, so a hand edit is visible without a rebuild."""
    by_kind: dict[str, int] = {}
    for item in items:
        for label in item.labels:
            by_kind[label.kind.value] = by_kind.get(label.kind.value, 0) + 1
    by_scenario: dict[str, int] = {}
    for item in items:
        by_scenario[item.scenario] = by_scenario.get(item.scenario, 0) + 1
    document = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "item_count": len(items),
        "label_count": sum(len(item.labels) for item in items),
        "negatives": sum(1 for item in items if item.scenario == "hard_negative"),
        "labels_by_kind": dict(sorted(by_kind.items())),
        "items_by_scenario": dict(sorted(by_scenario.items())),
        "items_sha256": digest,
        "note": (
            "Measures the redactor, not the triage. Deliberately outside "
            "corpus/gold/REGISTRY.yaml: a redaction recall number has nothing "
            "to say about routing or tiers."
        ),
    }
    return (
        "# GENERATED by python -m corpus.pii_golden.build - do not edit by hand.\n"
        + yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    )


def write_set(items: Sequence[LabelledText], out_dir: Path, seed: int) -> list[Path]:
    """Write items and manifest; returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    items_text = render_items(items, seed)
    items_path = out_dir / ITEMS_NAME
    items_path.write_text(items_text, encoding="utf-8", newline="\n")
    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_text(
        render_manifest(items, seed, _sha256(items_text)),
        encoding="utf-8",
        newline="\n",
    )
    return [items_path, manifest_path]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    """Build (or check) the seeded PII golden set."""
    parser = argparse.ArgumentParser(
        prog="corpus.pii_golden.build", description=__doc__
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--check", action="store_true", help="verify without writing anything"
    )
    args = parser.parse_args(argv)

    try:
        items = build_items(args.seed)
        self_check(items)
    except BuildError as error:
        print(str(error), file=sys.stderr)
        return 2

    report = measure(items, detector=Detector(Profile.REDACT))
    print(f"EingangsLotse PII golden set ({GENERATOR_VERSION}, seed {args.seed})")
    print(f"  items              {len(items)}")
    print(f"  labels             {sum(len(item.labels) for item in items)}")
    print(report.summary())

    if args.check:
        expected = render_items(items, args.seed)
        actual_path = args.out / ITEMS_NAME
        if not actual_path.is_file():
            print(f"\n  FAIL: {actual_path} does not exist", file=sys.stderr)
            return 1
        if actual_path.read_text(encoding="utf-8") != expected:
            print(f"\n  FAIL: {actual_path} differs from this build", file=sys.stderr)
            return 1
        print(f"\n  check passed: {args.out.as_posix()} matches this build")
        return 0

    written = write_set(items, args.out, args.seed)
    print("\n  wrote " + ", ".join(path.as_posix() for path in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
