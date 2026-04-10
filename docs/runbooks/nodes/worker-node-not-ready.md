# Worker Node Not Ready

## Alert
- `K3sWorkerNodeDown` (severity: info) — worker node (.219 or .221)
- `KubeNodeNotReady` (severity: critical) — k3s-infra node (.230)

## Symptoms
- `kubectl get nodes` shows node in `NotReady` state
- Pods on that node stuck in `Terminating` or evicted to other nodes
- If k3s-infra: Grafana/Prometheus/Alertmanager unreachable

## Root Cause Checklist
- [ ] Is the VM running on the hypervisor?
  ```bash
  virsh list --all
  ```
- [ ] Can you SSH to the node?
  ```bash
  ssh andy@192.168.122.<node-ip>
  ```
- [ ] Is k3s-agent running?
  ```bash
  ssh andy@192.168.122.<node-ip> sudo systemctl status k3s-agent
  ```
- [ ] Are there agent errors?
  ```bash
  ssh andy@192.168.122.<node-ip> sudo journalctl -u k3s-agent -n 30 --no-pager
  ```

## Node IP Reference
| Node | IP |
|------|----|
| k3s-worker-1 | 192.168.122.219 |
| k3s-worker-2 | 192.168.122.221 |
| k3s-infra | 192.168.122.230 |

## Recovery Steps

### Step 1 — Start the VM if it's down
```bash
virsh list --all
virsh start k3s-worker-1   # or k3s-worker-2 / k3s-infra
```

### Step 2 — Restart k3s-agent if VM is running but node NotReady
```bash
# Replace <ip> with 192.168.122.219 (worker-1) or 192.168.122.221 (worker-2)
ssh andy@<ip> sudo systemctl restart k3s-agent
# Watch node come back:
kubectl get nodes -w
```

### Step 3 — If agent won't start (certificate or token issues)
```bash
ssh andy@<ip> sudo systemctl stop k3s-agent
ssh andy@<ip> sudo rm -f /var/lib/rancher/k3s/agent/client-ca.crt
ssh andy@<ip> sudo systemctl start k3s-agent
# If still failing with "invalid token format":
#   Check /etc/systemd/system/k3s-agent.service.env on the node
#   Token must match: sudo cat /var/lib/rancher/k3s/server/node-token (from k3s-control)
```

### Step 4 — If node is stuck Terminating for a long time
```bash
# Force-remove the node (only if VM is truly gone and won't return)
kubectl delete node <node-name>   # e.g. k3s-worker-1 or k3s-worker-2
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
- `K3sControlPlaneUnhealthy` — if control plane also went down
- `NodeMemoryHigh` — OOM may have caused the node failure
