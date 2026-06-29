"""Root conftest — guards against running tests outside the project venv.

If core runtime deps (rich, InquirerPy, anthropic, …) are not importable,
every test that touches metis.* would crash at collection time instead of
producing a useful error.  This hook catches that early and tells the runner
exactly what to do rather than emitting 300+ confusing tracebacks.
"""
import sys

_REQUIRED = ["rich", "anthropic", "yaml", "InquirerPy", "dateparser"]
import importlib.util as _ilu
_missing = [pkg for pkg in _REQUIRED if _ilu.find_spec(pkg) is None]

if _missing:
    print(
        f"\n[conftest] Missing packages: {', '.join(_missing)}\n"
        f"           Tests must run inside the project venv:\n"
        f"           source venv/bin/activate && pytest tests/ -q\n"
        f"           or: make test\n",
        file=sys.stderr,
    )
    # Exit cleanly so CI sees a clear failure message, not 300 collection errors.
    sys.exit(1)
