#!/bin/bash
echo "Starting CI Runner..."
virsh start ci-runner 2>/dev/null || echo "ci-runner already running"
sleep 10

echo "Starting K3s cluster..."
virsh start k3s-control 2>/dev/null || echo "k3s-control already running"
echo "Waiting for control plane to be responsive..."
for i in $(seq 1 30); do
    if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no andy@192.168.122.218 "sudo k3s kubectl get nodes" &>/dev/null; then
        echo "Control plane ready after ${i}0s"
        break
    fi
    echo "  [${i}0s] waiting for k3s-control..."
    sleep 10
done

# Dynamic worker discovery — picks up any k3s-worker-* regardless of count
for vm in $(virsh list --all --name 2>/dev/null | grep '^k3s-worker'); do
    echo "Starting $vm..."
    virsh start "$vm" 2>/dev/null || echo "$vm already running"
    sleep 5
done

echo "Done."
virsh list --all
