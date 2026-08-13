# ADR-009: One Editable Home per Config Value

**Status:** Accepted, 2026-08-10 (part 01, plan step S1)

## Context
`AgencyRiskConfig` (contract) contains `procedures: list[ProcedureFlags]`, and
`ProcedureFlags.tier1_enabled` is the legal gate that decides whether tier 1 may
be produced for a procedure at all. The same flag belongs, editorially, next to
that procedure's requirement list in `config/procedures/<id>_v0.yaml`, where the
Fachbereich works.

If the flag can be written in both `config/thresholds.yaml` and the procedure
file, the two will eventually disagree, and the failure mode is an item cleared
to tier 1 for a procedure whose legal basis was revoked in the file nobody read.

## Options
1. Flags in `thresholds.yaml` only. Correct against the contract, but separates
   the legal gate from the procedure it gates.
2. Flags in both files, with a consistency check. Two homes, an inevitable
   merge conflict, and a check that has to be right forever.
3. Flags in the procedure file only; the loader assembles them into
   `AgencyRiskConfig.procedures`, and `thresholds.yaml` is rejected if it
   carries a `procedures` key at all.

## Decision
Option 3. The rule generalises: **every config value has exactly one editable
home**, and composition into contract shapes happens in
`engine/config_loader.py`, never by hand.

Applied in S1:
* `ProcedureFlags` live in `config/procedures/<id>_v0.yaml`; a `procedures` key
  in `thresholds.yaml` is a hard `ConfigError`.
* Routing-rule fixtures live next to the rules they exercise in
  `config/rules/routing_v0.yaml`; the loader rejects a rule that references an
  unknown fixture id and a fixture that expects an unknown rule id, and
  `tests/test_routing.py` runs every fixture against its rule.
* Anything not expressible in a contract model (field maps, clear-cut criteria,
  the rule-fixture block) gets a thin engine-local wrapper model in the loader.
  `schemas/` is never widened to accommodate a config-file layout.
* Config is parsed, not merely shape-checked: predicates are built into their
  AST and validation-constraint keys are checked against what the evaluator
  actually implements, so a typo fails at startup instead of silently
  evaluating to `False` forever.

## Consequences
- An operator changing a legal gate changes exactly one line in one file, and
  the system refuses to start if a second copy appears.
- The contracts stay a description of what crosses module boundaries, not a
  description of the file layout on disk.
- Cost: the config on disk is not a 1:1 image of the contract objects, so the
  loader is the place to look when tracing where a value came from. That
  indirection is documented in the loader's module docstring.
