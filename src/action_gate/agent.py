"""The agent side: committed decisions, loaded but never trusted.

There is no live model here. The decisions were generated once, deterministically,
and committed to ``fixtures/decisions.json``. This module only reads them.

Parsing is deliberately lenient. A missing or malformed field yields ``None``
rather than raising, because a gate's job is to handle a bad decision, not to be
spared one. A strict loader would refuse the malformed decision before any gate
saw it, and the gates would look more capable than they are.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .domain import LedgerError, parse_money

CORRECT = "correct"
WELL_FORMED_WRONG = "well_formed_wrong"


def _money_or_none(value: Any) -> int | None:
    try:
        return parse_money(value)
    except LedgerError:
        return None


@dataclass(frozen=True)
class Decision:
    """One agent decision, as committed.

    ``raw`` is retained so the judge gate can grade the decision as it was
    written, including fields the parser could not make sense of.
    """

    id: str
    label: str
    wrong_kind: str | None
    instruction_id: str | None
    source: str | None
    destination: str | None
    amount: int | None
    resulting_balances: Mapping[str, int] | None
    self_confidence: float | None
    raw: Mapping[str, Any]

    @property
    def is_correct(self) -> bool:
        return self.label == CORRECT

    @property
    def is_wrong(self) -> bool:
        return self.label == WELL_FORMED_WRONG

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Decision":
        if "id" not in raw or "label" not in raw:
            raise LedgerError(f"decision is missing its id or label: {raw!r}")
        if raw["label"] not in (CORRECT, WELL_FORMED_WRONG):
            raise LedgerError(f"decision {raw['id']} has unknown label {raw['label']!r}")

        action = raw.get("action")
        if not isinstance(action, Mapping):
            action = {}

        balances: dict[str, int] | None = None
        claimed = raw.get("resulting_balances")
        if isinstance(claimed, Mapping):
            parsed = {k: _money_or_none(v) for k, v in claimed.items()}
            if all(v is not None for v in parsed.values()):
                balances = {k: v for k, v in parsed.items() if v is not None}

        confidence = raw.get("self_confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None

        return cls(
            id=raw["id"],
            label=raw["label"],
            wrong_kind=raw.get("wrong_kind"),
            instruction_id=raw.get("instruction_id"),
            source=action.get("from"),
            destination=action.get("to"),
            amount=_money_or_none(action.get("amount")),
            resulting_balances=balances,
            self_confidence=float(confidence) if confidence is not None else None,
            raw=dict(raw),
        )


def load_decisions(path: str | Path) -> list[Decision]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return [Decision.from_dict(entry) for entry in payload["decisions"]]
