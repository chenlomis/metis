#!/usr/bin/env bash
# Installs a daily cron job that runs `scorerole` at 10am.
# Run once after setup: bash setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCOREROLE="$SCRIPT_DIR/venv/bin/scorerole"
LOG="$HOME/.job_pipeline/logs/cron.log"

if [ ! -f "$SCOREROLE" ]; then
  echo "Error: scorerole not found at $SCOREROLE"
  echo "Run: pip install -e . (from the repo root, inside your venv)"
  exit 1
fi

CRON_LINE="0 10 * * * cd \"$SCRIPT_DIR\" && \"$SCOREROLE\" >> \"$LOG\" 2>&1"

(crontab -l 2>/dev/null | grep -qF "scorerole") && {
  echo "Cron job already installed."
  exit 0
}

(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
echo "Cron job installed: scorerole runs daily at 10am."
echo "Logs: $LOG"
