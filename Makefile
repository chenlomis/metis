.PHONY: test test-fast test-full

# Always run inside the project venv so rich/InquirerPy/anthropic are available.
PYTEST := ./venv/bin/pytest

test: test-full

test-fast:
	$(PYTEST) tests/test_core.py tests/test_schedule.py -q

test-full:
	$(PYTEST) tests/ -q
