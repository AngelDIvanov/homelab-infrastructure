#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "Shutting down the lab (k3s and CI runner; k3s-infra remains running)..."
failed=0
"$SCRIPT_DIR/k3s-stop.sh" || failed=1
"$SCRIPT_DIR/ci-runner.sh" stop || failed=1

virsh list --all
if (( failed )); then
    echo "The lab shutdown completed with errors." >&2
    exit 1
fi

echo "Lab shutdown complete."
