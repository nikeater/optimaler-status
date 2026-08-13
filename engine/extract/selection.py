"""Which extractor the running service uses, resolved once at startup.

Part 05 built two readers of prose and part 12 measured the live one. This
module is the switch between them - and it exists for the LOCAL showcase only.
The default is REPLAY, everywhere, always: the gate, CI, the container image and
the hosted demonstration never see anything else, and none of them has a model
endpoint to see it with.

**The resolution order**, most specific first:

1. ``EINGANGSLOTSE_EXTRACTOR`` - ``replay`` or ``live``, and nothing else. An
   unrecognized value is a startup error rather than a silent fall back to
   replay: an operator who wrote ``Live`` meant something, and a service that
   quietly ignored them would be running a posture nobody chose. ``replay`` set
   explicitly is a kill switch that beats an enabled config.
2. ``config/extraction/extraction_v1.yaml``'s ``live.enabled``. The config file
   has documented this as the way to turn a model on for the running service
   since part 05, while nothing read it; now something does. The shipped value
   is ``false``, so this changes nothing observable in any shipped posture.
3. Otherwise replay.

``EINGANGSLOTSE_EXTRACTOR_URL`` and ``EINGANGSLOTSE_EXTRACTOR_MODEL`` override
the config's ``base_url`` and ``model`` without editing a frozen-versioned file,
which is what makes "point the demo at a different model" a one-line change.
Timeout, attempts and chunk size keep coming from the config: they are policy,
not a machine's address.

**Live mode that cannot be built is a startup error.** ``live`` with no
resolvable endpoint raises here, for the same reason ``LivePolicy`` refuses an
enabled policy without a ``base_url``: an extractor that can never answer would
degrade every single item toward tier 3 while looking configured.

**Live mode that fails at RUNTIME is not an error at all.** Once the extractor
exists, ADR-020's discipline takes over unchanged: an endpoint that is down, a
request that times out, a body that does not fit the schema - each of them is
"no proposals", each is a discard toward tier 3, and each is journaled in the
EXTRACTED event's failure histogram. The service never returns an error to a
citizen because a model was not running. That is asserted, not hoped: nothing on
this path probes the endpoint at startup either, so a service configured for
live mode still starts, still ingests and still decides with the model absent.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from engine.config_loader import ConfigBundle
from engine.extract.llm import LiveExtractor, settings_from_policy

#: The switch. Absent, empty or ``replay`` is the deterministic path.
EXTRACTOR_ENV = "EINGANGSLOTSE_EXTRACTOR"

#: Endpoint root, e.g. ``http://localhost:11434``. The client appends the
#: OpenAI-compatible ``/v1/...`` paths itself.
EXTRACTOR_URL_ENV = "EINGANGSLOTSE_EXTRACTOR_URL"

#: Model tag, e.g. ``mistral:7b-instruct-v0.3-q4_K_M``. Pinned rather than
#: floating: ``mistral:latest`` names a different model on a different day, and
#: the version stamp would still say ``llm:mistral``.
EXTRACTOR_MODEL_ENV = "EINGANGSLOTSE_EXTRACTOR_MODEL"

REPLAY = "replay"
LIVE = "live"

#: The two legal values, in the order an error message should offer them.
MODES = (REPLAY, LIVE)


class ExtractorSelectionError(RuntimeError):
    """The requested extractor posture cannot be built. Raised at startup only."""


@dataclass(frozen=True)
class ExtractorPosture:
    """Which reader of prose this process runs, resolved from the environment.

    Frozen and resolved once, like the demo posture next door: a service whose
    extractor could change between two requests would be a service whose
    version stamp lies about one of them.
    """

    mode: str = REPLAY
    base_url: str = ""
    model: str = ""

    @property
    def live(self) -> bool:
        return self.mode == LIVE

    @classmethod
    def from_env(
        cls,
        config: ConfigBundle,
        environ: Mapping[str, str] | None = None,
    ) -> ExtractorPosture:
        """Resolve the posture. Anything unrecognized is an error, not a guess."""
        source = os.environ if environ is None else environ
        requested = source.get(EXTRACTOR_ENV, "").strip()
        if requested and requested not in MODES:
            raise ExtractorSelectionError(
                f"{EXTRACTOR_ENV}={requested!r} is not a known extractor; "
                f"expected one of {', '.join(MODES)}. Unset it for the default "
                f"({REPLAY}), which is what every gate and the hosted demo run."
            )
        mode = requested or (LIVE if config.extraction.live.enabled else REPLAY)
        return cls(
            mode=mode,
            base_url=source.get(EXTRACTOR_URL_ENV, "").strip(),
            model=source.get(EXTRACTOR_MODEL_ENV, "").strip(),
        )

    def describe(self) -> str:
        """One line for a startup log; never a secret, never a payload."""
        if not self.live:
            return "extractor: replay (deterministic; no model endpoint is used)"
        return f"extractor: live ({self.model} at {self.base_url})"


def build_extractor(
    config: ConfigBundle,
    posture: ExtractorPosture | None = None,
    environ: Mapping[str, str] | None = None,
) -> LiveExtractor | None:
    """The live extractor for this process, or None for the replay default.

    None is not a failure and not a fallback: it is the shipped state, and
    ``run_pipeline`` already means "deterministic readers only" by it.
    """
    resolved = posture or ExtractorPosture.from_env(config, environ)
    if not resolved.live:
        return None
    settings = settings_from_policy(
        config.extraction.live,
        base_url=resolved.base_url or None,
        model=resolved.model or None,
    )
    if settings is None:
        raise ExtractorSelectionError(
            f"live extraction was selected but no endpoint is configured: set "
            f"{EXTRACTOR_URL_ENV} and {EXTRACTOR_MODEL_ENV}, or fill in "
            f"live.base_url and live.model in the extraction config. An "
            f"extractor that can never answer would push every item toward "
            f"tier 3 while looking configured."
        )
    return LiveExtractor(
        settings,
        system_prompt=config.extraction.prompt.system,
        user_prompt=config.extraction.prompt.user,
    )
