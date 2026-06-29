#!/usr/bin/env bash
# Installs a daily cron job that runs `metis` at 10am.
# Run once after setup: bash setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
METIS="$SCRIPT_DIR/venv/bin/metis"
LOG="$HOME/.job_pipeline/logs/cron.log"

if [ ! -f "$METIS" ]; then
  echo "Error: metis not found at $METIS"
  echo "Run: pip install -e . (from the repo root, inside your venv)"
  exit 1
fi

CRON_LINE="0 10 * * * cd \"$SCRIPT_DIR\" && \"$METIS\" >> \"$LOG\" 2>&1"

(crontab -l 2>/dev/null | grep -qF "metis") && {
  echo "Cron job already installed."
  exit 0
}

(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
echo "Cron job installed: metis runs daily at 10am."
echo "Logs: $LOG"
