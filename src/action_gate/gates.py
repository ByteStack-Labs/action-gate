"""The three gates that can stand between a decision and an irreversible action.

All three share one signature — ``gate(decision, world) -> GateResult`` — so the
harness can run them side by side on identical input. What separates them is not
their interface but what they are willing to look at:

- ``confidence_gate`` looks at the agent's self-report and nothing else.
- ``judge_gate`` looks at the decision's surface and nothing else.
- ``reconciliation_gate`` looks at the source of truth and re-derives the fact.

The first two consult signals the agent itself produced. The third consults one it
does not own. That is the whole distinction the fixture exists to demonstrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .agent import Decision
from .domain import Ledger, Proposal, derive_expected_outcome, format_money

DEFAULT_CONFIDENCE_THRESHOLD = 0.8


@dataclass(frozen=True)
class GateResult:
    approved: bool
    reason: str


def confidence_gate(
    decision: Decision,
    world: Ledger,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> GateResult:
    """Approve if and only if self-reported confidence clears the threshold.

    ``world`` is accepted for signature symmetry and deliberately never read. This
    gate cannot be wrong about the world because it never consults it; it can only
    be wrong about the agent, which is the failure mode that matters. A confident
    wrong decision passes here by construction.
    """
    confidence = decision.self_confidence
    if confidence is None:
        return GateResult(False, "no self-reported confidence")
    if confidence >= threshold:
        return GateResult(True, f"self-confidence {confidence:.2f} >= threshold {threshold:.2f}")
    return GateResult(False, f"self-confidence {confidence:.2f} < threshold {threshold:.2f}")


def judge_gate(decision: Decision, world: Ledger) -> GateResult:
    """Approve if the decision is plausible on its face.

    This is a deterministic stand-in for "a second model rates the decision",
    chosen so the fixture needs no model, no network, and no API key, and so its
    result is identical on every machine and every run. It grades appearance: are
    the fields there, is the amount a real movement, do the reported balances have
    the right shape and sign.

    It does not re-derive anything from the source of truth. ``world`` is accepted
    for signature symmetry and deliberately never read. That restraint is the
    point: a judge grades what it is shown, and a well-formed-wrong decision shows
    it nothing to object to.

    See the honesty section of the README — this proxy stands in for a model judge
    and its catch rate must not be read as a measurement of one.
    """
    raw: Mapping[str, Any] = decision.raw

    required = ("id", "instruction_id", "action", "resulting_balances", "self_confidence")
    missing = [field for field in required if field not in raw]
    if missing:
        return GateResult(False, f"missing field(s): {', '.join(missing)}")

    action = raw.get("action")
    if not isinstance(action, Mapping) or not all(k in action for k in ("from", "to", "amount")):
        return GateResult(False, "action is not a well-formed transfer")

    if decision.self_confidence is None or not 0.0 <= decision.self_confidence <= 1.0:
        return GateResult(False, "self-confidence is not a probability")

    if decision.amount is None:
        return GateResult(False, "amount is not a well-formed money value")
    if decision.amount <= 0:
        return GateResult(False, f"amount {format_money(decision.amount)} is not positive")

    if decision.source is None or decision.destination is None:
        return GateResult(False, "action does not name both accounts")
    if decision.source == decision.destination:
        return GateResult(False, "action moves money between one account and itself")

    balances = decision.resulting_balances
    if balances is None:
        return GateResult(False, "resulting balances are not well-formed money values")
    if set(balances) != {decision.source, decision.destination}:
        return GateResult(False, "resulting balances do not cover exactly the accounts in the action")

    overdrawn = sorted(account for account, cents in balances.items() if cents < 0)
    if overdrawn:
        return GateResult(False, f"resulting balance is negative for {', '.join(overdrawn)}")

    return GateResult(True, "surface-plausible: fields present, amount positive, balances well-formed")


def reconciliation_gate(decision: Decision, world: Ledger) -> GateResult:
    """Re-derive the fact from the source of truth and fail closed on mismatch.

    The derivation below is computed from ``world`` and the proposed action alone.
    The agent's own ``resulting_balances`` are read at exactly one point — the final
    equality check — and are never an input to the expected figures. If this gate
    reused the agent's arithmetic it would be checking the agent against itself,
    and a confidently wrong decision would reconcile with its own error.

    Every refusal names the invariant that failed: one of ``instruction_known``,
    ``accounts_exist``, ``amount_positive``, ``matches_instruction``,
    ``sufficient_funds``, or ``balance_mismatch`` when the action is legitimate but
    the claimed outcome is not what the ledger says it must be.
    """
    if decision.instruction_id is None or decision.source is None or decision.destination is None:
        return GateResult(False, "malformed_decision: action does not name an instruction and two accounts")
    if decision.amount is None:
        return GateResult(False, "malformed_decision: amount is not a well-formed money value")

    proposal = Proposal(
        instruction_id=decision.instruction_id,
        source=decision.source,
        destination=decision.destination,
        amount=decision.amount,
    )
    derivation = derive_expected_outcome(world, proposal)

    if not derivation.valid:
        failed = derivation.failed_check()
        detail = failed.detail if failed is not None else "invariant failed"
        return GateResult(False, f"{derivation.reason}: {detail}")

    claimed = decision.resulting_balances
    if claimed is None:
        return GateResult(False, "malformed_decision: resulting balances are not well-formed money values")

    if claimed != dict(derivation.expected_balances):
        divergences = []
        for account in sorted(set(claimed) | set(derivation.expected_balances)):
            expected = derivation.expected_balances.get(account)
            stated = claimed.get(account)
            if expected != stated:
                divergences.append(
                    f"{account} claimed {format_money(stated) if stated is not None else 'nothing'}, "
                    f"ledger requires {format_money(expected) if expected is not None else 'nothing'}"
                )
        return GateResult(False, f"balance_mismatch: {'; '.join(divergences)}")

    return GateResult(True, "reconciles with the source of truth")
