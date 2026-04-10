# Full Cluster Recovery

## Scenario
Host hypervisor (laptop) fails completely. All VMs are gone. Need to rebuild from scratch.

## Prerequisites
- Latest k3s SQLite snapshot from `/var/lib/k3s-snapshots/` (on k3s-infra disk)
  - If k3s-infra disk is also gone: last known-good snapshot from wherever you backed it up
- Terraform state (in `terraform/` directory, committed to Git)
- Ansible playbooks (in `ansible/`, committed to Git)
- All Kubernetes manifests (committed to Git)

## Recovery Targets

| Component | RTO | RPO | Notes |
|---|---|---|---|
| **Full cluster (Terraform + Ansible)** | ~2 hours | 0 (infra-as-code) | VMs rebuilt from code; no data |
| **k3s cluster state** | ~30 min | ≤ 12 hours | SQLite snapshot every 12h |
| **Vaultwarden data** | ~15 min | ≤ 24 hours | SQLite backup daily at 02:00 |
| **GitLab repos & CI** | ~20 min | ≤ 24 hours | `gitlab-backup` daily at 03:00 |
| **Registry images** | ~30 min | 0 (rebuild from source) | Images rebuilt from Git; no state |
| **NFS volumes** | ~1 hour | external backup cadence | No automated off-site backup yet |
| **Monitoring data** | ~10 min | loss accepted | Loki/Prometheus data is ephemeral |

**Overall cluster RTO: ~2 hours** (all services operational)
**Worst-case data RPO: 24 hours** (GitLab/Vaultwarden backups)

---

## Step 1 — Provision new VMs with Terraform

```bash
cd terraform/
terraform init
terraform apply
# This recreates: k3s-control, k3s-worker-1, k3s-worker-2, k3s-infra, ci-runner
```

## Step 2 — Run Ansible to configure nodes

```bash
cd ansible/
ansible-playbook playbooks/k3s.yaml -i inventory/hosts
ansible-playbook playbooks/infra.yaml -i inventory/hosts
```

## Step 3 — Restore k3s SQLite database

If you have a snapshot:
```bash
# On the new k3s-control node (192.168.122.218):
sudo systemctl stop k3s
sudo cp /path/to/k3s-state-<TIMESTAMP>.db /var/lib/rancher/k3s/server/db/state.db
sudo chown root:root /var/lib/rancher/k3s/server/db/state.db
sudo chmod 600 /var/lib/rancher/k3s/server/db/state.db
sudo systemctl start k3s
```

Verify:
```bash
kubectl get nodes
kubectl get pods -A
```

## Step 4 — Restore NFS data

The NFS server runs on k3s-infra (.230) at `/data`. If you have an external backup:
```bash
# Restore NFS data from backup
ssh andy@192.168.122.230 sudo rsync -av /backup/nfs-data/ /data/
```

## Step 5 — Re-apply Kubernetes manifests

If cluster state was restored from snapshot, most resources should already exist.
If starting fresh:
```bash
kubectl apply -f kubernetes/deployments/
kubectl apply -f kubernetes/policies/
kubectl apply -f kubernetes/backup/
```

## Step 6 — Rebuild registry images

The local registry loses images on restart. Re-trigger CI pipelines:
```bash
# For each project with images in the local registry:
# Go to GitLab → Project → CI/CD → Pipelines → Run Pipeline
```

Or manually:
```bash
docker build -t 192.168.122.218:30500/pylab:latest /path/to/pylab
docker push 192.168.122.218:30500/pylab:latest
```

## Step 7 — Verify everything

```bash
kubectl get nodes              # All Ready
kubectl get pods -A            # All Running
curl http://192.168.122.218:30090/-/healthy   # Prometheus
curl http://192.168.122.218:30080/api/health  # Grafana
```

---

## Monthly Recovery Drill

Run this monthly to verify the process works:

1. Take a manual snapshot: `kubectl create job --from=cronjob/k3s-db-snapshot manual-snap -n kube-system`
2. Spin up a test VM using Terraform workspace
3. Restore the snapshot to the test VM
4. Verify `kubectl get nodes` works
5. Destroy the test VM

Document the result and any issues found.
