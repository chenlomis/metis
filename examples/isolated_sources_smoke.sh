#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${METIS_SMOKE_DIR:-/tmp/metis-sources-smoke}"
PROFILE="$DATA_DIR/profile.yaml"
METIS_BIN="${METIS_BIN:-$ROOT/venv/bin/metis}"

mkdir -p "$DATA_DIR"
cat > "$PROFILE" <<'YAML'
candidate:
  name: Smoke Tester
  location: Remote
  location_preference: remote
target:
  level: Staff
  roles:
    - Product Manager
scoring:
  solid_match_threshold: 75
  moderate_match_threshold: 55
proactive_sources:
  enabled: false
  companies: []
YAML
cat > "$DATA_DIR/.env" <<'ENV'
METIS_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-smoke
GMAIL_ADDRESS=smoke@example.com
GMAIL_APP_PASSWORD=smoke-password
RECIPIENT_EMAIL=smoke@example.com
MAX_JOBS_PER_RUN=10
ENV
chmod 600 "$DATA_DIR/.env"

export METIS_DATA_DIR="$DATA_DIR"
export METIS_PROFILE="$PROFILE"

"$METIS_BIN" sources list
"$METIS_BIN" sources on
"$METIS_BIN" sources add Stripe
"$METIS_BIN" sources list
"$METIS_BIN" sources off

echo
echo "Smoke profile: $PROFILE"
