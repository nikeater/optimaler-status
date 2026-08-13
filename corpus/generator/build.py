"""Corpus build CLI: ``python -m corpus.generator.build --out corpus/gold/v1``.

The build is a pipeline of refusals. It renders every scenario spec, paraphrases
it, then runs the real engine over the result and compares the outcome with the
declared labels. Any mismatch that was not declared in the spec aborts the whole
build with the offending items listed; a declared divergence that failed to
occur aborts it too. A generator bug therefore produces no corpus at all, which
is the only acceptable failure mode for a tool that writes ground truth.

Determinism: output is a pure function of (scenario specs, seed, generator
version, paraphrase mode). Re-running the command over a committed corpus
rewrites byte-identical files, so the verification gate can simply check that
the tree is still clean. ``--check`` does the same comparison without writing.

Usage::

    python -m corpus.generator.build --out corpus/gold/v1 --seed 42
    python -m corpus.generator.build --check          # verify, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from corpus.generator.llm import LlmClient, settings_from_env
from corpus.generator.paraphrase import (
    DeterministicParaphraser,
    LlmParaphraser,
    NullParaphraser,
    Paraphraser,
)
from corpus.generator.render import (
    GENERATOR_VERSION,
    GeneratorError,
    check_field_paths,
    item_rng,
    render_labels,
    render_payload,
)
from corpus.generator.spec import ScenarioSpec, parse_scenario_file
from engine.config_loader import ConfigBundle, load_config
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import PipelineResult, run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_DIR = REPO_ROOT / "corpus" / "generator" / "scenarios"
DEFAULT_GOLD_SET = "v4"
DEFAULT_OUT_DIR = Path("corpus/gold") / DEFAULT_GOLD_SET
DEFAULT_SEED = 42
LABEL_SUFFIX = ".labels.yaml"
MANIFEST_NAME = "MANIFEST.yaml"
REGISTRY_PATH = REPO_ROOT / "corpus" / "gold" / "REGISTRY.yaml"

#: Deliberately free of the output path: two builds of the same specs with the
#: same seed must be byte-identical wherever they are written.
LABELS_HEADER = (
    "# Ground truth for one gold item. GENERATED - do not edit by hand.\n"
    "# Rebuild with: python -m corpus.generator.build --seed {seed}\n"
)

FREEZE_POLICY = (
    "Gold items are never trained on and never edited. A label that turns out "
    "to be wrong is fixed by superseding this whole set with a new versioned "
    "one (corpus/gold/v5/), never by editing an item here. The generator is the "
    "only writer of this directory."
)


@dataclass(frozen=True)
class BuiltItem:
    """One rendered corpus item, before it touches the disk."""

    spec: ScenarioSpec
    payload: dict[str, Any]
    labels: dict[str, Any]
    provenance: str

    @property
    def item_id(self) -> str:
        return self.spec.scenario_id

    def payload_bytes(self) -> str:
        return json.dumps(self.payload, indent=2, ensure_ascii=False) + "\n"

    def labels_bytes(self, *, seed: int) -> str:
        header = LABELS_HEADER.format(seed=seed)
        return header + yaml.safe_dump(
            self.labels, sort_keys=False, allow_unicode=True, default_flow_style=False
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.payload_bytes().encode("utf-8")).hexdigest()


def load_specs(directory: Path) -> list[ScenarioSpec]:
    """Load every scenario file in ``directory``, in file-name order."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise GeneratorError(f"no scenario files found in {directory}")
    specs: list[ScenarioSpec] = []
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        specs.extend(parse_scenario_file(document, source=path.name))
    duplicates = [
        scenario_id
        for scenario_id, count in Counter(spec.scenario_id for spec in specs).items()
        if count > 1
    ]
    if duplicates:
        raise GeneratorError(f"duplicate scenario ids: {sorted(duplicates)}")
    return specs


def validate_specs(specs: Sequence[ScenarioSpec], config: ConfigBundle) -> None:
    """Check every spec against the shipped config before rendering anything."""
    problems: list[str] = []
    unit_ids = {node.unit_id for node in config.taxonomy.nodes}
    for spec in specs:
        if spec.expected.unit_id is not None and spec.expected.unit_id not in unit_ids:
            problems.append(
                f"{spec.scenario_id}: expected unit {spec.expected.unit_id!r} is "
                f"not in the taxonomy"
            )
        procedure = config.procedure(spec.procedure_id)
        if spec.procedure_id is not None and procedure is None:
            problems.append(
                f"{spec.scenario_id}: unknown procedure {spec.procedure_id!r}"
            )
            continue
        known = (
            {item.requirement_id for item in procedure.requirements.requirements}
            if procedure is not None
            else set()
        )
        for gap in spec.expected.gaps:
            if gap.requirement_id not in known:
                problems.append(
                    f"{spec.scenario_id}: expected gap {gap.requirement_id!r} is "
                    f"not a requirement of {spec.procedure_id!r}"
                )
    if problems:
        raise GeneratorError("invalid scenario specs:\n  " + "\n  ".join(problems))


