"""The source-of-truth world and the irreversible action.

Money is integer cents everywhere inside this module. Cents are exact; binary
floats are not, and a reconciliation gate that compares balances cannot afford a
rounding artifact masquerading as a mismatch. Money crosses the JSON boundary as
a two-decimal string so the serialized form is exact as well.

The load-bearing function here is :func:`derive_expected_outcome`. It recomputes,
from the ledger alone, whether a proposed transfer is valid and what the resulting
balances must be. It never reads the agent's arithmetic. That independence is the
entire source of the reconciliation gate's power: the gate checks a signal the
agent does not own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

MONEY_PATTERN = re.compile(r"^-?\d+\.\d{2}$")


class LedgerError(RuntimeError):
    """Raised when the committed world state is malformed."""


def parse_money(text: str) -> int:
    """Parse a two-decimal money string into integer cents.

    Strict by design: anything that is not exactly two decimal places is a
    malformed world, not a value to be rounded into shape.
    """
    if not isinstance(text, str) or not MONEY_PATTERN.match(text):
        raise LedgerError(f"malformed money value: {text!r}")
    negative = text.startswith("-")
    units, _, hundredths = text.lstrip("-").partition(".")
    cents = int(units) * 100 + int(hundredths)
    return -cents if negative else cents


def format_money(cents: int) -> str:
    """Render integer cents as a two-decimal string."""
    sign = "-" if cents < 0 else ""
    magnitude = abs(cents)
    return f"{sign}{magnitude // 100}.{magnitude % 100:02d}"


@dataclass(frozen=True)
class Account:
    id: str
    holder: str
    opening_balance: int


@dataclass(frozen=True)
class Adjustment:
    """A posted movement against one account, ordered by ``seq``.

    The adjustment log is what makes "stale" meaningful: a balance read before
    the most recent adjustment is well-formed and out of date.
    """

    seq: int
    account: str
    delta: int
    memo: str


@dataclass(frozen=True)
class Instruction:
    """A pending payment instruction: the intent the action is meant to carry out.

    Balances alone cannot tell you that a transfer went to the wrong account — a
    payment to a real account the payer can afford is arithmetically fine. The
    instruction is the independent record of what was actually asked for.
    """

    id: str
    source: str
    destination: str
    amount: int
    memo: str


@dataclass(frozen=True)
class Proposal:
    """A transfer an agent proposes to execute, stripped of the agent's reasoning."""

    instruction_id: str
    source: str
    destination: str
    amount: int


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Derivation:
    """The independent re-derivation of what a proposal must do.

    ``expected_balances`` is populated only when the proposal is valid; an invalid
    proposal has no legitimate outcome to compare against.
    """

    valid: bool
    reason: str
    expected_balances: Mapping[str, int]
    checks: tuple[Check, ...]

    def failed_check(self) -> Check | None:
        for check in self.checks:
            if not check.passed:
                return check
        return None


