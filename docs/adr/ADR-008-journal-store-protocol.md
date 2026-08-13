# ADR-008: Journal Store Protocol with Sequence-Based Append-Only Enforcement

**Status:** Accepted, 2026-08-10 (part 01, plan step S1)

## Context
The case journal is the system's only source of truth: audit log, AI-Act
logging, Art. 22 human-involvement proof, notification trigger and correction
training pool are all projections of it (ADR-005). PostgreSQL is the target
store, but it arrives with the compose profile in a later part, and S1 must not
take a Docker dependency to run its walking skeleton.

The risk in postponing storage is that "append-only" degrades into a naming
convention that the first `UPDATE` breaks.

## Options
1. Write directly against a concrete store now and abstract later. Couples every
   stage to a storage decision that has not been made.
2. A repository class with inheritance. Forces the eventual PostgreSQL store to
   fit a base class designed before any of its constraints were known.
3. A structural `Protocol` (`append`, `read`, `next_sequence`, `case_ids`) with
   two S1 implementations, and append-only enforced inside the stores.

## Decision
Option 3, with the invariants enforced rather than promised:

* `JournalStore` is a `typing.Protocol`; every pipeline stage takes it as a
  parameter and no stage knows which backend it is writing to.
* Events carry a monotonic per-case `sequence` starting at 0. An append whose
  sequence is not exactly the next one raises `SequenceConflictError`; a repeated
  `event_id` raises `DuplicateEventError`. That is optimistic concurrency
  control, and it is what makes "the journal is the truth" defensible under
  concurrent writers.
* Nothing is ever updated or deleted. Correcting an item means appending a new
  event (for example `OVERRIDDEN`).
* S1 ships `InMemoryJournalStore` (tests, eval, dev) and `JsonlJournalStore`
  (one append-only file per case, so a crash can truncate at most the last line).
  `JsonlJournalStore` refuses case ids that are not filesystem-safe.
* Derived state is a fold over events (`engine/journal/projection.py`) and is
  deliberately defensive: a malformed payload degrades the projection, it never
  raises. A rendering bug must not be able to take down the audit trail.

## Consequences
- The PostgreSQL store lands as one new class plus a conformance test run, with
  no changes to any pipeline stage.
- The same test suite runs against every backend, so backends cannot diverge in
  behaviour.
- Cost: `JsonlJournalStore` re-reads a case file to compute the next sequence,
  which is fine for S1 volumes and would not be for production. It is a dev and
  test backend and is documented as one.
