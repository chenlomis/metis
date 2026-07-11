.PHONY: test test-fast test-full test-e2e coverage lint

# Prefer the contributor venv from README, then the legacy local venv.
# Fall back to the active Python so CI and already-activated shells still work.
PYTHON := $(shell if [ -x ./.venv/bin/python ]; then echo ./.venv/bin/python; elif [ -x ./venv/bin/python ]; then echo ./venv/bin/python; else echo python; fi)
PYTEST := $(shell if [ -x ./.venv/bin/pytest ]; then echo ./.venv/bin/pytest; elif [ -x ./venv/bin/pytest ]; then echo ./venv/bin/pytest; else echo python -m pytest; fi)

test: test-full

test-fast:
	$(PYTEST) tests/test_core.py tests/test_schedule.py -q

test-full:
	$(PYTEST) tests/ -q

test-e2e:
	$(PYTEST) tests/test_e2e_personas.py -q

coverage:
	$(PYTEST) tests/ --cov=metis --cov-report=term-missing --cov-report=xml

lint:
	$(PYTHON) -m ruff check metis tests
	$(PYTHON) -m compileall -q metis tests
