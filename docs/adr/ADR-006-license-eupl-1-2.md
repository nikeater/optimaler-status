# ADR-006: License - EUPL-1.2

Status: Accepted 2026-08-12

## Context

EingangsLotse is an open-source, API-first triage engine for German public-sector mass procedures. The license choice was named in week 1 (getting_started Section 5) and deliberately left to the user; every part since has shipped without a LICENSE file, which also meant the repository could not be published. The S1-S10 sequence is complete and the user has directed a public showcase (GitHub + a hosted demo), which makes the decision due now. The user chose EUPL-1.2 over Apache-2.0 on 2026-08-12.

## Options considered

1. **EUPL-1.2** - the European Union Public Licence. Copyleft. Written by the European Commission for public-sector software, with legally equivalent versions in the EU official languages including German. The license expected by German public-sector code platforms (openCode) and consistent with a public-money-public-code posture. Its compatibility clause permits downstream combination with the major copyleft licenses listed in its appendix (GPLv2/v3, AGPLv3, MPL-2.0 and others).
2. **Apache-2.0** - permissive, maximizes adoption by vendors and integrators, includes an explicit patent grant, and is the default of much of the Python ecosystem. Weaker fit for the project's audience: it allows proprietary forks of a system whose entire trust argument is that an agency can read every decision path.

## Decision

**EUPL-1.2.**

The deciding argument is coherence with what the system claims to be. The product's compliance story rests on inspectability - deterministic decisions, versioned configs, an auditable journal - and a copyleft license keeps derivatives of exactly those parts inspectable. The target deployers are German agencies, the target code platform is openCode, and the EUPL is the one license in the candidate set with a legally binding German text authored for precisely this setting.

Inbound compatibility is unproblematic: the project's dependencies (FastAPI, pydantic, Jinja2, HTMX, scikit-learn, presidio, spaCy, torch and the rest) are permissively licensed (MIT/BSD/Apache-2.0), which the EUPL can incorporate without conflict.

## Consequences

- The repository gains a `LICENSE` file carrying the official EUPL-1.2 English text verbatim (the official texts exist in all EU languages; English is the conventional repository copy). The text must be obtained from the official source, never retyped or paraphrased.
- `pyproject.toml` declares the SPDX identifier `EUPL-1.2`; the README gains a license section noting the multilingual official versions.
- Per-file license headers are not required for the showcase. If the project later publishes on openCode, REUSE-style annotation is the known refinement and is recorded as part of that release work (alongside P-11's transparency record and P-12's release practice).
- Contributions are accepted under the same license; a CLA is deliberately not introduced.
- Implementation (LICENSE file, metadata, README section) is part 11's work; this ADR records the decision.
