# Backward compatibility shim — existing cron jobs continue to work.
# Preferred: use the `scorerole` command directly (installed by pyproject.toml entry point).
#   scorerole                      # fetch last 3 days, skip seen roles
#   scorerole --lookback 7d        # fetch last 7 days
#   scorerole --lookback 2026-05-10  # fetch from specific date
#   scorerole reset                # clear state (with confirmation)
#   scorerole reset --force        # clear state, no prompt
#   scorerole debug                # dump latest email to ~/.job_pipeline/debug_email.txt
from scorerole.pipeline import main
if __name__ == "__main__":
    main()
