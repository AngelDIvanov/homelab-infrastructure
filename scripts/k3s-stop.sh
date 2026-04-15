#!/bin/bash
echo "Shutting down K3s cluster..."

# Stop all workers discovered from virsh — no hardcoding
for vm in $(virsh list --all --name 2>/dev/null | grep -E '^k3s-worker-'); do
    echo "Stopping $vm..."
    virsh shutdown "$vm"
done

virsh shutdown k3s-control
echo "Waiting for VMs to stop..."
sleep 15

echo "Stopping CI Runner..."
virsh shutdown ci-runner

virsh list --all
