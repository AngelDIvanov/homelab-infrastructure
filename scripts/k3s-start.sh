#!/bin/bash
echo "Starting CI Runner..."
virsh start ci-runner
sleep 10

echo "Starting K3s cluster..."
virsh start k3s-control
echo "Waiting for control plane..."
sleep 20

for vm in k3s-worker-1 k3s-worker-2 k3s-worker-3; do
    echo "Starting $vm..."
    virsh start $vm
    sleep 5
done

echo "Done."
virsh list --all
