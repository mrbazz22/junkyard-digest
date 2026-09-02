#!/bin/bash
# Junkyard digest cron entrypoint
# Used by cron "cagles-weekly-new-arrivals" (cf1e3794-2db2-412c-a1c1-2881281f191f)
# Runs the full pipeline: scrape → research → transform → digest → push
#
# No LLM. Pure deterministic shell. Failure → non-zero exit → cron failure alert.

set -e

REPO="/Users/administrator/.openclaw/workspace/junkyard-digest"
EBAY_CRED="$HOME/.openclaw/ebay_credentials.json"

cd "$REPO"

# Load eBay client secret from JSON credentials file
export EBAY_CLIENT_SECRET="$(python3 -c "import json; print(json.load(open('$EBAY_CRED'))['production']['cert_id'])")"

if [ -z "$EBAY_CLIENT_SECRET" ]; then
  echo "❌ EBAY_CLIENT_SECRET not set (check $EBAY_CRED)"
  exit 2
fi

echo "🚗 Junkyard digest cron — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "   Repo: $REPO"

# Run the full pipeline (push defaults ON since 2026-09-02)
python3 -u scripts/run_pipeline.py

echo "✅ Pipeline finished — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
