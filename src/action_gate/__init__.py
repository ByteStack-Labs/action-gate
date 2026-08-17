"""A verification floor between an agent's decision and an irreversible action.

Three gates sit between a proposed action and the world:

- ``confidence_gate`` approves what the agent is sure of.
- ``judge_gate`` approves what looks plausible.
- ``reconciliation_gate`` re-derives the fact from the source of truth and fails
  closed when the decision does not reconcile.

The first two approve well-formed-wrong decisions. The third does not. Standard
library only: no model, no network, no GPU.
"""

__version__ = "0.1.0"

SEED = 20260814
"""Single source of randomness for every fixture in this repository."""
