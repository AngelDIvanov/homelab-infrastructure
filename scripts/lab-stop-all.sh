#!/bin/bash
echo "Shutting down all environment..."

echo "Stopping K3s workers and control plane..."

# Stop all workers discovered from virsh — no hardcoding
for vm in $(virsh list --all --name 2>/dev/null | grep -E '^k3s-worker-'); do
    echo "Stopping $vm..."
    virsh shutdown "$vm"
done

virsh shutdown k3s-control
echo "Waiting for K3s VMs to stop..."
sleep 15

echo "Stopping CI Runner..."
virsh shutdown ci-runner

echo "Stopping CRC..."
virsh shutdown crc

echo "Waiting for remaining VMs to stop..."
sleep 10

virsh list --all
