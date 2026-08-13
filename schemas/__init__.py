"""EingangsLotse data contracts v0.

Single source of truth for every payload that crosses a module boundary.
Contracts may only change via an ADR (see docs/adr/). Exported as versioned
JSON Schema artifacts by export_json_schema.py; those artifacts are part of
the openCode open-standard deliverable.

Layout:
    common.py     shared enums, span type, version stamp
    envelope.py   internal envelope produced by channel adapters
    textlayer.py  normalized text layer with offset map
    extraction.py span-verified extraction records
    evidence.py   routing + completeness evidence
    anomaly.py    shadow-scorer anomaly evidence (downgrade-only input)
    decision.py   deterministic tier decision record
    events.py     append-only journal event envelope
    config.py     agency-editable config formats incl. the decision table
                  (anomaly evidence is referencable ONLY in downgrade
                  conditions; the schema has no other field for it)
"""

SCHEMA_VERSION = "0.1.0"
