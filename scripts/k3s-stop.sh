#!/usr/bin/env bash
set -uo pipefail

SHUTDOWN_TIMEOUT=${SHUTDOWN_TIMEOUT:-120}

if ! command -v virsh >/dev/null 2>&1; then
    echo "Error: virsh is required." >&2
    exit 1
fi

vm_running() {
    [ "$(virsh domstate "$1" 2>/dev/null)" = "running" ]
}

request_shutdown() {
    local vm=$1
    if ! virsh dominfo "$vm" >/dev/null 2>&1; then
        echo "Skipping undefined VM $vm."
    elif vm_running "$vm"; then
        echo "Requesting shutdown for $vm..."
        virsh shutdown "$vm" >/dev/null
    else
        echo "$vm is already stopped."
    fi
}

wait_for_shutdown() {
    local vm=$1
    local elapsed=0
    while vm_running "$vm"; do
        if (( elapsed >= SHUTDOWN_TIMEOUT )); then
            echo "Error: $vm did not stop within ${SHUTDOWN_TIMEOUT}s; refusing to force it off." >&2
            return 1
        fi
        sleep 2
        ((elapsed += 2))
    done
}

mapfile -t workers < <(
    virsh list --all --name 2>/dev/null | grep -E '^k3s-worker-[0-9]+$' | sort -Vr
)

for vm in "${workers[@]}"; do
    request_shutdown "$vm"
done
request_shutdown k3s-control

failed=0
for vm in "${workers[@]}" k3s-control; do
    wait_for_shutdown "$vm" || failed=1
done

virsh list --all
if (( failed )); then
    echo "One or more VMs are still running. Investigate before using 'virsh destroy'." >&2
    exit 1
fi

echo "K3s VMs stopped."
