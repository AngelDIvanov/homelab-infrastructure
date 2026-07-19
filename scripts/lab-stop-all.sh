#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SHUTDOWN_TIMEOUT=${SHUTDOWN_TIMEOUT:-120}

vm_running() {
    [ "$(virsh domstate "$1" 2>/dev/null)" = "running" ]
}

stop_vm() {
    local vm=$1
    local elapsed=0

    if ! virsh dominfo "$vm" >/dev/null 2>&1; then
        echo "Skipping undefined VM $vm."
        return 0
    fi
    if ! vm_running "$vm"; then
        echo "$vm is already stopped."
        return 0
    fi

    echo "Requesting shutdown for $vm..."
    virsh shutdown "$vm" >/dev/null || return 1
    while vm_running "$vm"; do
        if (( elapsed >= SHUTDOWN_TIMEOUT )); then
            echo "Error: $vm did not stop within ${SHUTDOWN_TIMEOUT}s; refusing to force it off." >&2
            return 1
        fi
        sleep 2
        ((elapsed += 2))
    done
}

echo "Shutting down the lab (k3s and CI runner; k3s-infra remains running)..."
failed=0
"$SCRIPT_DIR/k3s-stop.sh" || failed=1
stop_vm ci-runner || failed=1

virsh list --all
if (( failed )); then
    echo "The lab shutdown completed with errors." >&2
    exit 1
fi

echo "Lab shutdown complete."
