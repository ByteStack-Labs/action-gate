# Prefer the project virtualenv when one is present, so `make test` works on a
# fresh clone without activating it first. Override with `make PYTHON=...`.
VENV_PYTHON := .venv/bin/python
PYTHON ?= $(shell test -x $(VENV_PYTHON) && echo $(VENV_PYTHON) || echo python3)
export PYTHONPATH := src$(if $(PYTHONPATH),:$(PYTHONPATH))

.PHONY: all venv fixtures verify test receipt

# Regenerate fixtures, rebuild the receipt, re-derive every number, run the tests.
all: fixtures receipt verify test

# Deterministic, seeded. Overwrites fixtures/source_of_truth.json and fixtures/decisions.json.
fixtures:
	$(PYTHON) scripts/generate_fixtures.py

# Re-derives every published figure from committed fixtures. Exits non-zero on any mismatch.
verify:
	$(PYTHON) -m action_gate.verify

# Isolated dev environment: pytest is the only dependency, and it stays out of
# the system interpreter. The core library itself needs nothing but the stdlib.
venv:
	uv venv
	uv pip install -e ".[dev]"

# pytest only; no model, no network.
test:
	$(PYTHON) -m pytest

# Rewrites receipts/run.json and receipts/run.md from a fresh run.
receipt:
	$(PYTHON) -m action_gate.harness
