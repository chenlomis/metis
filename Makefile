.PHONY: test test-fast test-full test-e2e lint

# Prefer the contributor venv from README, then the legacy local venv.
# Fall back to the active Python so CI and already-activated shells still work.
PYTEST := $(shell if [ -x ./.venv/bin/pytest ]; then echo ./.venv/bin/pytest; elif [ -x ./venv/bin/pytest ]; then echo ./venv/bin/pytest; else echo python -m pytest; fi)

test: test-full

test-fast:
	$(PYTEST) tests/test_core.py tests/test_schedule.py -q

test-full:
	$(PYTEST) tests/ -q

test-e2e:
	$(PYTEST) tests/test_e2e_personas.py -q

lint:
	python -m compileall -q metis tests
