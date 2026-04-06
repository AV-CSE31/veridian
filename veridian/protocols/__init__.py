"""
veridian.protocols
──────────────────
Safety protocols for evolution control and inter-agent communication.

Public API::

    from veridian.protocols import EvolutionGate, EvolutionProposal, EvolutionOutcome
"""

from veridian.protocols.safe_evolution import EvolutionGate, EvolutionOutcome, EvolutionProposal

__all__ = ["EvolutionGate", "EvolutionOutcome", "EvolutionProposal"]
