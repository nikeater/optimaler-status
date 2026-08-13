# Seeded German-PII golden set (P-7)

Labelled German administrative snippets for measuring the **redactor**, not the
triage. 81 items, 79 labelled spans, 12 hard negatives. Generated, frozen by
rebuild rather than by decree, and deliberately **not** part of
`corpus/gold/REGISTRY.yaml`: a redaction recall number has nothing to say about
routing accuracy or tier decisions, and mixing the two would let a good triage
score hide a bad redaction one.

```powershell
python -m corpus.pii_golden.build            # rebuild items.yaml + MANIFEST.yaml
python -m corpus.pii_golden.build --check    # verify, write nothing (exit 1 on drift)
```

## Why it exists

Canary tests answer "did this particular fake identity survive". They cannot
answer "what share of German identifiers does the detector union find at all",
and the second question is the one that decides whether the boundary holds on
material nobody wrote a fixture for. The prior-art pass filed this as **P-7**
after Microsoft's own Presidio documentation: recall is not guaranteed, the
defaults are English-tuned, and missing custom recognizers are the number-one
reason real PII slips through. So recall gets measured, in CI, on a set built
for the purpose.

## What is in it

| Kind | Labels | What it covers |
|---|---|---|
| VSNR | 7 | Versicherungsnummern with real Pruefziffern, plus one deliberately mistyped |
| STID | 6 | Steuer-IDs passing ISO 7064 MOD 11,10 |
| IBAN | 7 | German IBANs plus one Austrian account, all mod-97 correct |
| BNR | 6 | Betriebsnummern behind an explicit label |
| AKTZ | 6 | Sozialgerichts- and Behoerden-Aktenzeichen, labelled and unlabelled |
| EMAIL | 7 | Addresses in sentence and in header position |
| TEL | 7 | +49 and 0-prefixed numbers with several groupings |
| ADDR | 13 | Strassenname + Hausnummer, PLZ + Ort, and both together |
| GEBDAT | 7 | Birth dates behind "geboren am", "Geburtsdatum", "geb." |
| ORG | 7 | Organisations by legal form, including `gGmbH` and `GmbH & Co. KG` |
| NAME | 6 | Three behind a salutation, three bare in prose |

Plus 12 **hard negatives** that must produce zero detections: procedural dates,
eight-digit amounts, Kundennummern, Beitragssaetze, form numbers. They are the
sentences a detector that fired on bare dates or bare eight-digit numbers would
drown in - and a gate that shouts about a Rentenbeginn is a gate somebody
switches off.

Everything is invented. The values are synthetic by construction; none of them
came from a document.

## Why the labels can be trusted

The builder never searches its own output. Every snippet is composed from a list
of chunks, some of which are `(kind, value)` pairs, and the character offsets
fall out of the concatenation. A builder that produced text and then ran a
regular expression over it to find its own labels would be measuring the regular
expression against itself.

The build then refuses to write anything unless the deterministic union covers
every label of every gated kind and the hard negatives stay silent - the same
"pipeline of refusals" discipline as the triage corpus generator.

## How it is scored

* **Recall by containment.** A label counts as found only when a detection of
  the same kind covers it entirely. Catching nine digits of an eleven-digit
  Steuer-ID would pass an overlap rule while leaving two digits and the shape of
  the number in the working copy, which is not redaction.
* **Precision is reported, never gated.** Over-redaction costs utility;
  under-redaction costs a person's data. A precision gate would create pressure
  in exactly the wrong direction, so false positives are inventoried in the eval
  report and left for a human to judge.
* **The gate splits by what produced the number.** The deterministic kinds must
  sit at recall 1.000 with or without the optional `[redact]` extra. `NAME` is
  gated only when the extra is installed: a bare German person name in prose has
  no grammar to match, which is precisely why P-7 asks for a union rather than
  for more regular expressions.

One deliberate over-redaction is worth knowing about: a bare eleven-digit number
is Steuer-ID-shaped, and the recall-first REDACT profile seals it even when the
check digit fails. That is why no hard negative contains one. The precision
-first VERIFY profile does gate on the checksum, and
`tests/test_redact_recognizers.py` pins both halves.

## Files

| File | What it is |
|---|---|
| `items.yaml` | the set: item id, scenario, text, labelled spans |
| `MANIFEST.yaml` | counts per kind and per scenario, plus a SHA-256 of `items.yaml` so a hand edit is visible without a rebuild |
| `build.py` | the seeded builder and its self-check |
