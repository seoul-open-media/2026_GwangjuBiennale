#!/usr/bin/env bash
set -euo pipefail

# systemd ExecStartPre helper for GB16 services.
# Usage: check_runtime_deps.sh [bridge|host|audience]

ROLE="${1:-bridge}"

require_cmd() {
    local c="$1"
    if ! command -v "$c" >/dev/null 2>&1; then
        echo "[ERR] missing command: $c" >&2
        exit 127
    fi
}

require_cmd python3

if [[ "$ROLE" == "bridge" ]]; then
    python3 - <<'PY'
import importlib
import sys

required = ["paho.mqtt.client", "dotenv"]
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception:
        missing.append(name)

if missing:
    sys.stderr.write("[ERR] missing python modules: " + ", ".join(missing) + "\n")
    sys.exit(1)

print("[OK] runtime deps: bridge")
PY
else
    echo "[OK] runtime deps: $ROLE"
fi

exit 0