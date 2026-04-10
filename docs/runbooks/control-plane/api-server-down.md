# K3s Control Plane Unreachable

## Alert
`K3sControlPlaneUnhealthy` (severity: critical)

## Symptoms
- `kubectl` commands fail: `connection refused` or `dial tcp: i/o timeout`
- Deployments cannot scale, pods not rescheduled
- GitLab CI/CD pipelines blocked
- Alertmanager may stop firing new alerts (if control plane hosts metrics)

## Root Cause Checklist
- [ ] Is k3s service running on k3s-control (.218)?
  ```bash
  ssh andy@192.168.122.218 sudo systemctl status k3s
  ```
- [ ] Is the API server listening on port 6443?
  ```bash
  ssh andy@192.168.122.218 sudo ss -tlnp | grep 6443
  ```
- [ ] Are there errors in the k3s journal?
  ```bash
  ssh andy@192.168.122.218 sudo journalctl -u k3s -n 50 --no-pager
  ```
- [ ] Is the disk full on k3s-control?
  ```bash
  ssh andy@192.168.122.218 df -h /var/lib/rancher/k3s/
  ```
- [ ] Is the VM itself running on the hypervisor?
  ```bash
  virsh list --all | grep k3s-control
  ```

## Recovery Steps

### Step 1 — Verify VM is running
```bash
virsh list --all
# If k3s-control is shut off:
virsh start k3s-control
# Wait ~30s for boot, then proceed
```

### Step 2 — Restart k3s service
```bash
ssh andy@192.168.122.218 sudo systemctl restart k3s
# Wait 30s then check
ssh andy@192.168.122.218 sudo systemctl status k3s
```

### Step 3 — If disk is full, clear space
```bash
ssh andy@192.168.122.218 sudo k3s crictl rmi --prune
ssh andy@192.168.122.218 sudo journalctl --vacuum-size=200M
```

### Step 4 — If SQLite database is corrupted
```bash
# Check for corruption
ssh andy@192.168.122.218 sudo sqlite3 /var/lib/rancher/k3s/server/db/state.db "PRAGMA integrity_check;"

# If corrupted, restore from latest snapshot (on k3s-infra)
ls -lt /var/lib/k3s-snapshots/
# Copy latest snapshot back to k3s-control:
scp /var/lib/k3s-snapshots/k3s-state-<LATEST>.db andy@192.168.122.218:/tmp/
ssh andy@192.168.122.218 "sudo systemctl stop k3s && sudo cp /tmp/k3s-state-<LATEST>.db /var/lib/rancher/k3s/server/db/state.db && sudo systemctl start k3s"
```

## Verify Recovery
```bash
kubectl get nodes          # All nodes Ready
kubectl get pods -A        # No new errors
kubectl get events -A --sort-by='.lastTimestamp' | tail -20
```

## Escalation
If the above steps don't restore the control plane within 15 minutes, proceed to [Full Cluster Recovery](../disaster-recovery/full-cluster-recovery.md).

## Post-Incident
- [ ] Review why k3s stopped (OOM, disk, crash)
- [ ] Verify snapshot job is running: `kubectl get cronjob k3s-db-snapshot -n kube-system`
- [ ] Check snapshot age: `ls -lt /var/lib/k3s-snapshots/`

## Related Alerts
- `K3sWorkerNodeDown` — worker nodes unreachable after control plane recovery
- `NodeDiskCritical` — disk pressure may have caused this
