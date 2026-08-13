"""The names a config rule may read, in one place.

Two modules need this vocabulary and they sit on opposite sides of an import
edge: :mod:`engine.evidence.context` BUILDS the evaluation context, and
:mod:`engine.config_loader` LINTS the rules that read it. The loader cannot
import the evidence plane (the evidence plane imports the loader), so until
part 05 the two kept their own copies of the prefixes with a comment asking them
not to drift. A frozenset of field names is where that stops being survivable,
so the vocabulary moved here: no imports, no logic, one definition.
"""

from __future__ import annotations

#: Scalar leaf of the structured payload, as received.
PAYLOAD_PREFIX = "payload."

#: Value of a field that went through a procedure's field map and, for text,
#: through span verification.
EXTRACTION_PREFIX = "extraction."

#: What the item SAYS: normalized, already redacted free text.
TEXT_PREFIX = "text."

#: The whole ``text.*`` namespace. Enumerated rather than open-ended: a rule
#: over ``text.something_else`` is a typo, and the config lint says so instead
#: of leaving behind a condition that can never be true.
TEXT_FIELDS = frozenset({"text.normalized", "text.source_types"})
