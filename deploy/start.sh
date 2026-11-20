#!/bin/sh
set -eu
# Render mounts the disk as root. Drop privileges after preparing its owner.
: "${PARTS_DESK_DATA_DIR:=/var/data}"
: "${PORT:=8000}"
: "${PARTS_DESK_OPS_TOKEN:?Set a private operator token before exposing this service}"
export PARTS_DESK_DATA_DIR
mkdir -p "$PARTS_DESK_DATA_DIR"
chown -R partsdesk:partsdesk "$PARTS_DESK_DATA_DIR"
exec runuser -u partsdesk -- /app/.venv/bin/uvicorn \
    --factory partsdesk.api.app:create_app --host 0.0.0.0 --port "$PORT" \
    --workers 1 --timeout-graceful-shutdown 30 --no-access-log
