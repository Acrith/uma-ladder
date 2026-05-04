.PHONY: install test lint smoke tailwind tailwind-watch run

VENV ?= .venv
PY = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
PYTEST = $(VENV)/bin/pytest
RUFF = $(VENV)/bin/ruff
FLASK = $(VENV)/bin/flask

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
	npx --yes tailwindcss@3.4.13 \
		-c tailwind.config.js \
		-i ./uma_ladder/static/css/input.css \
		-o ./uma_ladder/static/css/output.css \
		--minify

tailwind-watch:
	npx --yes tailwindcss@3.4.13 \
		-c tailwind.config.js \
		-i ./uma_ladder/static/css/input.css \
		-o ./uma_ladder/static/css/output.css \
		--watch

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
