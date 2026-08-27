"""The fixtures regenerate from SEED, and the figures regenerate from the fixtures.

``verify.py`` proves the published figures re-derive from the committed fixtures.
It cannot prove the committed fixtures are what SEED produces — it takes them as
given. That is the half these tests cover: the generator is re-run into a
throwaway directory and the output compared byte for byte against what is
committed, so the chain runs end to end.

    SEED -> fixtures -> receipt figures -> README

The generator is executed as a subprocess from a copy of the script placed in the
temporary tree. It resolves its output directory from its own location, so the
copy writes to the temporary tree and the committed fixtures are never touched —
a test that overwrote the artifact it was checking would prove nothing, which is
why one of the tests below asserts they are still byte-identical afterwards.

Standard library and pytest only. No model, no network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from action_gate import SEED
from action_gate.agent import load_decisions
from action_gate.domain import Ledger
from action_gate.harness import RECEIPTS, ROOT, render_markdown, run, write_receipt

FIXTURES = ROOT / "fixtures"
GENERATOR = ROOT / "scripts" / "generate_fixtures.py"
SEED_FILE = ROOT / "SEED"
RECEIPT_JSON = RECEIPTS / "run.json"
RECEIPT_MD = RECEIPTS / "run.md"

FIXTURE_FILES = ("source_of_truth.json", "decisions.json")

# Captured at import, before any generator runs, so an accidental write to the
# repository's own fixtures is detectable rather than invisible.
COMMITTED_STATE = {
    name: ((FIXTURES / name).read_bytes(), (FIXTURES / name).stat().st_mtime_ns)
    for name in FIXTURE_FILES
}


def regenerate(destination: Path) -> Path:
    """Run the committed generator into ``destination`` and return its fixtures dir."""
    workspace = destination / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    script = workspace / "scripts" / "generate_fixtures.py"
    shutil.copy2(GENERATOR, script)

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    produced = workspace / "fixtures"
    assert produced.resolve() != FIXTURES.resolve()
    return produced


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> Path:
    return regenerate(tmp_path_factory.mktemp("regen"))


# --- SEED -> fixtures ---------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_fixtures_regenerate_byte_identically_from_seed(regenerated, name):
    committed = (FIXTURES / name).read_bytes()
    rederived = (regenerated / name).read_bytes()
    assert rederived == committed, f"{name} does not reproduce from SEED {SEED}"


def test_regenerating_does_not_touch_the_committed_fixtures(regenerated):
    """The tests regenerate into a temporary tree; the repository is left alone."""
    for name in FIXTURE_FILES:
        content, mtime = COMMITTED_STATE[name]
        assert (FIXTURES / name).read_bytes() == content
        assert (FIXTURES / name).stat().st_mtime_ns == mtime, f"{name} was rewritten by the test run"


def test_two_regenerations_agree_with_each_other(tmp_path):
    first = regenerate(tmp_path / "first")
    second = regenerate(tmp_path / "second")
    for name in FIXTURE_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_seed_agrees_everywhere_it_is_written():
    """One seed, four copies. Any disagreement makes the provenance a claim, not a fact."""
    assert SEED_FILE.read_text(encoding="utf-8").strip() == str(SEED)
    assert json.loads((FIXTURES / "source_of_truth.json").read_text(encoding="utf-8"))["seed"] == SEED
    assert json.loads((FIXTURES / "decisions.json").read_text(encoding="utf-8"))["seed"] == SEED
    assert json.loads(RECEIPT_JSON.read_text(encoding="utf-8"))["seed"] == SEED


# --- fixtures -> figures ------------------------------------------------------


def test_harness_reproduces_the_committed_receipt():
    world = Ledger.load(FIXTURES / "source_of_truth.json")
    decisions = load_decisions(FIXTURES / "decisions.json")
    committed = json.loads(RECEIPT_JSON.read_text(encoding="utf-8"))
    assert run(world, decisions) == committed


def test_harness_is_deterministic_across_runs():
    world = Ledger.load(FIXTURES / "source_of_truth.json")
    decisions = load_decisions(FIXTURES / "decisions.json")
    assert run(world, decisions) == run(world, decisions)


def test_receipt_files_rewrite_byte_for_byte(tmp_path, monkeypatch):
    """The committed receipt is what ``make receipt`` writes, down to the bytes."""
    world = Ledger.load(FIXTURES / "source_of_truth.json")
    decisions = load_decisions(FIXTURES / "decisions.json")

    monkeypatch.setattr("action_gate.harness.RECEIPTS", tmp_path)
    write_receipt(run(world, decisions))

    assert (tmp_path / "run.json").read_bytes() == RECEIPT_JSON.read_bytes()
    assert (tmp_path / "run.md").read_bytes() == RECEIPT_MD.read_bytes()


def test_markdown_receipt_re_renders_from_the_committed_json():
    committed = json.loads(RECEIPT_JSON.read_text(encoding="utf-8"))
    assert render_markdown(committed) == RECEIPT_MD.read_text(encoding="utf-8")


# --- SEED -> figures: the loop closed -----------------------------------------


def test_committed_figures_derive_from_fixtures_regenerated_from_seed(regenerated):
    """End to end: nothing between the seed and the published figures is hand-held."""
    world = Ledger.load(regenerated / "source_of_truth.json")
    decisions = load_decisions(regenerated / "decisions.json")
    committed = json.loads(RECEIPT_JSON.read_text(encoding="utf-8"))

    rederived = run(world, decisions)
    assert rederived == committed
    assert rederived["gates"]["reconciliation_gate"]["dangerous_approvals"] == 0
