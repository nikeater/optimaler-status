"""EingangsLotse engine.

Two planes, kept apart on purpose:

* evidence plane (``ingest``, ``extract``, ``evidence``) produces evidence only,
* decision plane (``decide``) makes every decision, deterministically, from
  versioned config,

plus ``journal`` (append-only event store) and ``pipeline`` (the S1 wiring that
runs one item through both planes).
"""
