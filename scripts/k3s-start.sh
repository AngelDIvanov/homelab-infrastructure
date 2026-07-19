#!/usr/bin/env bash
set -uo pipefail

START_TIMEOUT=${START_TIMEOUT:-30}

if ! command -v virsh >/dev/null 2>&1; then
    echo "Error: virsh is required." >&2
    exit 1
fi

vm_exists() {
    virsh dominfo "$1" >/dev/null 2>&1
}

vm_running() {
    [ "$(virsh domstate "$1" 2>/dev/null)" = "running" ]
}

start_vm() {
    local vm=$1
    local elapsed=0

    if ! vm_exists "$vm"; then
        echo "Error: VM '$vm' is not defined." >&2
        return 1
    fi
    if vm_running "$vm"; then
        echo "$vm is already running."
        return 0
    fi

    echo "Starting $vm..."
    virsh start "$vm" >/dev/null || return 1
    until vm_running "$vm"; do
        if (( elapsed >= START_TIMEOUT )); then
            echo "Error: timed out waiting for $vm to start." >&2
            return 1
        fi
        sleep 2
        ((elapsed += 2))
    done
}

mapfile -t workers < <(
    virsh list --all --name 2>/dev/null | grep -E '^k3s-worker-[0-9]+$' | sort -V
)

failed=0
start_vm k3s-control || failed=1
for vm in "${workers[@]}"; do
    start_vm "$vm" || failed=1
done

virsh list --all
if (( failed )); then
    echo "One or more k3s VMs failed to start." >&2
    exit 1
fi

echo "K3s VMs started. Run ./scripts/check-lab.sh to verify cluster health."
