#!/bin/bash
echo "Shutting down all environment..."

echo "Stopping K3s workers and control plane..."
for vm in k3s-worker-1 k3s-worker-2 k3s-control; do
    echo "Stopping $vm..."
    virsh shutdown $vm
done

# Dynamic workers (k3s-worker-3+) — only stop if they exist
for vm in $(virsh list --all --name 2>/dev/null | grep -E '^k3s-worker-[3-9]'); do
    echo "Stopping dynamic worker $vm..."
    virsh shutdown "$vm"
done
echo "Waiting for K3s VMs to stop..."
sleep 15

echo "Stopping CI Runner..."
virsh shutdown ci-runner

echo "Stopping CRC..."
virsh shutdown crc

echo "Waiting for remaining VMs to stop..."
sleep 10

virsh list --all
