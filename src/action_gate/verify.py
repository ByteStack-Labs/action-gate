"""Re-derive every published figure and exit non-zero if one fails to reproduce.

This is the file that makes the repository a receipt rather than a claim. It runs
the entire pipeline again from the committed fixtures, compares the result against
the committed receipt figure by figure, re-renders the human-readable receipt and
compares it byte for byte, and checks that every number published in the README is
a number the receipt actually contains.

It also enforces the project's core invariant in code: the reconciliation gate
approves zero dangerous actions. If that ever stops being true, this exits
non-zero and says so before anything else, because nothing else in the repository
matters if the verification floor has a hole in it.

Run: ``make verify`` or ``python -m action_gate.verify``. No arguments, no network,
no model, standard library only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from . import SEED
from .harness import (
    GATES,
    OUTCOMES,
    RECEIPTS,
    ROOT,
    load_world_and_decisions,
    render_markdown,
    run,
)

README = ROOT / "README.md"
SEED_FILE = ROOT / "SEED"
RECEIPT_JSON = RECEIPTS / "run.json"
RECEIPT_MD = RECEIPTS / "run.md"

GATE_NAMES = tuple(name for name, _ in GATES)
CONFUSION_COLUMNS = ("correct_approvals", "correct_refusals", "dangerous_approvals", "over_refusals")
RESULTS_HEADING = "## Results"
NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")
BACKTICKED = re.compile(r"`([^`]+)`")


class Failure:
    """A figure that did not reproduce."""

    def __init__(self, figure: str, expected: Any, actual: Any) -> None:
        self.figure = figure
        self.expected = expected
        self.actual = actual

    def __str__(self) -> str:
        return (
            f"{self.figure}\n"
            f"        committed:  {self.expected!r}\n"
            f"        re-derived: {self.actual!r}"
        )


def compare(committed: Any, rederived: Any, path: str, failures: list[Failure]) -> int:
    """Walk both structures in parallel, recording every leaf that disagrees.

    Walking rather than enumerating a list of known figures is deliberate. Any
    figure added to the receipt later is covered the moment it exists, with no
    chance of shipping unchecked because someone forgot to register it here.
    """
    if isinstance(committed, dict) and isinstance(rederived, dict):
        checked = 0
        for key in sorted(set(committed) | set(rederived)):
            child = f"{path}.{key}" if path else str(key)
            if key not in committed:
                failures.append(Failure(child, "absent from the committed receipt", rederived[key]))
            elif key not in rederived:
                failures.append(Failure(child, committed[key], "absent from the re-run"))
            else:
                checked += compare(committed[key], rederived[key], child, failures)
        return checked

    if isinstance(committed, list) and isinstance(rederived, list):
        if len(committed) != len(rederived):
            failures.append(Failure(f"{path}.length", len(committed), len(rederived)))
            return 1
        checked = 0
        for index, (left, right) in enumerate(zip(committed, rederived)):
            checked += compare(left, right, f"{path}[{index}]", failures)
        return checked

    if committed != rederived:
        failures.append(Failure(path, committed, rederived))
    return 1


def check_internal_consistency(result: dict, failures: list[Failure]) -> int:
    """Check the receipt against itself.

    Matching a committed file only proves the file has not changed. These checks
    prove the figures are coherent: that every confusion table accounts for every
    decision, and that no count is quietly orphaned.
    """
    composition = result["composition"]
    total = composition["total"]
    checked = 0

    checked += 1
    stated = composition["correct"] + composition["well_formed_wrong"]
    if stated != total:
        failures.append(Failure("composition: correct + well_formed_wrong == total", total, stated))

    checked += 1
    kinds_total = sum(composition["defect_kinds"].values())
    if kinds_total != composition["well_formed_wrong"]:
        failures.append(
            Failure(
                "composition: defect kinds sum to well_formed_wrong",
                composition["well_formed_wrong"],
                kinds_total,
            )
        )

    for name in GATE_NAMES:
        row = result["gates"][name]

        checked += 1
        outcomes_total = sum(row[outcome] for outcome in OUTCOMES)
        if outcomes_total != total:
            failures.append(Failure(f"gates.{name}: four outcomes sum to total decisions", total, outcomes_total))

        checked += 1
        seen_correct = row["correct_approvals"] + row["over_refusals"]
        if seen_correct != composition["correct"]:
            failures.append(
                Failure(
                    f"gates.{name}: correct approvals + over-refusals == correct decisions",
                    composition["correct"],
                    seen_correct,
                )
            )

        checked += 1
        seen_wrong = row["dangerous_approvals"] + row["correct_refusals"]
        if seen_wrong != composition["well_formed_wrong"]:
            failures.append(
                Failure(
                    f"gates.{name}: dangerous approvals + correct refusals == well-formed-wrong decisions",
                    composition["well_formed_wrong"],
                    seen_wrong,
                )
            )

        checked += 1
        by_kind = sum(row["dangerous_approvals_by_kind"].values())
        if by_kind != row["dangerous_approvals"]:
            failures.append(
                Failure(
                    f"gates.{name}: dangerous approvals by kind sum to the total",
                    row["dangerous_approvals"],
                    by_kind,
                )
            )

    return checked


def check_receipt_matches_fixtures(result: dict, decisions: Sequence, failures: list[Failure]) -> int:
    """The receipt's composition must describe the fixtures actually on disk."""
    composition = result["composition"]
    checked = 1
    if composition["total"] != len(decisions):
        failures.append(
            Failure("composition.total describes fixtures/decisions.json", composition["total"], len(decisions))
        )
    return checked