def build_items(
    specs: Sequence[ScenarioSpec], *, seed: int, paraphraser: Paraphraser
) -> list[BuiltItem]:
    """Render and paraphrase every spec."""
    items: list[BuiltItem] = []
    for spec in specs:
        rng = item_rng(seed, spec.scenario_id)
        canonical = render_payload(spec, rng=rng)
        result = paraphraser.apply(spec, canonical, rng)
        items.append(
            BuiltItem(
                spec=spec,
                payload=result.payload,
                labels=render_labels(spec, paraphrase=result.provenance),
                provenance=result.provenance,
            )
        )
    return items


def self_check(items: Sequence[BuiltItem], config: ConfigBundle) -> None:
    """Run the real pipeline over every item and enforce the declared labels.

    Raises:
        GeneratorError: listing every item whose outcome does not match.
    """
    problems: list[str] = []
    for item in items:
        journal = InMemoryJournalStore()
        outcome = run_pipeline(item.payload, config=config, journal=journal)
        actual_tier = int(outcome.decision.tier)
        actual_unit = outcome.decision.routed_unit_id
        actual_gaps = sorted(
            (gap.requirement_id, gap.status.value)
            for gap in outcome.evidence.completeness.gaps
        )
        expected = item.spec.expected
        expected_gaps = sorted(
            (gap.requirement_id, gap.status) for gap in expected.gaps
        )
        divergence = set(expected.known_divergence)

        if expected.tier > 1 and actual_tier == 1:
            problems.append(
                f"{item.item_id}: FALSE CLEAR - expected tier {expected.tier}, "
                f"pipeline cleared it to tier 1"
            )
        elif "tier" in divergence:
            if actual_tier == expected.tier:
                problems.append(
                    f"{item.item_id}: declared tier divergence did not happen "
                    f"(both {actual_tier}); remove known_divergence"
                )
        elif actual_tier != expected.tier:
            problems.append(
                f"{item.item_id}: expected tier {expected.tier}, got {actual_tier}"
            )

        if "unit" in divergence:
            if actual_unit == expected.unit_id:
                problems.append(
                    f"{item.item_id}: declared unit divergence did not happen "
                    f"(both {actual_unit!r}); remove known_divergence"
                )
        elif actual_unit != expected.unit_id:
            problems.append(
                f"{item.item_id}: expected unit {expected.unit_id!r}, got "
                f"{actual_unit!r}"
            )

        if actual_gaps != expected_gaps:
            problems.append(
                f"{item.item_id}: expected gaps {expected_gaps}, got {actual_gaps}"
            )

        derivation = outcome.derivation
        if derivation.source.value != expected.derivation_source:
            problems.append(
                f"{item.item_id}: expected derivation source "
                f"{expected.derivation_source!r}, got {derivation.source.value!r}"
            )
        if derivation.procedure_id != item.spec.expected_procedure_id:
            problems.append(
                f"{item.item_id}: expected derived procedure "
                f"{item.spec.expected_procedure_id!r}, got "
                f"{derivation.procedure_id!r}"
            )
        problems.extend(_letter_problems(item, outcome))
    if problems:
        raise GeneratorError(
            "self-check failed, no corpus written:\n  " + "\n  ".join(problems)
        )


