# ADR-018: Vault Store Protocol, and What the Dev Backend Does Not Claim

**Status:** Accepted, 2026-08-11 (part 04, plan step S4)

## Context

ADR-002 says PII splits into "an encrypted PostgreSQL vault at ingest". That
store arrives with the compose profile in a later part, and S4 must not take a
Docker dependency to run the privacy boundary. ADR-008 already solved the same
shape for the journal: a structural `Protocol`, two in-repo backends, and the
invariants enforced inside the stores rather than promised in prose.

Two risks in doing the vault differently. First, coupling: if the seal path
writes directly against one concrete store, swapping it later touches ingest.
Second, and worse, the temptation to make the dev backend look like the
production one - a file store with a home-rolled cipher and a key in the repo
would satisfy a checklist and protect nothing, and would be reported as
"encrypted at rest" by whoever reads the code next.

## Options

1. Write against a concrete file store now and abstract later.
2. A base class the PostgreSQL store will inherit from.
3. A structural `Protocol` with two S4 implementations, mirroring ADR-008
   exactly, and a dev backend that documents its limits in its own output.

## Decision

Option 3.

* `VaultStore` is a `typing.Protocol` with `seal`, `fetch`, `exists`. Ingest
  takes it as a parameter and does not know which backend it writes to; the API
  chooses one from `EINGANGSLOTSE_VAULT_DIR`, mirroring the journal's
  `EINGANGSLOTSE_JOURNAL_DIR` so an operator learns one convention, not two.
* **Append-only.** A `vault_ref` may be sealed exactly once; a second seal is
  `DuplicateVaultRecordError`. Correcting a record means a new one. The same
  discipline as the journal, for the same reason: "the sealed record is what
  arrived" is only defensible if nothing can rewrite it.
* **`fetch` is documented as render-time only, in one place.** ADR-002 puts
  re-hydration strictly at outbound template rendering, round-trip checked, with
  an unknown placeholder as a hard error that blocks output. Nothing in parts 04
  to 07 calls `fetch` outside tests; the deterministic plane gets what it needs
  from the transient witness (ADR-017), which never touches this module.
* **`vault_ref` is 26 random characters from the placeholder alphabet**, prefixed
  `vault-`. Not derived from the submission id, the case id or a counter, so the
  handle leaks nothing about the case and two ingests of the same submission get
  different handles.
* **The file backend says what it is.** `JsonlVaultStore` writes plaintext JSON,
  one file per `vault_ref`, refuses filesystem-unsafe references, and writes a
  notice INTO every file it produces: dev backend, plaintext, synthetic data
  only, encryption at rest is a property of the production PostgreSQL vault. No
  home-rolled crypto. A file cipher here would look like protection without
  being any, which is the worse of the two failure modes.
* **A sealed entry stores the value AS RECEIVED**, JSON-encoded, so a number
  stays a number and the address subtree stays an object. Normalizing for
  rendering is part 08's job; the vault stores the truth rather than a cleaned-up
  version of it.
* `VaultRecord.summary()` is the value-free projection - reference, entry count,
  counts per kind - and it is what the journal and any log line get.

## Consequences

* The PostgreSQL vault lands as one new class plus a conformance test run, with
  no change to ingest, the pipeline or the API. The backend fixture in
  `tests/test_redact_vault.py` is parametrised over backends already, so a new
  one cannot diverge in behaviour without failing.
* Encryption at rest, key management, retention and deletion are **deployment**
  properties and are written down as such in `docs/vault-dpia-input.md` rather
  than half-implemented here.
* Cost: the JSONL backend re-reads nothing but also indexes nothing, and a
  vault_ref lookup is a filesystem stat. Fine for S4 volumes and not for
  production, exactly like `JsonlJournalStore`.
* One open thread for part 08: the vault stores raw values including whitespace
  as submitted (`" 17170459B012 "`). The re-hydrator has to normalize before it
  puts a value into a Nachforderung, and the round-trip check has to compare
  against the raw form, not the normalized one.
