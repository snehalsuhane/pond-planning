#!/usr/bin/env bash
# =============================================================================
# scripts/start_jaeger.sh
# Downloads and starts Jaeger all-in-one (single binary, no Docker needed).
# Exposes:
#   - UI:             http://localhost:16686
#   - OTLP/HTTP:      http://localhost:4318   (traces from the Flask app)
#   - OTLP/gRPC:      localhost:4317
# =============================================================================
set -euo pipefail

JAEGER_VERSION="1.58.0"
JAEGER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.jaeger"
JAEGER_BIN="${JAEGER_DIR}/jaeger-all-in-one"
ARCH="$(uname -m)"

# Map arch names
if [[ "$ARCH" == "x86_64" ]]; then
    JAEGER_ARCH="amd64"
elif [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
    JAEGER_ARCH="arm64"
else
    echo "Unsupported architecture: $ARCH"
    exit 1
fi

TARBALL="jaeger-${JAEGER_VERSION}-linux-${JAEGER_ARCH}.tar.gz"
DOWNLOAD_URL="https://github.com/jaegertracing/jaeger/releases/download/v${JAEGER_VERSION}/${TARBALL}"

mkdir -p "$JAEGER_DIR"

if [[ ! -f "$JAEGER_BIN" ]]; then
    echo "[Jaeger] Binary not found. Downloading v${JAEGER_VERSION}..."
    curl -L --progress-bar "$DOWNLOAD_URL" -o "${JAEGER_DIR}/${TARBALL}"
    tar -xzf "${JAEGER_DIR}/${TARBALL}" --strip-components=1 -C "$JAEGER_DIR"
    chmod +x "$JAEGER_BIN"
    rm -f "${JAEGER_DIR}/${TARBALL}"
    echo "[Jaeger] Downloaded and extracted to ${JAEGER_BIN}"
else
    echo "[Jaeger] Binary already present: ${JAEGER_BIN}"
fi

echo "[Jaeger] Starting Jaeger all-in-one..."
echo "         UI  → http://localhost:16686"
echo "         OTLP/HTTP → http://localhost:4318"

exec "$JAEGER_BIN" \
    --collector.otlp.enabled=true \
    --collector.otlp.http.host-port=":4318" \
    --collector.otlp.grpc.host-port=":4317" \
    --query.ui-config="" 2>&1
