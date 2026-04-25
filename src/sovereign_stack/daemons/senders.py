"""
Daemon sender taxonomy.

All four v1.3.2 daemons (uncertainty resurfacer, metabolize, triage, bridge)
share the "daemon." prefix so comms search-by-sender is consistent. Halt
alerts use a dedicated sender so human reviewers can filter for them
without catching routine digests.

Convention: `daemon.<step-name>` for routine posts; `daemon.halt-alert`
for circuit-breaker notifications. Adding a new daemon? Add its sender
to ALL_DAEMON_SENDERS and import from here — do not hardcode strings in
daemon code or tests.
"""

# Routine posts — one per daemon.
SENDER_UNCERTAINTY = "daemon.uncertainty"
SENDER_METABOLIZE = "daemon.metabolize"
SENDER_TRIAGE = "daemon.triage"
SENDER_BRIDGE = "daemon.bridge"

# Cross-daemon halt surface. When any daemon halts, it posts here so
# Anthony has a single place to see circuit-breaker events.
SENDER_HALT_ALERT = "daemon.halt-alert"

ALL_DAEMON_SENDERS = frozenset({
    SENDER_UNCERTAINTY,
    SENDER_METABOLIZE,
    SENDER_TRIAGE,
    SENDER_BRIDGE,
    SENDER_HALT_ALERT,
})
