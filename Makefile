PYTHON ?= python3
export PYTHONPATH := src$(if $(PYTHONPATH),:$(PYTHONPATH))

.PHONY: all fixtures verify test receipt

# Regenerate fixtures, rebuild the receipt, re-derive every number, run the tests.
all: fixtures receipt verify test

# Deterministic, seeded. Overwrites fixtures/source_of_truth.json and fixtures/decisions.json.
fixtures:
	$(PYTHON) scripts/generate_fixtures.py

# Re-derives every published figure from committed fixtures. Exits non-zero on any mismatch.
verify:
	$(PYTHON) -m action_gate.verify

test:
	$(PYTHON) -m pytest

# Rewrites receipts/run.json and receipts/run.md from a fresh run.
receipt:
	$(PYTHON) -m action_gate.harness