@dataclass(frozen=True)
class Ledger:
    """The committed source of truth."""

    currency: str
    accounts: Mapping[str, Account]
    adjustments: tuple[Adjustment, ...]
    instructions: Mapping[str, Instruction]

    @classmethod
    def from_dict(cls, raw: Mapping) -> "Ledger":
        try:
            accounts = {
                entry["id"]: Account(
                    id=entry["id"],
                    holder=entry["holder"],
                    opening_balance=parse_money(entry["opening_balance"]),
                )
                for entry in raw["accounts"]
            }
            adjustments = tuple(
                Adjustment(
                    seq=entry["seq"],
                    account=entry["account"],
                    delta=parse_money(entry["delta"]),
                    memo=entry["memo"],
                )
                for entry in sorted(raw["adjustments"], key=lambda e: e["seq"])
            )
            instructions = {
                entry["id"]: Instruction(
                    id=entry["id"],
                    source=entry["from"],
                    destination=entry["to"],
                    amount=parse_money(entry["amount"]),
                    memo=entry["memo"],
                )
                for entry in raw["instructions"]
            }
        except (KeyError, TypeError) as exc:
            raise LedgerError(f"malformed source of truth: {exc}") from exc

        for adjustment in adjustments:
            if adjustment.account not in accounts:
                raise LedgerError(f"adjustment {adjustment.seq} names unknown account")
        return cls(
            currency=raw.get("currency", "USD"),
            accounts=accounts,
            adjustments=adjustments,
            instructions=instructions,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Ledger":
        with open(path, encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def balance(self, account_id: str) -> int:
        """Current balance: opening plus every posted adjustment, in cents."""
        if account_id not in self.accounts:
            raise LedgerError(f"unknown account: {account_id}")
        total = self.accounts[account_id].opening_balance
        for adjustment in self.adjustments:
            if adjustment.account == account_id:
                total += adjustment.delta
        return total

    def balances(self) -> dict[str, int]:
        return {account_id: self.balance(account_id) for account_id in sorted(self.accounts)}

    def balances_excluding(self, seqs: Iterable[int]) -> dict[str, int]:
        """Balances as they stood before the given adjustments posted.

        Used to construct and to explain stale reads. The reconciliation gate
        never calls this: it reconciles against current balances only.
        """
        excluded = set(seqs)
        totals = {
            account_id: self.accounts[account_id].opening_balance
            for account_id in sorted(self.accounts)
        }
        for adjustment in self.adjustments:
            if adjustment.seq not in excluded:
                totals[adjustment.account] += adjustment.delta
        return totals

    def latest_adjustment_touching(self, account_ids: Iterable[str]) -> Adjustment | None:
        wanted = set(account_ids)
        touching = [a for a in self.adjustments if a.account in wanted]
        return max(touching, key=lambda a: a.seq) if touching else None

    def open_state(self) -> "LedgerState":
        return LedgerState(dict(self.balances()))


class LedgerState:
    """Mutable balances. Applying a transfer here is the point of no return.

    There is deliberately no inverse operation. This class models the moment the
    action leaves the system and reaches the world, which is precisely the moment
    a gate is no longer able to help.
    """

    def __init__(self, balances: dict[str, int]) -> None:
        self._balances = dict(balances)
        self._applied: list[Proposal] = []

    @property
    def balances(self) -> dict[str, int]:
        return dict(self._balances)

    @property
    def applied(self) -> tuple[Proposal, ...]:
        return tuple(self._applied)

    def apply_transfer(self, source: str, destination: str, amount: int) -> None:
        """Move money. Performs no validation — validation is the gate's job.

        This is intentionally blunt. An unguarded irreversible action will happily
        overdraw an account or pay the wrong party; that is why the gate has to
        run before it, not inside it.
        """
        for account_id in (source, destination):
            if account_id not in self._balances:
                raise LedgerError(f"unknown account: {account_id}")
        self._balances[source] -= amount
        self._balances[destination] += amount
        self._applied.append(
            Proposal(
                instruction_id="",
                source=source,
                destination=destination,
                amount=amount,
            )
        )


def derive_expected_outcome(ledger: Ledger, proposal: Proposal) -> Derivation:
    """Recompute, from the ledger alone, whether a proposal is valid and its outcome.

    This function is the independent code path. It reads the source of truth and
    the proposed action, and nothing else — never the agent's claimed balances,
    never the agent's confidence. Every check it runs is named, so a refusal can
    say which invariant failed rather than only that something did.
    """
    checks: list[Check] = []

    instruction = ledger.instructions.get(proposal.instruction_id)
    checks.append(
        Check(
            name="instruction_known",
            passed=instruction is not None,
            detail=(
                f"instruction {proposal.instruction_id} is on file"
                if instruction is not None
                else f"no instruction on file with id {proposal.instruction_id}"
            ),
        )
    )
    if instruction is None:
        return Derivation(False, "instruction_known", {}, tuple(checks))

    missing = [a for a in (proposal.source, proposal.destination) if a not in ledger.accounts]
    checks.append(
        Check(
            name="accounts_exist",
            passed=not missing,
            detail="both accounts exist" if not missing else f"unknown account(s): {', '.join(missing)}",
        )
    )
    if missing:
        return Derivation(False, "accounts_exist", {}, tuple(checks))

    positive = proposal.amount > 0 and proposal.source != proposal.destination
    checks.append(
        Check(
            name="amount_positive",
            passed=positive,
            detail=(
                f"amount {format_money(proposal.amount)} moves between distinct accounts"
                if positive
                else f"amount {format_money(proposal.amount)} is not a movement between distinct accounts"
            ),
        )
    )
    if not positive:
        return Derivation(False, "amount_positive", {}, tuple(checks))

    divergences = []
    if proposal.source != instruction.source:
        divergences.append(f"from {proposal.source} != instructed {instruction.source}")
    if proposal.destination != instruction.destination:
        divergences.append(f"to {proposal.destination} != instructed {instruction.destination}")
    if proposal.amount != instruction.amount:
        divergences.append(
            f"amount {format_money(proposal.amount)} != instructed {format_money(instruction.amount)}"
        )
    checks.append(
        Check(
            name="matches_instruction",
            passed=not divergences,
            detail=(
                f"action carries out instruction {instruction.id}"
                if not divergences
                else "; ".join(divergences)
            ),
        )
    )
    if divergences:
        return Derivation(False, "matches_instruction", {}, tuple(checks))

    available = ledger.balance(proposal.source)
    funded = available >= proposal.amount
    checks.append(
        Check(
            name="sufficient_funds",
            passed=funded,
            detail=(
                f"{proposal.source} holds {format_money(available)}, "
                f"{'covers' if funded else 'short of'} {format_money(proposal.amount)}"
            ),
        )
    )
    if not funded:
        return Derivation(False, "sufficient_funds", {}, tuple(checks))

    expected = {
        proposal.source: available - proposal.amount,
        proposal.destination: ledger.balance(proposal.destination) + proposal.amount,
    }
    return Derivation(True, "", expected, tuple(checks))
