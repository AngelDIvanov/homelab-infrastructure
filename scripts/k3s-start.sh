#!/bin/bash
# k3s-start.sh
# Starts all lab VMs dynamically — discovers what exists in virsh,
# skips already-running VMs and missing VMs gracefully.
# Order: ci-runner -> k3s-infra -> k3s-control -> workers (sorted)

start_vm() {
    local vm="$1"
    local delay="${2:-5}"

    # Check if VM exists
    if ! virsh dominfo "$vm" &>/dev/null; then
        echo "  skip    $vm  (not found)"
        return
    fi

    # Check if already running
    local state
    state=$(virsh domstate "$vm" 2>/dev/null)
    if [ "$state" = "running" ]; then
        echo "  running $vm  (already up)"
        return
    fi

    echo "  starting $vm..."
    virsh start "$vm" &>/dev/null
    if [ $? -eq 0 ]; then
        echo "  started  $vm"
    else
        echo "  FAILED   $vm"
    fi
    sleep "$delay"
}

echo ""
echo "  DevOps Home Lab -- Starting all VMs"
echo "  ------------------------------------"

# Fixed boot order: infra services first
start_vm "ci-runner"   5
start_vm "k3s-infra"   5
start_vm "k3s-control" 20  # longer wait for control plane

# Discover and start all workers dynamically (anything matching k3s-worker-*)
workers=$(virsh list --all 2>/dev/null | awk '{print $2}' | grep '^k3s-worker-' | sort)

if [ -z "$workers" ]; then
    echo "  no workers found"
else
    for vm in $workers; do
        start_vm "$vm" 5
    done
fi

echo ""
echo "  Done."
echo ""
virsh list --all
