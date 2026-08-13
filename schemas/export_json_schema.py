"""Export all contracts as versioned JSON Schema artifacts.

Usage: python -m schemas.export_json_schema [outdir]
Writes schemas/artifacts/v<SCHEMA_VERSION>/<Model>.schema.json
These artifacts are part of the openCode open-standard deliverable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from . import SCHEMA_VERSION
from .anomaly import AnomalyEvidence
from .config import (
    AgencyRiskConfig,
    DecisionTable,
    RequirementList,
    RoutingRule,
    TaxonomyNode,
)
from .decision import DecisionRecord
from .envelope import Envelope
from .events import Event
from .evidence import EvidenceRecord
from .extraction import ExtractionSet
from .textlayer import TextLayer

EXPORTED: list[type[BaseModel]] = [
    Envelope,
    TextLayer,
    ExtractionSet,
    EvidenceRecord,
    AnomalyEvidence,
    DecisionRecord,
    Event,
    DecisionTable,
    AgencyRiskConfig,
    RoutingRule,
    RequirementList,
    TaxonomyNode,
]


def main() -> None:
    outdir = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).parent / "artifacts" / f"v{SCHEMA_VERSION}"
    )
    outdir.mkdir(parents=True, exist_ok=True)
    for model in EXPORTED:
        schema = model.model_json_schema()
        schema["$id"] = (
            f"https://opencode.de/eingangslotse/schemas/"
            f"v{SCHEMA_VERSION}/{model.__name__}.schema.json"
        )
        path = outdir / f"{model.__name__}.schema.json"
        path.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
