"""Shared fixtures and the Hypothesis profile."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

from engine.config_loader import ConfigBundle, load_config
from engine.journal.store import InMemoryJournalStore
from schemas.common import VersionStamp

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = REPO_ROOT / "corpus" / "gold" / "s1"
GOLD_V1_DIR = REPO_ROOT / "corpus" / "gold" / "v1"
GOLD_V2_DIR = REPO_ROOT / "corpus" / "gold" / "v2"
GOLD_V3_DIR = REPO_ROOT / "corpus" / "gold" / "v3"
GOLD_V4_DIR = REPO_ROOT / "corpus" / "gold" / "v4"
SCENARIO_DIR = REPO_ROOT / "corpus" / "generator" / "scenarios"

settings.register_profile(
    "eingangslotse",
    deadline=None,
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.load_profile("eingangslotse")


@pytest.fixture(scope="session")
def config() -> ConfigBundle:
    """The real config in ``config/``: tests run against what ships."""
    return load_config(REPO_ROOT / "config")


@pytest.fixture
def versions(config: ConfigBundle) -> VersionStamp:
    """Version stamp derived from the real config."""
    return config.version_stamp()


@pytest.fixture
def journal() -> InMemoryJournalStore:
    """A fresh in-memory journal."""
    return InMemoryJournalStore()


@pytest.fixture(scope="session")
def gold_dir() -> Path:
    """The S1 pre-gold corpus directory.

    Superseded twice over and kept only to prove that its sidecars still load
    (``tests/test_eval.py``). Its two items are NOT used as behavioural
    fixtures any more: they carry part-01 Versicherungsnummern with the birth
    date in the wrong digits, which part 03's structural check rejects, and a
    frozen set is never edited to keep a test green.
    """
    return GOLD_DIR


@pytest.fixture(scope="session")
def gold_v1_dir() -> Path:
    """The frozen part-02 gold set, superseded by v2."""
    return GOLD_V1_DIR


@pytest.fixture(scope="session")
def gold_v2_dir() -> Path:
    """The frozen part-03 gold set, superseded by v3."""
    return GOLD_V2_DIR


@pytest.fixture(scope="session")
def gold_v3_dir() -> Path:
    """The frozen part-03b gold set, superseded by v4."""
    return GOLD_V3_DIR


@pytest.fixture(scope="session")
def gold_v4_dir() -> Path:
    """The current frozen gold set: v3's forms plus 24 free-text letters."""
    return GOLD_V4_DIR


@pytest.fixture(scope="session")
def scenario_dir() -> Path:
    """The scenario specs the gold set is generated from."""
    return SCENARIO_DIR
