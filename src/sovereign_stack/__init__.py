"""
Sovereign Stack: The Complete Local AI System

Path is Model. Storage is Inference. Glob is Query.
The filesystem is not storage. It is a circuit.
Restraint is not constraint. It is conscience.
"""

__version__ = "1.0.0"

from .coherence import Coherence, AGENT_MEMORY_SCHEMA
from .governance import ThresholdDetector, GovernanceCircuit
from .simulator import Simulator
from .memory import ExperientialMemory, MemoryEngine
from .spiral import SpiralMiddleware, SpiralPhase
from .glyphs import GLYPHS, glyph_for

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
