# DPIA input: the identity vault and the redaction boundary

Input material for the Datenschutz-Folgenabschaetzung (Art. 35 DSGVO), covering
what part 04 built. This is **input**, not the DPIA: the assessment itself is
made by the controller with its DSB, and several sections below can only be
completed by the deploying agency (legal basis, retention, roles). Compliance
backlog row **C-5**; the Art. 13/14 notice text belongs to part 07 and the
measured Art. 22 override rates to part 10.

Status of the system it describes: development, synthetic data only, no pilot,
no real personal data has ever been processed.

## 1. Purpose and legal-basis pointers

| | |
|---|---|
| Processing operation | Automated triage of inbound items to a public pension carrier: routing to the responsible unit, completeness check against a published requirement list, and a tier decision that determines how much human review an item gets. |
| Purpose of the vault specifically | To make it possible to run that triage - including any later LLM-assisted step - on content from which direct identifiers have been removed, while keeping the identifiers available for the outbound letter the caseworker sends. |
| Categories of data subjects | Applicants (Versicherte), their employers or contracting parties (Auftraggeber), and named contact persons in submitted correspondence. |
| Legal basis to be completed by the controller | Art. 6(1)(e) with par. 3 DSGVO in conjunction with the SGB provisions the procedure runs under (par. 35 SGB VI Altersrente, par. 43 SGB VI Erwerbsminderungsrente, par. 7a SGB IV Statusfeststellung), and par. 67a-67c SGB X for Sozialdaten. The vault performs no separate processing purpose; it is a technical measure inside the same operation. |
| Art. 35(3)(a) trigger | The system evaluates personal aspects and produces a decision about how a case is handled, which is why a DPIA is assumed to be required rather than argued away. |
| Art. 9 special categories | Health data is unavoidably present in the Erwerbsminderungsrente procedure (Befundberichte, gutachten_status). Part 04 does not classify or seal attachment content; it must be in scope of the DPIA and of part 05's text layer. |

## 2. This is PSEUDONYMIZATION, not anonymization

Stated plainly, because the distinction is the one most often blurred in
system descriptions:

* The working copy carries randomized placeholders, and the values they stand
  for are held in the vault under a reference the same system holds. Re-
  identification is therefore possible **by design** - part 08 does exactly that
  to write the outbound letter.
* That makes the working copy pseudonymous data under Art. 4(5) DSGVO. **The
  GDPR applies to it in full.** Nothing in this document may be read as a claim
  that redacted content is outside the scope of data protection law.
* Pseudonymization is claimed as what it is: a technical and organisational
  measure under Art. 32(1)(a) and a data-minimisation measure under Art. 25(1)
  toward the model plane and the log plane, which never see the values.

## 3. What is sealed

The policy is one file, `config/redaction/identity_fields_v1.yaml`, and it is
the single source of truth for what leaves the raw plane (ADR-017).

| Payload path | Kind | Sealing | Witness | Value ever quoted |
|---|---|---|---|---|
| `antragsteller.name` | NAME | whole value | yes | never |
| `antragsteller.versicherungsnummer` | VSNR | whole value | yes | never |
| `antragsteller.geburtsdatum` | GEBDAT | whole value | yes | never |
| `antragsteller.anschrift` | ADDR | whole subtree, one entry | no | never |
| `auftraggeber.firmenname` | ORG | whole value | yes | never |
| `auftraggeber.anschrift` | ADDR | whole subtree, one entry | no | never |
| `auftraggeber.betriebsnummer` | BNR | whole value | yes | never |

Plus, at ingest time and without being in the policy: **any leaf where the
post-redaction sweep finds a checksum-validated Versicherungsnummer, Steuer-ID
or IBAN, an e-mail address, or text imitating the reserved placeholder syntax**
is auto-sealed in full as kind TEXT. That is how a Versicherungsnummer typed
into a free-text field is caught.

Not sealed, and deliberately so: procedural content the deterministic rules read
(Rentenart, Rentenbeginn, Antragsart, Taetigkeitsbeginn, Betraege, Eingangsdatum).
Sealing those would empty the payload of everything the triage is about, and they
are not identifiers.

## 4. Data flow

