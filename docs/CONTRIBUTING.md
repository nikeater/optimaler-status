# Contributing

## Toolchain
- Python 3.12+, managed via a local venv in `eingangslotse/.venv`.
- Install dev deps: `pip install -e .[dev]` from `eingangslotse/`.

## Style
- `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE_CASE` constants.
- Formatting and linting: `ruff format` + `ruff check` (rules pinned in `pyproject.toml`).

## Typing (mypy strictness map)
| Path | Strictness |
|---|---|
| `engine/decide/` | `mypy --strict` |
| `engine/redact/` | `mypy --strict` |
| everything else | standard (`mypy` default config in `pyproject.toml`) |

## Tests
- `pytest` from `eingangslotse/`; Hypothesis for property tests.
- Coverage floor: 95% on `engine/decide` and `engine/redact`.
- Gates that run on every commit and never move: false-clear 0% on the frozen gold set, one-way valve monotonicity property test, vault canary tests, placeholder round-trip test.
- LLM calls are mocked in unit tests; the nightly eval job runs the real model.

## Contracts discipline
- `schemas/` is the single source of truth; any contract change requires an ADR in `docs/adr/` plus an index line in `docs/DESIGN_DECISIONS.md`.
- Re-export JSON Schema artifacts (`python -m schemas.export_json_schema`) whenever a contract changes.

## Git
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, scope optional (e.g. `feat(decide): ...`).
- Pre-commit hooks (to be wired in part 01): ruff, mypy, pytest quick suite.
- Never commit real personal data; the corpus is synthetic by construction.

## Logging discipline (privacy)
- Nothing un-redacted may appear in any log line; canary tests assert this.
- Log placeholders, ids, and versions, never vault content.
