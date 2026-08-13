# ADR-017: Seal at Ingest, Transient Witness for the Deterministic Plane

**Status:** Accepted, 2026-08-11 (part 04, plan step S4)

## Context

ADR-002 decided the shape of the privacy boundary: PII splits into a sealed
vault at ingest, the working copy carries collision-resistant randomized
placeholders, a second detector sweep must find nothing, and re-hydration
happens strictly at outbound template rendering. Part 01 shipped the contract
for it (`Envelope.vault_ref`, `Envelope.redaction_verified`) with two
deliberate hard-codes marked as "the single place that must change": a
`vault_ref` derived from the submission id, and `redaction_verified=True`
asserted rather than computed.

Building the real thing surfaced a tension ADR-002 did not have to answer,
because in ADR-002 the model was the only consumer of the working copy. It is
not. The **deterministic** plane also computes on those values, and it computes
things that only work on the real ones:

* a Versicherungsnummer is checked against its format AND against the birth date
  it encodes in positions 3 to 8,
* a birth date has to be a real calendar date within absolute bounds,
* the gap between a birth date and a Rentenbeginn has to be at least 60 years
  (par. 237a SGB VI), between a birth date and a Taetigkeitsbeginn at least 14
  (par. 5 JArbSchG).

Validating a random placeholder token against any of those returns "no problem
found" for every input, which is the one failure mode a completeness checker
must not have: it would report COMPLETE on garbage and the false-clear gate
would still read 0.000, because the gold set would be wrong in the same
direction.

A third pressure: a leak inventory taken before implementation found that
`engine/evidence/completeness.py` embedded observed values verbatim in its
problem strings (`Wert '{value}' entspricht nicht dem Format ...`). Those
strings become `GapItem.detail`, ride into the EVIDENCE_ASSEMBLED journal
payload and into the Nachforderung `{problem}` substitution. An invalid
Versicherungsnummer therefore landed raw in the audit log - past the boundary,
through the one door nobody was watching.

## Options

1. **Seal after evidence.** Let the deterministic plane run on raw values and
   seal before the model plane. Simple, and it makes the envelope's documented
   invariant a lie: the envelope, the journal and every artifact between ingest
   and sealing would carry identity data.
2. **Do not seal what validators need.** Keeps validation working and leaves
   the Versicherungsnummer and the birth date - the two most identifying fields
   in the corpus - in the working copy forever.
3. **Dereference the vault inside the validators.** Correct results, and it
   breaks ADR-002's render-time-only rule on day one. Once the vault has a
   second caller, "the vault is only read at rendering" becomes a naming
   convention.
4. **Seal at ingest, and hand the run a transient witness.** Ingest already
   holds the raw values for the instant of sealing. It hands the pipeline an
   in-memory `placeholder -> value` mapping that lives for exactly one request.

## Decision

Option 4, with the scope of the witness fenced in code rather than promised.

**1. Seal at ingest, before the Envelope exists.** Identity-classed payload
paths are removed from the structured payload and replaced by placeholders
before `Envelope(...)` is constructed. The contract's documented invariant
("carries ONLY redacted content; nothing un-redacted passes this point")
becomes true instead of vacuously true. Both part-01 hard-codes are gone:
`vault_ref` is 26 random characters that carry no information about the case,
`redaction_verified` is exactly what the verification pass computed.

**2. One policy file answers four questions at once.**
`config/redaction/identity_fields_v1.yaml` declares, per path: the placeholder
kind, whether the seal covers a whole subtree, whether the value participates
in the witness, and whether its observed value may ever be quoted in a problem
text. Splitting those into separate lists is how classification drifts - a field
gets added to the seal list and forgotten in the visibility list, and the value
walks out through an error message. The loader refuses a policy path that a
procedure's `field_map` reads but that carries `witness: false`, because that
combination would validate a token and report "valid".

**3. The witness is transient and unreachable.** It is created by ingest,
consumed by `evaluate_completeness`, and is not on the envelope, not on
`PipelineResult`, not in the journal, not in the API and not in the vault API.
Its type offers exactly one operation - "what does this placeholder stand for,
right now" - and its `repr` prints a count. It is **not** a vault dereference:
nothing in parts 04 to 07 calls `VaultStore.fetch` outside tests, so ADR-002's
render-time-only rule stands unamended.

