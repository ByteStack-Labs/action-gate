# Receipt

Seed `20260814`. Confidence threshold `0.80`. No model, no network, no GPU.

Regenerate with `make receipt`; re-derive every figure below with `make verify`. This file carries no timestamp — it is a function of the committed fixtures and the committed code, so two runs produce identical bytes.

## Fixture composition

| | count |
|---|---|
| decisions | 60 |
| correct | 30 |
| well-formed-wrong | 30 |
| — `arithmetic_off_by` | 8 |
| — `overdraft_ignored` | 8 |
| — `stale_balance` | 7 |
| — `wrong_account` | 7 |

## Dangerous approvals

A dangerous approval is a well-formed-wrong decision that the gate approved: an action the source of truth contradicts, cleared to proceed.

| gate | dangerous approvals | of well-formed-wrong |
|---|---|---|
| `confidence_gate` | 30 | 30 |
| `judge_gate` | 25 | 30 |
| `reconciliation_gate` | 0 | 30 |

## Confusion tables

| gate | correct approvals | correct refusals | dangerous approvals | over-refusals |
|---|---|---|---|---|
| `confidence_gate` | 25 | 0 | 30 | 5 |
| `judge_gate` | 30 | 5 | 25 | 0 |
| `reconciliation_gate` | 30 | 30 | 0 | 0 |

## Dangerous approvals by defect kind

| gate | `arithmetic_off_by` | `overdraft_ignored` | `stale_balance` | `wrong_account` |
|---|---|---|---|---|
| `confidence_gate` | 8 | 8 | 7 | 7 |
| `judge_gate` | 8 | 3 | 7 | 7 |
| `reconciliation_gate` | 0 | 0 | 0 | 0 |

Precision is the authority.