def check_seed(failures: list[Failure]) -> int:
    """The SEED file and the SEED constant must agree.

    Two copies of a number is one copy too many. If they diverge, every fixture
    downstream is generated from something other than what the repository says it
    was generated from.
    """
    if not SEED_FILE.exists():
        failures.append(Failure("SEED file", "present", "missing"))
        return 1
    on_disk = SEED_FILE.read_text(encoding="utf-8").strip()
    if on_disk != str(SEED):
        failures.append(Failure("SEED file agrees with action_gate.SEED", on_disk, str(SEED)))
    return 1


def check_markdown_receipt(result: dict, failures: list[Failure]) -> int:
    """Re-render run.md and compare byte for byte.

    Every figure in the readable receipt is rendered from the same result object,
    so a byte-identical re-render proves all of them reproduce.
    """
    if not RECEIPT_MD.exists():
        failures.append(Failure("receipts/run.md", "present", "missing"))
        return 1

    committed = RECEIPT_MD.read_text(encoding="utf-8")
    rederived = render_markdown(result)
    if committed != rederived:
        committed_lines = committed.splitlines()
        rederived_lines = rederived.splitlines()
        for index in range(max(len(committed_lines), len(rederived_lines))):
            left = committed_lines[index] if index < len(committed_lines) else "<end of file>"
            right = rederived_lines[index] if index < len(rederived_lines) else "<end of file>"
            if left != right:
                failures.append(Failure(f"receipts/run.md line {index + 1}", left, right))
                break
    return 1


def published_values(result: dict) -> set[str]:
    """Every numeric string the receipt can legitimately support."""
    values = {
        str(result["seed"]),
        str(result["confidence_threshold"]),
        f"{result['confidence_threshold']:.2f}",
        str(len(GATE_NAMES)),
    }
    composition = result["composition"]
    for key in ("total", "correct", "well_formed_wrong"):
        values.add(str(composition[key]))
    for count in composition["defect_kinds"].values():
        values.add(str(count))
    for name in GATE_NAMES:
        row = result["gates"][name]
        for outcome in OUTCOMES:
            values.add(str(row[outcome]))
        for count in row["dangerous_approvals_by_kind"].values():
            values.add(str(count))
    return values


def parse_results_table(text: str) -> list[list[str]]:
    """Pull the table rows out of the README's Results section."""
    rows: list[list[str]] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == RESULTS_HEADING
            continue
        if in_section and line.strip().startswith("|"):
            cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
            rows.append(cells)
    return rows


def check_readme(result: dict, failures: list[Failure]) -> int:
    """Every figure published in the README must come from the receipt.

    Two layers. The ``## Results`` table is compared cell by cell against the
    confusion tables. Then every backticked numeric token anywhere in the file
    must be a value the receipt actually contains, so a number typed in by hand
    fails the build instead of sitting in the prose looking credible.
    """
    if not README.exists():
        print("  README.md: not written yet, skipped")
        return 0

    text = README.read_text(encoding="utf-8")
    checked = 0

    rows = parse_results_table(text)
    gate_rows = {row[0]: row for row in rows if row and row[0] in GATE_NAMES}
    if not gate_rows:
        failures.append(
            Failure("README.md", f"a '{RESULTS_HEADING}' section with a table row per gate", "no such table")
        )
        return 1

    for name in GATE_NAMES:
        if name not in gate_rows:
            failures.append(Failure(f"README.md results table: row for {name}", "present", "missing"))
            continue
        expected = [str(result["gates"][name][column]) for column in CONFUSION_COLUMNS]
        stated = [cell for cell in gate_rows[name][1:] if NUMERIC.match(cell)]
        checked += 1
        if stated != expected:
            failures.append(Failure(f"README.md results table: {name}", expected, stated))

    allowed = published_values(result)
    for token in BACKTICKED.findall(text):
        candidate = token.strip()
        if not NUMERIC.match(candidate):
            continue
        checked += 1
        if candidate not in allowed:
            failures.append(
                Failure(f"README.md: published figure `{candidate}`", "a figure present in the receipt", candidate)
            )

    return checked


def main() -> int:
    print("action-gate verify")

    if not RECEIPT_JSON.exists():
        print(f"\nFAIL: receipts/run.json is missing. Run `make receipt` first.")
        return 1

    world, decisions = load_world_and_decisions()
    rederived = run(world, decisions)
    committed = json.loads(RECEIPT_JSON.read_text(encoding="utf-8"))

    print(
        f"  fixtures: {len(decisions)} decisions, {len(world.accounts)} accounts, "
        f"{len(world.instructions)} instructions"
    )

    failures: list[Failure] = []

    # The core invariant, checked before anything else and reported on its own. A
    # hole in the verification floor is not one failure among many.
    floor = rederived["gates"]["reconciliation_gate"]["dangerous_approvals"]
    if floor != 0:
        print(f"\nFAIL: the reconciliation gate approved {floor} dangerous action(s).")
        print("  The verification floor did not hold. Nothing else in this receipt matters.")
        return 1
    print("  invariant: reconciliation_gate approved 0 dangerous actions")

    checked = compare(committed, rederived, "", failures)
    print(f"  receipt: {checked} figures re-derived from the committed fixtures")

    structural = check_internal_consistency(rederived, failures)
    structural += check_receipt_matches_fixtures(rederived, decisions, failures)
    structural += check_seed(failures)
    print(f"  consistency: {structural} structural checks")

    check_markdown_receipt(rederived, failures)
    print("  receipts/run.md: re-rendered and compared byte for byte")

    published = check_readme(rederived, failures)
    if published:
        print(f"  README.md: {published} published figures checked against the receipt")

    if failures:
        print(f"\nFAIL: {len(failures)} figure(s) did not reproduce.\n")
        for failure in failures:
            print(f"    {failure}")
        print("")
        return 1

    print("\nPASS: every published figure re-derives from the committed fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