def _letter_problems(item: BuiltItem, outcome: PipelineResult) -> list[str]:
    """The three claims a letter item makes, checked at BUILD time (ADR-019).

    1. **Deterministic sealing is enough.** The pipeline just ran with the
       default union - no NER, no optional wheel - and did not refuse the item,
       so the working copy verified clean on the recognizers every machine has.
       This is what makes "the gate is not weaker than production" a fact about
       the corpus rather than a hope: production may add the model member, and
       the numbers this project quotes never depended on it.
    2. **Identity really left the letter.** A letter item whose sender block
       sealed nothing would look like a text item and exercise no span sealing,
       which is the failure mode where a corpus quietly stops testing something.
    3. **Every declared fact is locatable and verifiable.** The sidecar says
       where the generator wrote each value; if the double lock rejects one of
       them, the corpus and the letter disagree, and a corpus that ships a
       disagreement measures the disagreement forever.
    """
    if item.spec.letter is None:
        return []
    problems: list[str] = []
    if not outcome.envelope.redaction_verified:
        problems.append(f"{item.item_id}: letter did not verify clean after sealing")
    redaction = outcome.redaction
    if redaction is not None and redaction.text_sealed_count == 0:
        problems.append(
            f"{item.item_id}: nothing was sealed out of the letter; a letter "
            f"item with no identity in it exercises no span sealing"
        )
    extraction = outcome.extraction
    if extraction is not None and extraction.text_discarded_count:
        problems.append(
            f"{item.item_id}: {extraction.text_discarded_count} of "
            f"{len(extraction.verifications)} declared spans failed verification "
            f"({extraction.failure_counts()}); the sidecar and the letter disagree"
        )
    return problems


def build_manifest(
    items: Sequence[BuiltItem],
    *,
    seed: int,
    config: ConfigBundle,
    scenario_dir: Path,
    paraphrase_mode: str,
    llm_model: str | None,
    gold_set: str = DEFAULT_GOLD_SET,
) -> dict[str, Any]:
    """The MANIFEST: what this corpus is, and the policy that keeps it honest."""
    stamp = config.version_stamp()
    return {
        "gold_set": gold_set,
        "frozen": True,
        "policy": FREEZE_POLICY,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "paraphrase_mode": paraphrase_mode,
        "llm_model": llm_model,
        "scenario_source": _repo_relative(scenario_dir),
        "config_versions": {
            "schema": stamp.schema_version,
            "taxonomy": stamp.taxonomy_version,
            "rules": stamp.rules_version,
            "decision_table": stamp.decision_table_version,
            "thresholds": stamp.thresholds_version,
        },
        "requirements_versions": {
            procedure_id: procedure.requirements.version
            for procedure_id, procedure in sorted(config.procedures.items())
        },
        "counts": {
            "items": len(items),
            "by_kind": _counts(item.spec.kind.value for item in items),
            "by_procedure": _counts(
                item.spec.procedure_id or "unknown" for item in items
            ),
            "by_expected_tier": _counts(str(item.spec.expected.tier) for item in items),
            "by_paraphrase": _counts(item.provenance for item in items),
            "by_channel": _counts(item.spec.channel for item in items),
            "letters": sum(1 for item in items if item.spec.letter is not None),
            "ocr_letters": sum(
                1
                for item in items
                if item.spec.letter is not None and item.spec.letter.ocr_noise
            ),
            "by_derivation_source": _counts(
                item.spec.expected.derivation_source for item in items
            ),
            "anomaly_expected": sum(1 for item in items if item.spec.anomaly_expected),
            "known_divergences": sum(
                1 for item in items if item.spec.expected.known_divergence
            ),
        },
        "items": [
            {
                "item_id": item.item_id,
                "kind": item.spec.kind.value,
                "procedure_id": item.spec.procedure_id,
                "expected_tier": item.spec.expected.tier,
                "expected_unit_id": item.spec.expected.unit_id,
                "derivation_source": item.spec.expected.derivation_source,
                "anomaly_expected": item.spec.anomaly_expected,
                "channel": item.spec.channel,
                "letter": item.spec.letter is not None,
                "paraphrase": item.provenance,
                "sha256": item.sha256(),
            }
            for item in items
        ],
    }


def render_files(
    items: Sequence[BuiltItem],
    manifest: dict[str, Any],
    *,
    seed: int,
    gold_set: str = DEFAULT_GOLD_SET,
) -> dict[str, str]:
    """The complete file set this build would write, name -> text.

    Independent of the output directory on purpose: the same specs and seed
    produce the same bytes wherever they land, so ``--check`` can compare a
    build against the committed corpus without path noise.
    """
    files: dict[str, str] = {}
    for item in items:
        files[f"{item.item_id}.json"] = item.payload_bytes()
        files[f"{item.item_id}{LABEL_SUFFIX}"] = item.labels_bytes(seed=seed)
    files[MANIFEST_NAME] = (
        "# GENERATED corpus manifest - do not edit by hand.\n"
        f"# Rebuild with: python -m corpus.generator.build --out "
        f"corpus/gold/{gold_set} --seed {seed}\n"
    ) + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    return files


