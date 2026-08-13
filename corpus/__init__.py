"""Corpus tooling and data.

``corpus/generator/`` builds the gold sets in ``corpus/gold/``. Neither is
imported by the engine, the API or the decision path, and neither is part of the
installed distribution - both are run from the repo root.
"""
