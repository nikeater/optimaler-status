# Documentation index

| Document | What it is |
| --- | --- |
| [`technical-spec.md`](technical-spec.md) | **Start here.** The consolidated specification: the whole system as built, every measured number, and the caveats that go with them |
| [`BUILD.md`](BUILD.md) | Build, run, test, seed, containerise, deploy. The specification the CI workflow implements |
| [`adr/`](adr/) | 36 architecture decision records. ADR-001 (two planes), ADR-004 (the one-way valve) and ADR-017 (seal at ingest) are the load-bearing three |
| [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md) | The ADR index, one line each |
| [`technical-design.md`](technical-design.md) | The design as it stood before the build, kept for the diff against what was actually built |
| [`notifications.md`](notifications.md) | The notification catalogue: which message a case owes, when, and why each one is a Realakt |
| [`research/`](research/) | The three research passes the design rests on: prior art, the legal implementability map, and Statusfeststellung under par. 7a SGB IV |
| [`transparency-record.md`](transparency-record.md) | **What the shipped release actually is.** Every configuration version, every threshold and where its number came from, the gates that hold, and what the system does not do. ATRS-style, one record per configuration version |
| [`KNOWN-ERRORS.md`](KNOWN-ERRORS.md) | The failure modes that are known and not fixed, with what each one costs. This is the LIVING list |
| [`known-errors/`](known-errors/) | One frozen snapshot of the above per released version. `v0.1.0.md` is what was known to be broken on the day v0.1.0 shipped, and is never edited afterwards |
| [`vault-dpia-input.md`](vault-dpia-input.md) | Input for a data-protection impact assessment: what the vault holds, what production storage has to be, retention and erasure |
| [`ai-act-scoping-memo.md`](ai-act-scoping-memo.md) | Where this system sits under the EU AI Act, and what would move it |
| [`accessibility-selfcheck.md`](accessibility-selfcheck.md) | EN 301 549 / WCAG 2.1 AA SELF-assessment of every page, with the measured contrast ratios of the design system. Not an audit |
| [`PUBLISHING.md`](PUBLISHING.md) | How to publish this repository and host the demonstration instance |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to work on this, and the rules that are not negotiable |

## A note on where these files are authored

`adr/` is a directory junction into the workspace that produced this
repository, so the ADRs here are the ADRs there - one copy, no drift.

Everything else here is a **copy**, byte-identical to its workspace original at
the time of the copy - taken at part 11, except `transparency-record.md` and
`known-errors/`, which are release records and were taken at part 25. Two files
are authored here and have no workspace original at all: this index and
`PUBLISHING.md`, both of which are about the published repository rather than
about the system. The one deliberate divergence is `technical-spec.md`, which
carries an added note at the top saying which of its cross-references do not
resolve in this repository.

The workspace also holds records that are deliberately NOT published here - the
engineering log, the compliance backlog, the implementation plan and the
per-part task briefs - because they are a build diary rather than documentation
of the system.

If you are working in that workspace: the copies are what a reader of the
public repository sees, and nothing keeps them in step automatically. Re-copy
after editing an original, or the two will disagree and the published one will
be the one everybody reads.
