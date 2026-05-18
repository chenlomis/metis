#!/usr/bin/env bash
# Adds a daily 10am cron job.
# Run once: bash setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python3"
LOG="$HOME/.job_pipeline/logs/cron.log"

CRON_LINE="0 10 * * * cd \"$SCRIPT_DIR\" && \"$PYTHON\" job_alert_pipeline.py >> \"$LOG\" 2>&1"

# Add only if not already present
(crontab -l 2>/dev/null | grep -qF "job_alert_pipeline.py") && {
  echo "Cron job already installed."
  exit 0
}

(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
echo "Cron job installed: runs daily at 10am."
echo "Logs: $LOG"
