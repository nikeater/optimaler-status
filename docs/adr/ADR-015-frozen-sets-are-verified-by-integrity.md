# ADR-015: A Superseded Gold Set Is Verified by Its Bytes, Not by Re-Running Today's Engine

**Status:** Accepted, 2026-08-11 (part 03, plan step S3)

## Context
ADR-010 froze gold sets and made supersession the only way to correct one. Part
03 exercised that for the first time: the engine changed (procedure derivation,
routing arbitration, structural validation), so v1's labels needed correcting
and v1 was superseded by v2.

That exposed a hole in the tooling. `python -m corpus.generator.build --check`
rebuilds a set from the current specs and runs the current engine over every
item, refusing on any disagreement. For the *current* set that is exactly right.
For a *superseded* set it is guaranteed to fail, because the set was superseded
precisely because today's engine disagrees with it. The verification gate asks
for a check on "every frozen set the tree contains", and the only check that
stays meaningful for a superseded set is a different question.

There was a second problem. The natural place to record "v1 was superseded by
v2" is v1's own `MANIFEST.yaml` - which is inside the frozen directory that
ADR-010 says is never edited. Writing supersession into the artifact would make
the freeze policy self-contradicting on its first application.

## Options
1. **Rebuild every set on every check.** Impossible: a superseded set fails by
   definition.
2. **Stop checking superseded sets.** Then a hand-edited or corrupted gold file
   is undetectable, and the historical numbers quoted against it become
   unfalsifiable.
3. **Two verification modes, chosen by a registry that lives outside the frozen
   directories.**

## Decision
Option 3.

* `corpus/gold/REGISTRY.yaml` lists every set with a `status` (`current` /
  `superseded`), its successor, and a `verification` mode. It is outside every
  frozen directory, so recording a successor never touches a frozen artifact.
* `--check` on a **current** set rebuilds and compares byte for byte, including
  the self-check against the live engine (unchanged behaviour).
* `--check` on a **superseded** set runs `verify_integrity`: recompute the
  SHA-256 of every item, compare against the set's own MANIFEST, and report any
  missing, altered or unlisted file. It never loads the config or runs the
  pipeline.
* The two questions are named in the output, so nobody reads an integrity pass
  as a behavioural pass: "a frozen set is verified by its bytes, not by
  re-running today's engine over it".

## Consequences
- The gate stays runnable over the whole tree as sets accumulate, and it keeps
  meaning something for old sets: v1 is still provably the v1 the numbers came
  from.
- Re-running the *engine* over an old set remains available and is what
  `python -m eval.run --gold corpus/gold/v1` does. Those numbers are expected to
  move, and reading them next to the v2 numbers is how a reader sees what a
  change did. Part 03's log records both.
- A set can be superseded without touching a single byte of it, which is what
  ADR-010 always implied and did not have a mechanism for.
- The registry is hand-maintained, so it can drift from the directory listing.
  A test asserts every directory under `corpus/gold/` is registered and that
  exactly one set is current; drift fails the suite rather than silently
  skipping a set.
- `corpus/gold/s1` is registered with `verification: none`: it is part-01
  scaffolding with no manifest, superseded twice over, and kept only because its
  sidecars prove the label loader still reads pre-part-02 files.
