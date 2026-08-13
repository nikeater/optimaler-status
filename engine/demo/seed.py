"""Build the demo's state by running the real pipeline over frozen gold v4.

``python -m engine.demo.seed`` wipes the five state directories and re-runs
every item of ``corpus/gold/v4`` through exactly the machinery ``POST /ingest``
runs - :func:`engine.pipeline.run_pipeline`, then :func:`notify_case`, then
:func:`draft_case` - writing into the JSONL backends the env vars name. The gold
directory itself is only ever READ; nothing here writes under ``corpus/``.

Two properties make this usable as the demo's reset button:

* **It is deterministic.** Every source of variation is injected: the clock
  (``--now``), the placeholder token stream (``--token-seed``, per item) and the
  detector union (the deterministic one, never the optional NER model, so the
  state is identical inside and outside the container). What is left is the
  ``event_id`` of each journal event, which is a uuid4 the store mints, and the
  notification and draft ids derived from it. :func:`state_digest` folds the
  state with exactly those ids removed, and two resets produce the same digest.
* **A restart IS a reset.** The container entrypoint runs this on boot, so the
  hosted demo needs no in-process timer and no scheduler: whatever a visitor
  confirmed, overrode or escalated is gone the next time the service starts.
  Hosts that stop an idle free-tier service and start it again on the next
  request therefore reset the demo for free (see ``deploy/README.md``).

**The seeded data is synthetic and that is what makes the plaintext backends
acceptable here.** ``JsonlVaultStore`` and ``JsonlDraftStore`` say in their own
docstrings that they are development backends holding no real identity; the
demo posture (``engine/demo/mode.py``) is what keeps that true in public by
refusing ingest. The two halves only work together - a demo with an open ingest
and a plaintext vault would be a data-protection incident with a URL.

``SeededTokenSource`` is used here and would be indefensible anywhere else: a
predictable placeholder stream means anyone with the seed can predict every
token a case carries. Over synthetic corpus items there is nothing to predict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from engine.config_loader import ConfigBundle, load_config
from engine.dispatch import DISPATCH_DIR_ENV
from engine.draft import DRAFTS_DIR_ENV, JsonlDraftStore, draft_case
from engine.draft.projection import facts_from
from engine.journal.store import JsonlJournalStore
from engine.notify import OUTBOX_DIR_ENV, JsonlOutbox, notify_case
from engine.pipeline import run_pipeline
from engine.redact import JsonlVaultStore, SeededTokenSource
from engine.redact.placeholders import PLACEHOLDER_RE

# engine.demo is a DEPLOYMENT entry point, not part of the engine: nothing in
# the pipeline imports it, so reaching into eval for the corpus loader adds no
# cycle and no runtime weight to the decision path. It is deliberate rather
# than convenient - `load_corpus` is what the gate iterates, sidecar check
# included, so the demo cannot silently seed a different item set than the one
# the measured numbers describe.
from eval.harness import DEFAULT_GOLD_DIR, load_corpus

JOURNAL_DIR_ENV = "EINGANGSLOTSE_JOURNAL_DIR"
VAULT_DIR_ENV = "EINGANGSLOTSE_VAULT_DIR"

#: Default base for the per-item token seed. Any int works; it exists so a
#: deployment that wants a different placeholder stream can have one without
#: touching code.
DEFAULT_TOKEN_SEED = 20260812

#: Keys whose value is a freshly minted id rather than a fact about the state.
#: Removed before digesting - see the module docstring.
VOLATILE_KEYS = frozenset(
    {"event_id", "notification_id", "source_event_id", "draft_id"}
)


@dataclass(frozen=True)
class StatePaths:
    """Where the five file-backed stores live, in one object.

    One object because they are one lifecycle: the demo resets all five or none
    of them, and a reset that forgot the dispatch exports would leave last
    visitor's confirmations lying next to a fresh journal.
    """

    journal: Path
    vault: Path
    outbox: Path
    drafts: Path
    dispatch: Path

    @classmethod
    def under(cls, root: Path | str) -> StatePaths:
        """The conventional layout under one root directory."""
        base = Path(root)
        return cls(
            journal=base / "journal",
            vault=base / "vault",
            outbox=base / "outbox",
            drafts=base / "drafts",
            dispatch=base / "dispatch",
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> StatePaths:
        """The layout the env vars name; raises when one of them is missing.

        Deliberately all-or-nothing. A partial environment would seed a journal
        into a volume and the drafts into the container's filesystem, and the
        demo would look correct until the first restart lost half of it.
        """
        source = os.environ if environ is None else environ
        wanted = {
            "journal": JOURNAL_DIR_ENV,
            "vault": VAULT_DIR_ENV,
            "outbox": OUTBOX_DIR_ENV,
            "drafts": DRAFTS_DIR_ENV,
            "dispatch": DISPATCH_DIR_ENV,
        }
        found = {name: source.get(var, "").strip() for name, var in wanted.items()}
        missing = sorted(wanted[name] for name, value in found.items() if not value)
        if missing:
            raise SystemExit(
                "cannot seed: no --state-dir given and these env vars are unset: "
                + ", ".join(missing)
            )
        return cls(**{name: Path(value) for name, value in found.items()})

    def all(self) -> tuple[Path, ...]:
        return (self.journal, self.vault, self.outbox, self.drafts, self.dispatch)

    def as_env(self) -> dict[str, str]:
        """The env vars an app must carry to read this state back."""
        return {
            JOURNAL_DIR_ENV: str(self.journal),
            VAULT_DIR_ENV: str(self.vault),
            OUTBOX_DIR_ENV: str(self.outbox),
            DRAFTS_DIR_ENV: str(self.drafts),
            DISPATCH_DIR_ENV: str(self.dispatch),
        }


@dataclass(frozen=True)
class SeedSummary:
    """What one seeding run produced. Counts and ids, never a payload."""

    items: int
    cases: int
    notifications: int
    drafts: int
    unresolved_tokens: int
    gold_dir: str
    base_time: datetime

    def render(self) -> str:
        return "\n".join(
            [
                "EingangsLotse demo seed",
                f"  gold dir          {self.gold_dir}",
                f"  base time         {self.base_time.isoformat()}",
                f"  items seeded      {self.items}",
                f"  cases in journal  {self.cases}",
                f"  notifications     {self.notifications}",
                f"  drafts            {self.drafts} "
                f"({self.unresolved_tokens} unresolved tokens)",
            ]
        )


def reset_state(paths: StatePaths) -> None:
    """Wipe the five state directories and recreate them empty.

    Idempotent in both directions: it works on a state that does not exist yet
    and on one that is already full, and running it twice leaves the same five
    empty directories.
    """
    for directory in paths.all():
        if directory.is_dir():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)


def seed_state(
    paths: StatePaths,
    *,
    gold_dir: Path = DEFAULT_GOLD_DIR,
    config: ConfigBundle | None = None,
    now: datetime | None = None,
    token_seed: int = DEFAULT_TOKEN_SEED,
) -> SeedSummary:
    """Wipe the state and rebuild it from the frozen gold set.

    The fold per item is character for character what ``POST /ingest`` does, and
    that is the point: a demo whose state came from a shortcut would be a demo
    of the shortcut. ``item_index`` staggers the clock so the queue view has
    something to sort - a hundred cases received in the same microsecond is not
    a screen anyone can read - and every offset is a function of the item's
    position in the sorted corpus, so it is as deterministic as the rest.
    """
    bundle = config or load_config()
    base = now or datetime.now(UTC)
    reset_state(paths)
    journal = JsonlJournalStore(paths.journal)
    vault = JsonlVaultStore(paths.vault)
    outbox = JsonlOutbox(paths.outbox)
    drafts = JsonlDraftStore(paths.drafts)

    items = load_corpus(gold_dir)
    notifications = 0
    drafted_count = 0
    unresolved = 0
    for index, item in enumerate(items):
        # Oldest first: the last item of the corpus is the freshest case, so a
        # queue sorted by age shows the whole corpus rather than a flat block.
        moment = base - _age(len(items) - index)
        outcome = run_pipeline(
            item.payload,
            config=bundle,
            journal=journal,
            vault=vault,
            now=moment,
            # Per item, so one item's token stream cannot depend on how many
            # placeholders the items before it happened to need.
            token_source=SeededTokenSource(token_seed + index),
            # Deliberately the DETERMINISTIC union: the demo image ships without
            # the [redact] extra, and a seed that used the NER model when it was
            # installed would produce a different state on a developer machine
            # than in the container.
            text_detector=None,
        )
        case_id = outcome.decision.case_id
        sent = notify_case(
            journal.read(case_id),
            config=bundle,
            journal=journal,
            outbox=outbox,
            now=moment,
        )
        notifications += sent.count
        prepared = draft_case(
            journal.read(case_id),
            config=bundle,
            journal=journal,
            vault=vault,
            drafts=drafts,
            facts=facts_from(outcome.extractions),
            now=moment,
        )
        drafted_count += len(prepared.drafts)
        # The same check the eval's drafting section runs: a placeholder that
        # survived into a letter body is an unresolved token, and a demo that
        # showed one would be showing a broken re-hydration.
        unresolved += sum(
            len(PLACEHOLDER_RE.findall(record.body)) for record in prepared.drafts
        )
    return SeedSummary(
        items=len(items),
        cases=len(journal.case_ids()),
        notifications=notifications,
        drafts=drafted_count,
        unresolved_tokens=unresolved,
        gold_dir=str(gold_dir),
        base_time=base,
    )


def state_digest(paths: StatePaths) -> str:
    """A stable fingerprint of the seeded state, minus the minted ids.

    What it hashes: every state file's path relative to its store, and every
    line of it as canonical JSON with :data:`VOLATILE_KEYS` stripped at every
    depth. What it therefore proves when two runs agree: the same cases, the
    same events in the same order with the same payloads and the same clock,
    the same notifications and the same letters. What it cannot prove: that the
    uuid4s matched, which they never will and never should.
    """
    digest = hashlib.sha256()
    for label, directory in _stores(paths):
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            digest.update(f"{label}/{relative}\n".encode())
            digest.update(_canonical(path).encode("utf-8"))
    return digest.hexdigest()


def _stores(paths: StatePaths) -> tuple[tuple[str, Path], ...]:
    return (
        ("journal", paths.journal),
        ("vault", paths.vault),
        ("outbox", paths.outbox),
        ("drafts", paths.drafts),
        ("dispatch", paths.dispatch),
    )


def _canonical(path: Path) -> str:
    """One state file as canonical JSON lines, or verbatim when it is not JSON."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.dumps(_strip(json.loads(text)), sort_keys=True) + "\n"
    if path.suffix != ".jsonl":
        return text
    return "".join(
        json.dumps(_strip(json.loads(line)), sort_keys=True) + "\n"
        for line in text.splitlines()
        if line.strip()
    )