```
   inbound submission (raw, contains personal data)
        |
        |  [ingest, single process, single request]
        v
   +----------------------------------------------------------+
   |  SEAL, per policy                                         |
   |     raw value ---> VaultRecord entry (durable)            |
   |     path      ---> [[PII|KIND|TOKEN]] in the working copy |
   |     scalar    ---> witness entry (in memory, this request)|
   +----------------------------------------------------------+
        |                 |                        |
        v                 v                        v
   working copy      VaultRecord               witness
   (placeholders)    (durable store)      (transient, request-scoped)
        |                 |                        |
        |                 |                        +--> completeness
        |                 |                             validation only;
        |                 |                             never serialized,
        |                 |                             never journaled,
        |                 |                             dies with the request
        |                 |
        |                 +--> read ONLY at outbound template rendering
        |                      (part 08), round-trip checked, unknown
        |                      placeholder = hard error, blocks output
        v
   VERIFY sweep (precision-first)
        |  clean --------> envelope.redaction_verified = true
        |  residue ------> auto-seal that leaf, sweep ONCE more
        |  still dirty --> REFUSE the submission, before any journal
        |                  event exists; sanitized HTTP 422
        v
   derive -> extract -> evidence -> decide -> journal
   (every one of these sees placeholders only)
```

The **witness** deserves its own sentence in an assessment, because it is the
one place where raw values and the deterministic plane meet. It is an in-memory
mapping from placeholder to scalar value, created by ingest and consumed by the
completeness checker inside the same request. It is not on the envelope, not on
the pipeline result, not in the journal, not in any API response and not in the
vault interface; its type exposes one lookup operation and its `repr` prints a
count. Its lifetime is the request. It exists because a Versicherungsnummer has
to be checked against the birth date it encodes, and validating a random token
would report "valid" for every input (ADR-017).

## 5. Storage and access

| Component | Development (today) | Production (target) |
|---|---|---|
| Working copy, journal | in-memory or JSONL files | PostgreSQL |
| Identity vault | `InMemoryVaultStore`, or `JsonlVaultStore` under `EINGANGSLOTSE_VAULT_DIR` - **plaintext JSON, synthetic data only**, and every file it writes says so | PostgreSQL, **encrypted at rest**, separate schema and separate credentials from the case journal, so a read on the journal is not a read on identities |
| Key management | none, because there is nothing to protect | to be specified by the deploying agency: keys outside the database, rotation policy, no key in the application repository |
| Access path to values | `VaultStore.fetch` | same interface; the only production caller is the outbound template renderer of part 08 (`engine/draft/rehydrate.py`) |
| **Prepared drafts (part 08)** | `InMemoryDraftStore`, or `JsonlDraftStore` under `EINGANGSLOTSE_DRAFTS_DIR` - **plaintext JSON, synthetic data only**, the same deliberate limitation the file vault documents for itself | PostgreSQL with **the vault's protections, not the journal's**: encrypted at rest, own credentials, and the retention period of identity data rather than of case history |

**A second store now holds personal data, and it holds it in readable form.** A
prepared Nachforderung or Bewilligungsentwurf is a letter to a named person: it
carries the re-hydrated Anschrift, Versicherungsnummer and Geburtsdatum, because
a letter without them cannot be posted (ADR-023). Consequences for the
controller, stated rather than left to be discovered:

* every retention, erasure and key-management answer that applies to the vault
  applies to the draft store, and a `purge` for one without the other leaves the
  data in the other;
* `GET /drafts/{case_id}` is the one endpoint in this system that returns
  identity data. It is open in the demo because every case in this repository is
  synthetic; a deployment must put it behind the role model of part 10 before a
  single real submission enters;
* the DRAFTED journal event carries ids, counts and a body LENGTH and never the
  text, so the audit trail does not become a second copy of the letters.

**No home-rolled cryptography anywhere.** The dev file backend is deliberately
plaintext rather than encrypted-with-a-key-in-the-repo, because the second would
look like protection and provide none.

Access to identity values is code-level, not user-level: no endpoint returns a
vault value, and none is planned. `GET /cases/{id}` returns the journal and the
derived state, both of which contain placeholders only.

## 6. Retention and deletion

To be set by the controller; what the architecture supports today:

* The journal is append-only by design (ADR-008) and is the AI-Act logging and
  Art. 22 evidence base; Art. 26(6) AI Act suggests at least six months, German
  archiving law and SGB X record-keeping will normally require longer.
* The vault is separable from the journal by construction, so an identity-data
  retention period **shorter** than the journal retention period is
  implementable: deleting a `VaultRecord` leaves the case history intact and
  makes re-hydration fail loudly rather than silently, which is the correct
  failure direction.
* Deletion of a vault record is not yet implemented. `VaultStore` has `seal`,
  `fetch` and `exists` only. Adding `purge(vault_ref)` is the natural shape and
  is a deployment-part item, not a design question.
* Art. 17 erasure requests therefore need an operational answer per deployment;
  the technical hook exists.

## 7. Technical and organisational measures (Art. 32) this part implements

