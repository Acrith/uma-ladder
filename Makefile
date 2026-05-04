.PHONY: install test lint smoke tailwind tailwind-watch run

VENV ?= .venv
PY = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
PYTEST = $(VENV)/bin/pytest
RUFF = $(VENV)/bin/ruff
FLASK = $(VENV)/bin/flask

# Use bash so the nvm-aware tailwind target works.
SHELL := /bin/bash

# Wrap npx so nvm-installed Node is reachable from `make` (which doesn't run
# an interactive shell). Override TAILWIND_NPX if you have npx on PATH already.
TAILWIND_NPX ?= bash -c 'export NVM_DIR="$$HOME/.nvm"; [ -s "$$NVM_DIR/nvm.sh" ] && . "$$NVM_DIR/nvm.sh"; npx --yes tailwindcss@3.4.13 "$$@"' bash

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

test:
	$(PYTEST)

lint:
	$(RUFF) check .

# Build Tailwind CSS using a one-shot npx (no node_modules persisted).
tailwind:
	$(TAILWIND_NPX) -c tailwind.config.js -i ./uma_ladder/static/css/input.css -o ./uma_ladder/static/css/output.css --minify

tailwind-watch:
	$(TAILWIND_NPX) -c tailwind.config.js -i ./uma_ladder/static/css/input.css -o ./uma_ladder/static/css/output.css --watch

run:
	FLASK_APP=uma_ladder FLASK_CONFIG=development $(FLASK) run

# End-to-end smoke: fresh DB, migrate, seed, ensure routes respond, run tests.
smoke:
	@rm -f instance/uma_ladder.dev.sqlite
	FLASK_APP=uma_ladder FLASK_CONFIG=development $(FLASK) db upgrade
	FLASK_APP=uma_ladder FLASK_CONFIG=development $(FLASK) uma seed-characters
	FLASK_APP=uma_ladder FLASK_CONFIG=development $(FLASK) uma seed-presets
	FLASK_APP=uma_ladder FLASK_CONFIG=development $(FLASK) uma import-g1-races
	$(PYTEST) -q
	$(RUFF) check .
