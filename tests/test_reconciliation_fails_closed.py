"""The reconciliation gate fails closed. This is the property the fixture exists for.

Two halves. First, the exhaustive one: every well-formed-wrong decision in the
committed fixtures is refused, one test per decision, so a regression names the
decision that got through rather than reporting a count that moved.

Second, the adversarial one: a *correct* decision is tampered with one field at a
time — an unknown instruction, an unknown account, an amount that is not a
movement, a payee the instruction never named, an outcome off by a single cent —
and the gate must refuse each one, naming the invariant that failed. A gate that
only refuses the defects someone thought to generate is not failing closed; it is
recognising a list. These cases are outside the list on purpose.

Closed is also the default here: malformed input the parser could not make sense
of must be refused, never waved through for want of something to check.
"""

from __future__ import annotations

from typing import Any

import pytest

from action_gate.agent import Decision
from action_gate.domain import Proposal, derive_expected_outcome, format_money
from action_gate.gates import reconciliation_gate
from action_gate.harness import load_world_and_decisions

WORLD, DECISIONS = load_world_and_decisions()
CORRECT = [d for d in DECISIONS if d.is_correct]
WRONG = [d for d in DECISIONS if d.is_wrong]
BASE = CORRECT[0]

DEFECT_KINDS = ("arithmetic_off_by", "overdraft_ignored", "stale_balance", "wrong_account")

DROP = object()


def variant(decision: Decision, **fields: Any) -> Decision:
    raw = dict(decision.raw)
    for name, value in fields.items():
        if value is DROP:
            raw.pop(name, None)
        else:
            raw[name] = value
    return Decision.from_dict(raw)


def with_action(decision: Decision, **fields: Any) -> Decision:
    return variant(decision, action={**decision.raw["action"], **fields})


def unrelated_account(decision: Decision) -> str:
    return next(a for a in sorted(WORLD.accounts) if a not in (decision.source, decision.destination))


def shifted_balances(decision: Decision, account: str, cents: int) -> dict[str, str]:
    """The decision's claimed balances with one account moved by ``cents``."""
    claimed = dict(decision.resulting_balances)
    claimed[account] += cents
    return {a: format_money(c) for a, c in sorted(claimed.items())}


# --- the exhaustive half: nothing well-formed-wrong is ever approved -----------


@pytest.mark.parametrize("decision", WRONG, ids=[d.id for d in WRONG])
def test_no_well_formed_wrong_decision_is_approved(decision):
    result = reconciliation_gate(decision, WORLD)
    assert result.approved is False, f"{decision.id} ({decision.wrong_kind}): {result.reason}"


def test_every_defect_kind_is_represented_in_what_was_refused():
    """Guards the test above from passing vacuously if a defect kind disappeared."""
    refused = {d.wrong_kind for d in WRONG if not reconciliation_gate(d, WORLD).approved}
    assert refused == set(DEFECT_KINDS)


def test_every_refusal_names_an_invariant():
    named = {
        "instruction_known",
        "accounts_exist",
        "amount_positive",
        "matches_instruction",
        "sufficient_funds",
        "balance_mismatch",
        "malformed_decision",
    }
    for decision in WRONG:
        reason = reconciliation_gate(decision, WORLD).reason
        assert reason.split(":")[0] in named, f"{decision.id}: {reason}"


def test_approval_is_exactly_agreement_with_the_independent_derivation():
    """No decision is approved for any reason other than reconciling with the ledger."""
    for decision in DECISIONS:
        derivation = derive_expected_outcome(
            WORLD,
            Proposal(
                instruction_id=decision.instruction_id,
                source=decision.source,
                destination=decision.destination,
                amount=decision.amount,
            ),
        )
        reconciles = derivation.valid and dict(derivation.expected_balances) == decision.resulting_balances
        assert reconciliation_gate(decision, WORLD).approved is reconciles, decision.id


# --- the adversarial half: one tampered field at a time -----------------------