| Measure | Implementation | Evidence |
|---|---|---|
| Pseudonymisation (Art. 32(1)(a)) | seal at ingest before the envelope exists; the boundary is upstream of every consumer | `engine/redact/boundary.py`, `engine/ingest/envelope.py` |
| Data minimisation toward the model plane (Art. 25(1)) | identity values never enter the content a model would receive | canary suite, `tests/test_redact_canaries.py` |
| Verification rather than assertion | `redaction_verified` is computed by a second detector sweep; it was a hard-coded `True` until this part | `engine/redact/verify.py` |
| Fail-safe processing | residue that survives one auto-seal round refuses the submission before any journal event; errors seal MORE, never less | `RedactionRefusedError`, sanitized 422 |
| Leak testing as a permanent gate | seeded fake identities swept out of envelope, journal (both backends, including files on disk), every API response including two kinds of 422, the eval report, the metrics page, captured logs and exception strings | `tests/test_redact_canaries.py`, run on every commit |
| Detector effectiveness measurement | per-kind recall and precision on a seeded German-PII golden set, in the eval report and gated in CI (backlog P-7) | `corpus/pii_golden/`, `engine/redact/recall.py` |
| No value in error paths | pydantic errors reduced to location and type; FastAPI's own 422, which echoes the request body by default, replaced | `api/app.py` |
| Separation of duties in storage | vault behind its own protocol and its own env var, separate store from the journal | ADR-018 |
| Unlinkability of the handle | `vault_ref` is 26 random characters, not derived from the submission | `engine/redact/placeholders.py` |

## 8. Residual risks, stated honestly

1. **Pseudonymous, not anonymous.** The controller holds both the working copy
   and the vault. Anyone with database access to both can re-identify every
   case. Mitigation is organisational (separate credentials, separate schema,
   access logging), not architectural.
2. **The dev file backend is plaintext.** It is for synthetic data in
   development. If anyone points `EINGANGSLOTSE_VAULT_DIR` at a machine holding
   real submissions, there is no protection at all. The production deployment
   must use the PostgreSQL vault.
3. **The witness holds raw values in process memory for the duration of a
   request.** A core dump, a debugger, or a crash reporter that captures locals
   could expose them. Its type refuses to print its contents and it is never
   serialized, which addresses the accidental paths, not a deliberate one.
4. **Structured person names have no second line of defence.** The verification
   sweep is checksum-gated and calls no model, so it cannot recognise a name
   sitting in a structured field. For structured payloads the policy IS the
   control. A field an agency adds without adding a policy row is a leak, and
   the only thing standing between that and the journal is review. The canary
   suite found exactly this gap on `antragsteller.name` during development.
5. **Attachments are not processed at all yet.** Scans and PDFs are referenced
   (`RawRef`) and never opened. When part 05 opens them, everything in this
   document has to be re-examined for the text layer, including the Art. 9 health
   data in Befundberichte.
6. **Detector recall is measured on a set we wrote.** 1.000 on ten deterministic
   kinds and on NAME with the optional NER extra installed - on 81 snippets
   built by the same project that built the recognizers. That is a floor, not a
   guarantee, and real intake will contain shapes nobody anticipated. The metric
   exists so the number has a history and a regression is visible; it is not
   evidence about production recall.
7. **The Betriebsnummer and Aktenzeichen recognizers are context-bound.** A bare
   eight-digit number is not treated as a Betriebsnummer, because it is a Betrag
   far more often. An unlabelled Betriebsnummer in free text is therefore a known
   miss.
8. **Over-redaction is accepted where it trades against privacy.** The
   recall-first profile seals an eleven-digit number that fails the Steuer-ID
   check digit, and the NER member produces false positives (measured precision
   0.62 for ADDR, 0.60 for NAME with the extra installed). Utility cost,
   deliberately chosen; precision is reported and never gated.
9. **A caseworker sees identities, and must.** Nothing here restricts the human
   view; the boundary is between the raw plane and the model, log and metrics
   planes. Role-based restriction of the caseworker UI is part 10 and a
   Berechtigungskonzept question.

## 9. Open items for the controller

* Legal basis and Verarbeitungsverzeichnis entry (Art. 30) for the deployment.
* Retention periods for the journal and for the vault, separately, plus the
  `purge` operation that implements the shorter one.
* Key management for the encrypted PostgreSQL vault.
* Auftragsverarbeitung (Art. 28) where hosting or LLM inference is not in-house;
  note that model calls in this architecture receive pseudonymised content only,
  which changes the assessment but does not remove the requirement.
* Berechtigungskonzept for the caseworker UI (part 10) and for direct database
  access.
* Art. 13/14 notice text inside the receipt confirmation (part 07, C-5).
* Measured Art. 22 human-involvement metrics: confirm-without-edit rate and
  time-to-confirm (part 10, C-5 and P-6).