def write_corpus(files: dict[str, str], out: Path) -> list[str]:
    """Write the file set, dropping stale generated files. Returns removals."""
    out.mkdir(parents=True, exist_ok=True)
    for name, text in sorted(files.items()):
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    removed = [name for name in _generated_names(out) if name not in files]
    for name in removed:
        (out / name).unlink()
    return removed


def diff_corpus(files: dict[str, str], out: Path) -> list[str]:
    """Differences between the built file set and what is on disk."""
    differences: list[str] = []
    for name, text in sorted(files.items()):
        path = out / name
        if not path.is_file():
            differences.append(f"missing: {name}")
        elif path.read_text(encoding="utf-8") != text:
            differences.append(f"differs: {name}")
    differences.extend(
        f"stale: {name}" for name in _generated_names(out) if name not in files
    )
    return differences


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    """The gold-set registry, keyed by directory name.

    The registry lives *outside* the frozen directories on purpose: recording
    "v1 was superseded by v2" inside v1 would be an edit to a frozen set, and
    ADR-010 has exactly one rule about editing frozen sets.
    """
    if not path.is_file():
        return {}
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = document.get("sets", [])
    if not isinstance(entries, list):
        raise GeneratorError(f"{path}: 'sets' must be a list")
    return {str(entry["gold_set"]): dict(entry) for entry in entries}


