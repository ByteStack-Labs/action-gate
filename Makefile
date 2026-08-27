# Prefer the project virtualenv when one is present, so `make test` works on a
# fresh clone without activating it first. Override with `make PYTHON=...`.
VENV_PYTHON := .venv/bin/python
PYTHON ?= $(shell test -x $(VENV_PYTHON) && echo $(VENV_PYTHON) || echo python3)
export PYTHONPATH := src$(if $(PYTHONPATH),:$(PYTHONPATH))

.PHONY: all venv fixtures verify test receipt rasters

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

# Rasterizes docs/assets/action-gate-hero.svg into the two house-dimension assets:
#
#   docs/assets/action-gate-hero.png     1456x816   README embed and profile
#   docs/assets/action-gate-social.png   1280x640   social, OpenGraph, GitHub social preview
#
# Both are build artifacts of the SVG and nothing else. Neither target size shares the
# artwork's 1200x630 aspect, so each is rendered at whichever dimension fits — width for the
# hero, height for the social card — and then centered on the exact canvas and padded with
# the SVG's own background color (11, 18, 32 = 0b1220), which makes the padding invisible.
# Passing cairosvg both dimensions at once would stretch the artwork instead of fitting it;
# that is why only one is given. If the SVG's viewBox ever changes, revisit which dimension
# fits. Deliberately not part of `all`: this is the one target that fetches a renderer, and
# the library, the tests, and the verifier never need it.
rasters:
	uv run --no-project --with "cairosvg==2.9.0" --with "pillow" python -c 'import io, cairosvg; from PIL import Image; art = Image.open(io.BytesIO(cairosvg.svg2png(url="docs/assets/action-gate-hero.svg", output_width=1456))).convert("RGB"); canvas = Image.new("RGB", (1456, 816), (11, 18, 32)); canvas.paste(art, ((1456 - art.width) // 2, (816 - art.height) // 2)); canvas.save("docs/assets/action-gate-hero.png", optimize=True)'
	uv run --no-project --with "cairosvg==2.9.0" --with "pillow" python -c 'import io, cairosvg; from PIL import Image; art = Image.open(io.BytesIO(cairosvg.svg2png(url="docs/assets/action-gate-hero.svg", output_height=640))).convert("RGB"); canvas = Image.new("RGB", (1280, 640), (11, 18, 32)); canvas.paste(art, ((1280 - art.width) // 2, (640 - art.height) // 2)); canvas.save("docs/assets/action-gate-social.png", optimize=True)'
