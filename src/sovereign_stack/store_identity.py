"""The stable label of the machine whose Stack chronicle this install serves.

THE CONTRACT (plan 1, HQ's ruling of 2026-10-22 from the commit review of
design B6). The store is named by ONE label, stated here once, so the surfaces
that name it cannot drift apart:

  * the recall envelope's ``scope.stores_searched`` (design B6, plan 1 Task 3)
    reads ``stack-chronicle:<STUDIO_HOST_LABEL>``;
  * a receipt ref's seat tag, ``studio:~/...`` (design C3, plan 1 Task 4), is
    this label.

Read it through the module (``store_identity.STUDIO_HOST_LABEL``) where it is
used, never ``from .store_identity import STUDIO_HOST_LABEL``: a name bound at
import is a second copy, and tests/test_recall_scope_line.py pins that the
envelope follows this one.

WHY A CONSTANT AND NOT socket.gethostname(). The label names the store, not the
machine's current network name. Measured on the Mac Studio, 2026-10-22:
``scutil --get HostName`` prints "not set", while LocalHostName, kern.hostname
and socket.gethostname() read "mac-studio". With HostName unset the kernel
hostname is a value configd derives, and macOS can replace it with a name the
network supplies (inferred from platform behaviour, not observed), so a label
read from it could change while the store stays the same.

WHAT THIS DOES NOT DECIDE. Whether the machine running this code IS the Studio
is a different question: Task 4 must answer it before it strips a ``studio:``
tag, because ``tag == STUDIO_HOST_LABEL`` is true for every such ref on every
machine.

RESIDUAL, NAMED. The label is fixed. If this code ever serves another machine's
own chronicle, the envelope will still say "stack-chronicle:studio" and name
the wrong store. A config-provided id, the other form HQ's ruling allows, is the
general answer and is not built here.
"""

STUDIO_HOST_LABEL = "studio"
