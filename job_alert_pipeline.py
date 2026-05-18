# Backward compatibility shim — existing cron jobs continue to work.
# New usage: scorerole / scorerole --debug / scorerole --reset / scorerole --since 7d
from scorerole.pipeline import main
if __name__ == "__main__":
    main()
