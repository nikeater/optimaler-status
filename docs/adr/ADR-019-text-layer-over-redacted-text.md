# ADR-019: The Text Layer Is Built Over Redacted Text

**Status:** Accepted, 2026-08-12 (part 05, plan step S5)

## Context

Part 04 moved the privacy boundary in front of the envelope: identity-classed
payload paths are sealed into a vault before an `Envelope` object exists
(ADR-017). Everything it sealed was a payload PATH. A letter has no paths.

Part 05 adds the other half of the inbox - a free-text Anschreiben by e-mail, a
scanned letter - and with it three questions that the structured path never had
to answer:

1. **In which coordinates does a span live?** The dormant `TextLayer` contract
   from part 01 speaks of an offset map from normalized text back to "the
   original". There are now two candidates for "the original": the letter as it
   arrived, and the letter as the working copy carries it.
2. **Which detector union decides what to seal in prose?** The optional NER
   member finds bare person names, which no regular expression carries. The
   gate must not depend on an optional wheel.
3. **What is the correct extraction of a value that was sealed?** The letter
   now literally says `[[PII|VSNR|...]]`.

## Decision

### 1. Seal first, then normalize. "Original" means the redacted original.

The boundary seals every identity span it finds in a free-text part, span by
span, BEFORE the `Envelope` is constructed - the same place and the same moment
it seals a payload leaf. The text layer is built over `ContentPart.redacted_text`
and nothing else.

Consequently every offset in `OffsetSegment`, every `Span` and every quote in an
extraction proposal lives in **redacted coordinates**. The raw letter exists
behind `raw_refs` and in the vault, and it never re-enters the model path - not
even to translate a span for display. A caseworker who needs the original opens
the original.

The alternative - normalize, then seal - would put a raw letter through a
transformation nobody audited and leave every span pointing at text that still
had a name in it. Sealing spans rather than whole parts is what keeps the
working copy a working copy: a letter with its verbs removed cannot be triaged.

### 2. The gate seals with the deterministic union; production may add the model.

`text_seal_detector(with_ner=False)` is the default and every gate path leaves it
there. `api/app.py` builds the union WITH the model member when the `[redact]`
extra is installed (`EINGANGSLOTSE_TEXT_NER=0` turns it off), because a real
letter carries bare person names in the middle of a sentence and no regular
expression finds them.

That asymmetry is only acceptable because the corpus generator **asserts at build
time** that deterministic sealing leaves every gold letter verification-clean.
The gate is therefore not the weaker path by construction rather than by hope: a
letter item whose sealing depended on the optional model could not be written.

### 3. A placeholder in prose is a correct extraction.

An extractor that proposes `[[PII|VSNR|...]]` as the value of
`versicherungsnummer` is quoting the letter correctly, and its record is correct.
The real value is validated through the transient witness in the completeness
checker (ADR-017), which resolves a value that is exactly one placeholder before
running the Versicherungsnummer's format and cross-field checks. Nothing is ever
unsealed to make a span match.

### 4. The sweep ignores hits ON a placeholder.

The post-seal sweep over prose runs the recall-first union over the text with
every well-formed placeholder masked to a same-length run of spaces, plus the
precision-first union over the unmasked text (which is what finds a FORGED
token). A recognizer that fires on a placeholder has found the redaction, not
residue.

This was not a theoretical refinement. The canary suite caught the model member
tagging a random twelve-character token as a PERSON and as an ORGANIZATION. No
re-sealing round can clean a hit that IS the redaction, so the boundary refused
its own output - at a rate set by which characters the token source happened to
draw.

## Consequences

- `ContentPart.redacted_text` is non-None for the first time in the project, and
  the envelope's documented invariant ("carries only redacted content") now
  covers prose as well as leaves.
- The vault entry for a sealed span carries `part_id` and `span` and NO `path`.
  Part 08's re-hydrator must handle both shapes; inventing a payload path for
  prose would tell it to look somewhere that does not exist.
- Two mentions of the same value in one letter get two DIFFERENT tokens. Token
  equality must not become a channel that says "these two spans are the same
  person" to anything downstream - part 06's scorer above all.
- **A limit that no threshold fixes:** OCR-mangled identity evades a
  pattern-based detector entirely. A Versicherungsnummer read with an `O` for a
  `0` is still a Versicherungsnummer to a human and no longer one to a regular
  expression, so it is not sealed, the sweep does not flag it, and it reaches the
  working copy. Recorded in `docs/KNOWN-ERRORS.md`, asserted by a canary test,
  and answerable only upstream - at the scanner, with OCR confidence (part 07).
- No schema change was needed. `TextLayer`, `TextLayerPart` and `OffsetSegment`
  have carried this shape since part 01.

## Alternatives considered

**Normalize the raw letter, then seal the normalized text.** Rejected: it puts
the raw document through an unaudited transformation and produces an offset map
of the un-redacted text, which is a map that must never exist.

**Keep both coordinate systems and translate on demand.** Rejected: the
translation would need the raw text at translation time, which re-opens the one
door ADR-002 closed. Display of an original is a vault operation at render time,
not a span translation.

**Seal a free-text part as one whole placeholder.** Rejected: it is what the
auto-seal fallback does for a structured leaf, where the leaf is a value. A
letter is not a value.
