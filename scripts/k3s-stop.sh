#!/bin/bash
echo "Shutting down K3s cluster..."
for vm in k3s-worker-1 k3s-worker-2 k3s-worker-3 k3s-control; do
    echo "Stopping $vm..."
    virsh shutdown $vm
done
echo "Waiting for VMs to stop..."
sleep 15

echo "Stopping CI Runner..."
virsh shutdown ci-runner

virsh list --all