*Consequence, stated so it cannot be discovered later:* sealed-field validation
is only possible in the ingest-coupled pass. If the pipeline ever becomes a
multi-process staged system, the witness cannot cross the stage boundary and
this decision has to be revisited - the options then are re-reading the vault
under an audited, narrow interface, or moving validation into ingest.

**4. Value visibility is derived from the same policy row.** Problem strings for
identity-classed fields never quote the observed value; bounds, pattern texts
and constraint names stay, because they describe the RULE and not the person.
Cross-field messages hide the sealed operand and may still name the open one
(`min_years_after` on the Rentenbeginn names the Rentenbeginn, never the
Geburtsdatum). Where both operands are sealed - the Versicherungsnummer against
the birth date it encodes - both are hidden, because naming one would
reconstruct the other from the fact that they disagree. Non-identity fields keep
their more useful wording.

**5. Two detector profiles, because the two jobs have opposite error costs.**
REDACT is recall-first and used to decide what to seal: a format hit counts even
when the checksum fails, because a mistyped Versicherungsnummer identifies a
person just as well as a correct one. VERIFY is precision-first and used by the
post-redaction sweep, where a hit seals a leaf and, failing that, refuses a
submission: checksum-validated VSNR, Steuer-ID and IBAN, e-mail addresses, and
anything imitating the reserved placeholder syntax. Deliberately not bare dates
and not bare eight-digit numbers - a Rentenbeginn and a Betrag in Cent are
legitimate payload content, and a gate that fires on them is a gate somebody
switches off within a week, which is worse than the false negatives it would
have caught.

**6. Fail-safe: seal more, never forward unverified.** Residue at a path the
policy does not cover (a Versicherungsnummer typed into a free-text field) is
auto-sealed as a whole leaf, kind TEXT, with a witness entry, and the sweep runs
ONCE more. Still dirty means the submission is refused with a typed error before
any journal event exists, mapped to a sanitized 422. Never a stack trace with
values, never a half-ingested case. Exactly one re-verification round, so "the
sweep could not clean this" is an explicit reportable state rather than a loop.

**7. No schema changes.** `Envelope`, `ContentPart`, `ExtractionRecord` all
carry this design as-is: a sealed field's extraction value is a placeholder
string and witness resolution happens inside the completeness checker.
`VersionStamp` has no field for a redaction policy and schemas are contracts, so
the policy id travels in the RECEIVED journal payload
(`redaction_policy_id: identity_fields_v1`) instead.

## Consequences

* **The frozen gold set is the proof.** Sealing is transparent to the
  deterministic plane exactly when the witness works, so gold v3 coming through
  with routing 1.000, tier 1.000, false_clear 0.000, false_flag 0.000, gaps
  1.000 and derivation 1.000 - byte-identical, no label touched - is a measured
  claim about the boundary and not a formality.
* **The presence invariant is load-bearing.** A path that is absent, null or
  blank is never sealed. Sealing it would replace "no answer" with a placeholder
  and every `op: ne / value: null` predicate and every MISSING gap in the system
  would change meaning. A config lint now refuses any identity-classed field
  compared by value instead of tested for presence, so a future
  `op: eq / value: "Musterfirma GmbH"` is a loud startup error rather than a
  rule that silently stops firing.
* **One rendering rule for scalars.** The witness has to hand a validator
  exactly the string the schema mapper would have produced from the same raw
  value, or a Versicherungsnummer submitted with stray whitespace would suddenly
  fail its format pattern. `engine.redact.scalar_text` is that one definition and
  the mapper imports it.
* **Structured person names have no second line of defence.** The VERIFY sweep
  is checksum-gated and calls no model, so it cannot find a name in a structured
  field. For structured payloads the policy IS the control and the sweep is a
  backstop for checksummed identifiers only. The canary suite found this the
  hard way on `antragsteller.name`; free text gets the full union including NER
  from part 05 on.
* **Error paths are part of the boundary.** The `/ingest` 422 no longer echoes
  the pydantic input, and FastAPI's own request-validation 422 is replaced for
  the same reason: it returns the whole submitted body by default. An error
  message is the easiest place in a web application to leak what the rest of the
  architecture spent a part sealing.
* **Cost.** Every stage below ingest reads placeholders, so any future rule that
  wants a sealed VALUE has to be a deliberate policy change rather than a config
  edit. That is the intended friction.
