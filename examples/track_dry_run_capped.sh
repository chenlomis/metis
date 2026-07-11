#!/usr/bin/env bash
set -euo pipefail

LOOKBACK="${1:-7d}"
MAX_EMAILS="${METIS_TRACK_MAX_EMAILS:-25}"

export METIS_TRACK_MAX_EMAILS="$MAX_EMAILS"

echo "Running metis track --lookback $LOOKBACK --dry-run"
echo "Candidate email cap: $METIS_TRACK_MAX_EMAILS"
echo

metis track --lookback "$LOOKBACK" --dry-run
