# Control Plane Unreachable

## Alert
- `ControlPlaneUnhealthy` (severity: critical) — the kube-apiserver scrape target is down
- `ControlPlaneNodeNotReady` (severity: critical) — the kubeadm-control node is NotReady

## Symptoms
- `kubectl` commands fail: `connection refused` or `dial tcp: i/o timeout`
- Deployments cannot scale, pods not rescheduled
- GitLab CI/CD pipelines blocked
- Alertmanager may stop firing new alerts (Prometheus cannot discover targets)

## Root Cause Checklist
- [ ] Is the VM itself running on the hypervisor?
  ```bash
  virsh list --all | grep kubeadm-control
  ```
- [ ] Is the API server listening on port 6443?
  ```bash
  ssh labadmin@192.168.122.240 sudo ss -tlnp | grep 6443
  ```
- [ ] Are kubelet and containerd running? (the control-plane components are static pods managed by the kubelet)
  ```bash
  ssh labadmin@192.168.122.240 sudo systemctl status kubelet containerd
  ```
- [ ] Are the static pods up?
  ```bash
  ssh labadmin@192.168.122.240 sudo crictl ps -a --name 'kube-apiserver|etcd|kube-scheduler|kube-controller-manager'
  ```
- [ ] Are there errors in the kubelet journal?
  ```bash
  ssh labadmin@192.168.122.240 sudo journalctl -u kubelet -n 50 --no-pager
  ```
- [ ] Is the disk full on kubeadm-control?
  ```bash
  ssh labadmin@192.168.122.240 df -h / /var/lib/etcd /var/lib/containerd
  ```

## Recovery Steps

### Step 1 — Verify VM is running
```bash
virsh list --all
# If kubeadm-control is shut off:
virsh start kubeadm-control
# Wait ~60s for boot and the static pods, then proceed
```

### Step 2 — Restart the kubelet (re-creates the static pods)
```bash
ssh labadmin@192.168.122.240 sudo systemctl restart containerd
ssh labadmin@192.168.122.240 sudo systemctl restart kubelet
# Wait 30s then check
ssh labadmin@192.168.122.240 sudo crictl ps --name kube-apiserver
```

### Step 3 — If disk is full, clear space
```bash
ssh labadmin@192.168.122.240 sudo crictl rmi --prune
ssh labadmin@192.168.122.240 sudo journalctl --vacuum-size=200M
```

### Step 4 — If a static pod keeps crashing
```bash
# Logs of the last apiserver container
ssh labadmin@192.168.122.240 'sudo crictl logs --tail 50 $(sudo crictl ps -a --name kube-apiserver -q | head -1)'
# Same for etcd
ssh labadmin@192.168.122.240 'sudo crictl logs --tail 50 $(sudo crictl ps -a --name etcd -q | head -1)'
```

### Step 5 — If etcd is corrupted, restore from the latest snapshot
The `etcd-snapshot` CronJob (`kubernetes/backup/etcd-snapshot-cronjob.yaml`)
writes snapshots to the `etcd-snapshots` NFS claim every 12h.

```bash
# Pick the snapshot from a pod that mounts the claim, copy it to the control node
kubectl -n kube-system get pods -l job-name --field-selector=status.phase=Succeeded | head
# ... copy the chosen snapshot to /tmp/etcd-snapshot.db on kubeadm-control, then:
ssh labadmin@192.168.122.240 '
  sudo systemctl stop kubelet
  sudo mv /var/lib/etcd /var/lib/etcd.broken.$(date +%s)
  sudo ETCDCTL_API=3 etcdctl snapshot restore /tmp/etcd-snapshot.db --data-dir /var/lib/etcd
  sudo systemctl start kubelet
'
```

## Verify Recovery
```bash
kubectl get nodes          # All nodes Ready
kubectl get pods -n kube-system   # apiserver, etcd, scheduler, controller-manager Running
kubectl get events -A --sort-by='.lastTimestamp' | tail -20
```

## Escalation
If the above steps don't restore the control plane within 15 minutes, proceed to [Full Cluster Recovery](../disaster-recovery/full-cluster-recovery.md).

## Post-Incident
- [ ] Review why the control plane stopped (OOM, disk, crash)
- [ ] Verify the snapshot job is running: `kubectl get cronjob etcd-snapshot -n kube-system`
- [ ] Check snapshot age: `kubectl get jobs -n kube-system --sort-by=.status.startTime | grep etcd-snapshot`, then list `/backup` from a pod that mounts the `etcd-snapshots` claim

## Related Alerts
- `WorkerNodeDown` — worker nodes unreachable after control plane recovery
- `EtcdSnapshotMissing` — no snapshot to restore from
- `NodeDiskCritical` — disk pressure may have caused this