def verify_integrity(out: Path) -> list[str]:
    """Verify a frozen set against its own MANIFEST. Returns the problems.

    This is what ``--check`` does for a superseded set, and it is a different
    question from the one a rebuild answers. A rebuild asks "does today's
    engine still produce these labels", which for a superseded set is *meant*
    to be no - that is why it was superseded. Integrity asks the question that
    stays meaningful forever: are these still the bytes that were frozen?
    """
    manifest_path = out / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"no {MANIFEST_NAME} in {out.as_posix()}: nothing to verify against"]
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    listed: set[str] = set()
    for entry in manifest.get("items", []):
        item_id = str(entry["item_id"])
        listed.update({f"{item_id}.json", f"{item_id}{LABEL_SUFFIX}"})
        payload_path = out / f"{item_id}.json"
        label_path = out / f"{item_id}{LABEL_SUFFIX}"
        if not payload_path.is_file():
            problems.append(f"missing: {payload_path.name}")
            continue
        if not label_path.is_file():
            problems.append(f"missing labels sidecar: {label_path.name}")
        digest = hashlib.sha256(payload_path.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            problems.append(
                f"sha256 mismatch: {payload_path.name} "
                f"(manifest {entry.get('sha256')}, file {digest})"
            )
    listed.add(MANIFEST_NAME)
    problems.extend(
        f"not listed in the manifest: {name}"
        for name in _generated_names(out)
        if name not in listed
    )
    return problems


def summary(manifest: dict[str, Any], *, out: Path) -> str:
    """One-screen build summary."""
    counts = manifest["counts"]
    lines = [
        "EingangsLotse corpus build",
        f"  out dir            {out.as_posix()}",
        f"  generator          {manifest['generator_version']}  seed "
        f"{manifest['seed']}",
        f"  paraphrase         mode={manifest['paraphrase_mode']} "
        f"provenance={counts['by_paraphrase']}",
        f"  items              {counts['items']}",
        f"  by kind            {counts['by_kind']}",
        f"  by procedure       {counts['by_procedure']}",
        f"  by expected tier   {counts['by_expected_tier']}",
        f"  by channel         {counts['by_channel']}",
        f"  letters            {counts['letters']}  (OCR: {counts['ocr_letters']})",
        f"  anomaly expected   {counts['anomaly_expected']}",
        f"  known divergences  {counts['known_divergences']}",
    ]
    return "\n".join(lines)


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _repo_relative(path: Path) -> str:
    """Repo-relative posix path, or the bare name for a path outside the repo."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.name


def _generated_names(out: Path) -> list[str]:
    if not out.is_dir():
        return []
    return sorted(
        path.name
        for path in out.iterdir()
        if path.is_file()
        and (
            path.name == MANIFEST_NAME
            or path.name.endswith(LABEL_SUFFIX)
            or path.suffix == ".json"
        )
    )


def _select_paraphraser(
    mode: str, *, base_url: str | None, model: str | None
) -> tuple[Paraphraser, str | None]:
    """Pick the paraphrase strategy; returns it plus the model id if any.

    ``auto`` only probes when an endpoint was configured, so a developer who
    happens to run a local model cannot silently produce a different corpus
    than the committed one.
    """
    deterministic = DeterministicParaphraser()
    if mode == "none":
        return NullParaphraser(), None
    if mode == "deterministic":
        return deterministic, None
    settings = settings_from_env(base_url, model)
    if settings is None:
        if mode == "llm":
            raise GeneratorError(
                "--paraphrase llm needs --llm-base-url or EINGANGSLOTSE_LLM_BASE_URL"
            )
        return deterministic, None
    client = LlmClient(settings)
    if not client.available():
        if mode == "llm":
            raise GeneratorError(
                f"--paraphrase llm requested but {settings.base_url} is unreachable"
            )
        return deterministic, None
    return LlmParaphraser(client, deterministic), settings.model


def main(argv: list[str] | None = None) -> int:
    """Build (or check) the gold corpus."""
    parser = argparse.ArgumentParser(prog="corpus.generator.build", description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--gold-set",
        default=None,
        help="corpus identity written into the MANIFEST; defaults to the name "
        f"of the output directory (default: {DEFAULT_GOLD_SET})",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIO_DIR)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against the corpus on disk and write nothing",
    )
    parser.add_argument(
        "--paraphrase",
        choices=["auto", "deterministic", "llm", "none"],
        default="auto",
        help="auto: use the LLM endpoint when one is configured and reachable, "
        "deterministic otherwise",
    )
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-model", default=None)
    args = parser.parse_args(argv)
    gold_set = args.gold_set or args.out.name

    try:
        registry = load_registry()
        entry = registry.get(gold_set, {})
        if args.check and entry.get("status") == "superseded":
            return _check_frozen(args.out, gold_set, entry)
        config = load_config(args.config)
        check_field_paths(config)
        specs = load_specs(args.scenarios)
        validate_specs(specs, config)
        paraphraser, llm_model = _select_paraphraser(
            args.paraphrase, base_url=args.llm_base_url, model=args.llm_model
        )
        items = build_items(specs, seed=args.seed, paraphraser=paraphraser)
        self_check(items, config)
        manifest = build_manifest(
            items,
            seed=args.seed,
            gold_set=gold_set,
            config=config,
            scenario_dir=args.scenarios,
            # The EFFECTIVE strategy, not what was asked for: `auto` that fell
            # back to deterministic must produce the same manifest as an
            # explicit `--paraphrase deterministic`, or the corpus would depend
            # on which flag someone typed.
            paraphrase_mode=paraphraser.name,
            llm_model=llm_model,
        )
        files = render_files(items, manifest, seed=args.seed, gold_set=gold_set)
    except GeneratorError as error:
        print(f"corpus build failed:\n{error}", file=sys.stderr)
        return 2

    print(summary(manifest, out=args.out))
    if args.check:
        differences = diff_corpus(files, args.out)
        if differences:
            print("\n  CHECK FAILED, corpus on disk differs:", file=sys.stderr)
            for difference in differences:
                print(f"    {difference}", file=sys.stderr)
            return 1
        print(f"\n  check passed: {args.out.as_posix()} matches this build")
        return 0
    removed = write_corpus(files, args.out)
    print(f"\n  wrote {len(files)} files to {args.out.as_posix()}")
    for name in removed:
        print(f"  removed stale {name}")
    return 0


def _check_frozen(out: Path, gold_set: str, entry: dict[str, Any]) -> int:
    """``--check`` for a superseded set: integrity only, never a rebuild."""
    successor = entry.get("superseded_by", "a newer set")
    mode = str(entry.get("verification", "integrity"))
    print(
        f"EingangsLotse corpus check\n"
        f"  gold set           {gold_set} (superseded by {successor})\n"
        f"  mode               {mode}\n"
        f"  note               a frozen set is verified by its bytes, not by "
        f"re-running today's engine over it (ADR-015)"
    )
    if mode == "none":
        # Hand-written pre-gold scaffolding: no generator wrote it, no manifest
        # describes it, so there is no claim to verify. Saying that beats
        # inventing a check that would always pass.
        print(f"\n  nothing to verify: {entry.get('note', '')}".rstrip())
        return 0
    problems = verify_integrity(out)
    if problems:
        print("\n  CHECK FAILED, frozen corpus is not intact:", file=sys.stderr)
        for problem in problems:
            print(f"    {problem}", file=sys.stderr)
        return 1
    print(f"\n  check passed: {out.as_posix()} matches its manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
