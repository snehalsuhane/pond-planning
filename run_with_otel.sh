#!/usr/bin/env bash
# =============================================================================
# run_with_otel.sh
# Convenience launcher: starts Jaeger (background) then the Flask app.
#
# Usage:
#   source ~/my_env/bin/activate
#   bash run_with_otel.sh
#
# Observability endpoints (internal → external for machine 2273):
#   Flask API        → http://localhost:5000  (external: 5273, App 3 slot)
#   Jaeger UI        → http://localhost:4000  (external: 4273, App 2 slot)
#   Prometheus scrape→ http://localhost:6000/metrics  (external: 6273, App 4 slot)
#   JSON log file    → logs/otel_app.jsonl
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Jaeger ─────────────────────────────────────────────────────────────────
if pgrep -f "jaeger-all-in-one" > /dev/null 2>&1; then
    echo "[OTel] Jaeger already running."
else
    echo "[OTel] Starting Jaeger in background..."
    bash scripts/start_jaeger.sh > logs/jaeger.log 2>&1 &
    JAEGER_PID=$!
    echo "[OTel] Jaeger PID=${JAEGER_PID}  (log: logs/jaeger.log)"
    sleep 3   # give Jaeger a moment to bind its ports
fi

# ── 2. Flask app ──────────────────────────────────────────────────────────────
echo ""
echo "[OTel] Starting Flask app..."
echo "       Flask API        → http://localhost:5000  (external: 5273)"
echo "       Jaeger UI        → http://localhost:4000  (external: 4273)"
echo "       Prometheus       → http://localhost:6000/metrics  (external: 6273)"
echo "       JSON logs        → logs/otel_app.jsonl"
echo ""

export FLASK_ENV=development
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
export PROMETHEUS_PORT=6000

python app.py