@pytest.mark.parametrize(
    "case, invariant",
    [
        ("unknown_instruction", "instruction_known"),
        ("unknown_source_account", "accounts_exist"),
        ("unknown_destination_account", "accounts_exist"),
        ("amount_zero", "amount_positive"),
        ("amount_negative", "amount_positive"),
        ("self_transfer", "amount_positive"),
        ("wrong_payee", "matches_instruction"),
        ("amount_above_instruction", "matches_instruction"),
        ("amount_below_instruction", "matches_instruction"),
        ("balance_off_by_one_cent", "balance_mismatch"),
        ("balance_missing_an_account", "balance_mismatch"),
        ("balance_names_an_extra_account", "balance_mismatch"),
        ("no_instruction_id", "malformed_decision"),
        ("no_action", "malformed_decision"),
        ("amount_unparseable", "malformed_decision"),
        ("balances_unparseable", "malformed_decision"),
    ],
)
def test_a_correct_decision_is_refused_once_any_field_is_tampered_with(case, invariant):
    other = unrelated_account(BASE)
    instructed = WORLD.instructions[BASE.instruction_id].amount
    tampered = {
        "unknown_instruction": lambda: variant(BASE, instruction_id="INS-9999"),
        "unknown_source_account": lambda: with_action(BASE, **{"from": "ACC-0000"}),
        "unknown_destination_account": lambda: with_action(BASE, to="ACC-0000"),
        "amount_zero": lambda: with_action(BASE, amount="0.00"),
        "amount_negative": lambda: with_action(BASE, amount=format_money(-instructed)),
        "self_transfer": lambda: with_action(BASE, to=BASE.source),
        "wrong_payee": lambda: with_action(BASE, to=other),
        "amount_above_instruction": lambda: with_action(BASE, amount=format_money(instructed + 1)),
        "amount_below_instruction": lambda: with_action(BASE, amount=format_money(instructed - 1)),
        "balance_off_by_one_cent": lambda: variant(
            BASE, resulting_balances=shifted_balances(BASE, BASE.source, 1)
        ),
        "balance_missing_an_account": lambda: variant(
            BASE, resulting_balances={BASE.source: format_money(BASE.resulting_balances[BASE.source])}
        ),
        "balance_names_an_extra_account": lambda: variant(
            BASE,
            resulting_balances={
                **{a: format_money(c) for a, c in BASE.resulting_balances.items()},
                other: format_money(WORLD.balance(other)),
            },
        ),
        "no_instruction_id": lambda: variant(BASE, instruction_id=DROP),
        "no_action": lambda: variant(BASE, action=DROP),
        "amount_unparseable": lambda: with_action(BASE, amount="1,234.00"),
        "balances_unparseable": lambda: variant(
            BASE, resulting_balances={a: "unknown" for a in BASE.resulting_balances}
        ),
    }
    decision = tampered[case]()

    result = reconciliation_gate(decision, WORLD)
    assert not result.approved
    assert result.reason.startswith(f"{invariant}:"), result.reason


def test_the_untampered_decision_still_reconciles():
    """The control. Every refusal above is caused by the tampering, not by the base."""
    assert reconciliation_gate(BASE, WORLD).approved


def test_an_off_by_one_cent_claim_is_refused_in_both_directions():
    """Cents are exact. There is no tolerance band for a mismatch to hide in."""
    for cents in (1, -1):
        for account in sorted(BASE.resulting_balances):
            decision = variant(BASE, resulting_balances=shifted_balances(BASE, account, cents))
            result = reconciliation_gate(decision, WORLD)
            assert not result.approved
            assert result.reason.startswith("balance_mismatch:")


def test_a_refusal_reports_what_the_ledger_requires():
    """A refusal has to be actionable: it names the claim and the derived figure."""
    decision = variant(BASE, resulting_balances=shifted_balances(BASE, BASE.source, 5_00))
    reason = reconciliation_gate(decision, WORLD).reason
    expected = WORLD.balance(BASE.source) - BASE.amount
    assert BASE.source in reason
    assert format_money(expected) in reason


def test_confidence_cannot_buy_an_approval():
    """No self-report is admissible evidence. The ledger is the only witness."""
    for decision in WRONG:
        for confidence in (0.0, 0.5, 1.0):
            assert not reconciliation_gate(variant(decision, self_confidence=confidence), WORLD).approved