def _strip(value: Any) -> Any:
    """Drop every volatile id, at every depth."""
    if isinstance(value, dict):
        return {
            key: _strip(inner)
            for key, inner in value.items()
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip(inner) for inner in value]
    return value


def _age(rank: int) -> timedelta:
    """How far back the ``rank``-th-newest case was received.

    17 minutes because it is coprime with 60: hourly buckets in a queue view
    should not all land on the same minute.
    """
    return timedelta(minutes=17 * rank)


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface. Separate from :func:`main` so a test can read it."""
    parser = argparse.ArgumentParser(
        prog="python -m engine.demo.seed",
        description=(
            "Wipe the demo state directories and rebuild them by running the "
            "frozen gold corpus through the real pipeline. Reads corpus/gold/, "
            "never writes to it."
        ),
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "root under which journal/, vault/, outbox/, drafts/ and dispatch/ "
            "are created. Default: the five EINGANGSLOTSE_*_DIR env vars, all "
            "of which must then be set."
        ),
    )
    parser.add_argument(
        "--gold-dir",
        default=str(DEFAULT_GOLD_DIR),
        help=f"frozen corpus to seed from (default: {DEFAULT_GOLD_DIR})",
    )
    parser.add_argument(
        "--now",
        default=None,
        help=(
            "ISO-8601 base timestamp the seeded clock counts back from. "
            "Default: the current UTC time, so a fresh restart shows fresh "
            "queues. Pin it to make a run reproducible byte for byte."
        ),
    )
    parser.add_argument(
        "--token-seed",
        type=int,
        default=DEFAULT_TOKEN_SEED,
        help=(
            "base seed of the per-item placeholder stream "
            f"(default: {DEFAULT_TOKEN_SEED}). Synthetic data only."
        ),
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="print the state digest after seeding (ids excluded; see module doc)",
    )
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Wipe and re-seed. Exit 0, or SystemExit from an unusable environment."""
    args = build_parser().parse_args(argv)
    paths = (
        StatePaths.under(args.state_dir) if args.state_dir else StatePaths.from_env()
    )
    summary = seed_state(
        paths,
        gold_dir=Path(args.gold_dir),
        now=_parse_now(args.now),
        token_seed=args.token_seed,
    )
    lines: tuple[str, ...] = ()
    if not args.quiet:
        lines = (summary.render(), *_env_lines(paths))
    if args.digest:
        lines = (*lines, f"  state digest      {state_digest(paths)}")
    for line in lines:
        print(line)
    return 0


def _env_lines(paths: StatePaths) -> tuple[str, ...]:
    """The env vars an operator has to hand the app to see this state."""
    return tuple(f"  {key:<32}{value}" for key, value in sorted(paths.as_env().items()))


def _parse_now(raw: str | None) -> datetime | None:
    """An ISO-8601 base timestamp; a naive one is read as UTC."""
    if raw is None:
        return None
    moment = datetime.fromisoformat(raw)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
