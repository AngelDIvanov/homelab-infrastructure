# Worker Node Not Ready

## Alert
- `WorkerNodeDown` (severity: info) — a worker node (.241, .242, ...) is NotReady

The control-plane node has its own alert, see [API Server Down](../control-plane/api-server-down.md).

## Symptoms
- `kubectl get nodes` shows node in `NotReady` state
- Pods on that node stuck in `Terminating` or evicted to other nodes

## Root Cause Checklist
- [ ] Is the VM running on the hypervisor?
  ```bash
  virsh list --all
  ```
- [ ] Can you SSH to the node?
  ```bash
  ssh labadmin@192.168.122.<node-ip>
  ```
- [ ] Are kubelet and containerd running?
  ```bash
  ssh labadmin@192.168.122.<node-ip> sudo systemctl status kubelet containerd
  ```
- [ ] Are there kubelet errors?
  ```bash
  ssh labadmin@192.168.122.<node-ip> sudo journalctl -u kubelet -n 30 --no-pager
  ```

## Node IP Reference
| Node | IP |
|------|----|
| kubeadm-worker-1 | 192.168.122.241 |
| kubeadm-worker-2 | 192.168.122.242 |

## Recovery Steps

### Step 1 — Start the VM if it's down
```bash
virsh list --all
virsh start kubeadm-worker-1   # or kubeadm-worker-2
```

### Step 2 — Restart the kubelet if VM is running but node NotReady
```bash
ssh labadmin@<ip> sudo systemctl restart containerd
ssh labadmin@<ip> sudo systemctl restart kubelet
# Watch node come back:
kubectl get nodes -w
```

### Step 3 — If the kubelet won't start (certificate or join issues)
```bash
ssh labadmin@<ip> sudo journalctl -u kubelet -n 50 --no-pager
# "certificate signed by unknown authority" or "Unauthorized": the node must be
# re-joined — see nodes/cluster-drift.md ("Node was never joined, or was deleted").
```

### Step 4 — If node is stuck Terminating for a long time
```bash
# Force-remove the node (only if VM is truly gone and won't return)
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force
kubectl delete node <node-name>   # e.g. kubeadm-worker-1
# Pods will reschedule to healthy nodes automatically
```

## Verify Recovery
```bash
kubectl get nodes
kubectl get pods -A -o wide | grep <node-name>  # Should be empty or Running
```

## Post-Incident
- [ ] Check why VM went down: hypervisor OOM? Host suspend?
- [ ] If host suspending: configure systemd to disable sleep (see laptop setup docs)
- [ ] If recurring: add node monitoring at hypervisor level

## Related Alerts
- `ControlPlaneUnhealthy` — if the control plane also went down
- `ClusterDrift` — VM running but node missing from the cluster
- `NodeMemoryHigh` — OOM may have caused the node failure
