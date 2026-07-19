#!/usr/bin/env bash
set -uo pipefail

VM_NAME=ci-runner
TIMEOUT=${CI_RUNNER_TIMEOUT:-120}
ACTION=${1:-status}

if ! command -v virsh >/dev/null 2>&1; then
    echo "Error: virsh is required." >&2
    exit 1
fi
if ! virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
    echo "Error: VM '$VM_NAME' is not defined." >&2
    exit 1
fi

vm_running() {
    [ "$(virsh domstate "$VM_NAME" 2>/dev/null)" = "running" ]
}

wait_for_state() {
    local expected=$1
    local elapsed=0
    while true; do
        if [ "$expected" = running ] && vm_running; then
            return 0
        fi
        if [ "$expected" = stopped ] && ! vm_running; then
            return 0
        fi
        if (( elapsed >= TIMEOUT )); then
            echo "Error: timed out waiting for $VM_NAME to become $expected." >&2
            return 1
        fi
        sleep 2
        ((elapsed += 2))
    done
}

case "$ACTION" in
    start)
        if vm_running; then
            echo "$VM_NAME is already running."
        else
            echo "Starting $VM_NAME..."
            virsh start "$VM_NAME" >/dev/null
            wait_for_state running
            echo "$VM_NAME started."
        fi
        ;;
    stop)
        if ! vm_running; then
            echo "$VM_NAME is already stopped."
        else
            echo "Requesting graceful shutdown for $VM_NAME..."
            virsh shutdown "$VM_NAME" >/dev/null
            wait_for_state stopped
            echo "$VM_NAME stopped."
        fi
        ;;
    status)
        virsh domstate "$VM_NAME"
        ;;
    *)
        echo "Usage: $0 {start|stop|status}" >&2
        exit 2
        ;;
esac
