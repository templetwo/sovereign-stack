"""
Sovereign Stack: The Complete Local AI System

Path is Model. Storage is Inference. Glob is Query.
The filesystem is not storage. It is a circuit.
Restraint is not constraint. It is conscience.
"""

# Read version from installed package metadata so we never have a single
# source of truth drift (pyproject.toml + this file + CHANGELOG.md). If
# the package is being run from a source tree without install, fall back
# to the literal that matches CHANGELOG's latest entry.
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version
    try:
        __version__ = _pkg_version("sovereign-stack")
    except PackageNotFoundError:
        __version__ = "1.3.2"
except ImportError:
    __version__ = "1.3.2"

from .coherence import AGENT_MEMORY_SCHEMA, Coherence
from .glyphs import GLYPHS, glyph_for
from .governance import GovernanceCircuit, ThresholdDetector
from .memory import ExperientialMemory, MemoryEngine
from .simulator import Simulator
from .spiral import SpiralMiddleware, SpiralPhase

__all__ = [
    # Routing
    "Coherence",
    "AGENT_MEMORY_SCHEMA",
    # Governance
    "ThresholdDetector",
    "GovernanceCircuit",
    # Simulation
    "Simulator",
    # Memory
    "ExperientialMemory",
    "MemoryEngine",
    # Spiral
    "SpiralMiddleware",
    "SpiralPhase",
    # Glyphs
    "GLYPHS",
    "glyph_for",
]
