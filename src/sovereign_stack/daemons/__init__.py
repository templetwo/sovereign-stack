"""
Sovereign Stack daemons — scheduled reflection workers.

Each daemon runs out-of-band (via launchd) and follows the same invariants:

  1. compass_check before any high-stakes action; PAUSE halts the run.
  2. grounded_extract before any comms post; un-grounded content is skipped,
     not posted as hypothesis.
  3. Comms digests carry ack_required=True. Consecutive unacked digests
     above the daemon's threshold trigger a halt-write + halt-alert.
  4. Every daemon writes its halt notes to a shared halts/ directory so
     Anthony has one place to look.
  5. Sender taxonomy from .senders — do NOT invent ad hoc sender strings.

Shared scaffolding lives in base.py (BaseDaemon, DaemonState, the
constants and outcome codes that are stable across daemons). Subclass
to build a new scheduled reflection daemon — see uncertainty_resurfacer
and metabolize_daemon for the pattern.
"""

from .base import (
    COMPASS_PAUSE,
    COMPASS_PROCEED,
    COMPASS_WITNESS,
    CONSECUTIVE_UNACKED_THRESHOLD,
    DEFAULT_CHANNEL,
    OUTCOME_ALREADY_HALTED,
    OUTCOME_DRY_RUN,
    OUTCOME_GROUNDING_FAILED,
    OUTCOME_HALTED,
    OUTCOME_PAUSED,
    OUTCOME_POSTED,
    POSTED_DIGESTS_RETAINED,
    STATE_SCHEMA_VERSION,
    BaseDaemon,
    DaemonState,
)
from .metabolize_daemon import (
    MAX_DIGEST_ITEMS_PER_CATEGORY,
    MetabolizeDaemon,
)
from .senders import (
    ALL_DAEMON_SENDERS,
    SENDER_BRIDGE,
    SENDER_HALT_ALERT,
    SENDER_METABOLIZE,
    SENDER_TRIAGE,
    SENDER_UNCERTAINTY,
)
from .uncertainty_resurfacer import (
    MAX_DIGEST_UNCERTAINTIES,
    UncertaintyResurfacer,
)

__all__ = [
    # Sender taxonomy
    "SENDER_UNCERTAINTY",
    "SENDER_METABOLIZE",
    "SENDER_TRIAGE",
    "SENDER_BRIDGE",
    "SENDER_HALT_ALERT",
    "ALL_DAEMON_SENDERS",
    # Base scaffolding
    "BaseDaemon",
    "DaemonState",
    "STATE_SCHEMA_VERSION",
    "CONSECUTIVE_UNACKED_THRESHOLD",
    "POSTED_DIGESTS_RETAINED",
    "DEFAULT_CHANNEL",
    "COMPASS_PAUSE",
    "COMPASS_PROCEED",
    "COMPASS_WITNESS",
    "OUTCOME_POSTED",
    "OUTCOME_PAUSED",
    "OUTCOME_HALTED",
    "OUTCOME_ALREADY_HALTED",
    "OUTCOME_GROUNDING_FAILED",
    "OUTCOME_DRY_RUN",
    # Concrete daemons
    "UncertaintyResurfacer",
    "MetabolizeDaemon",
    "MAX_DIGEST_UNCERTAINTIES",
    "MAX_DIGEST_ITEMS_PER_CATEGORY",
]
