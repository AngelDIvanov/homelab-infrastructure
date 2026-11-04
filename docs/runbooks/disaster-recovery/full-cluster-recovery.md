# Full Cluster Recovery

## Scenario
Host hypervisor (laptop) fails completely. All VMs are gone. Need to rebuild from scratch.

## Prerequisites
- Latest etcd snapshot from the `etcd-snapshots` NFS claim (`/data` on the infra VM)
  - If the infra VM disk is also gone: last known-good snapshot from wherever you backed it up
- Terraform code (`terraform-kubeadm/` for the cluster, `terraform/` for the infra VM; state is local and untracked, see Step 1)
- Ansible playbooks (in `ansible/`, committed to Git)
- All Kubernetes manifests (committed to Git)

## Recovery Targets

| Component | RTO | RPO | Notes |
|---|---|---|---|
| **Full cluster (Terraform + Ansible)** | ~2 hours | 0 (infra-as-code) | VMs rebuilt from code; no data |
| **Cluster state (etcd)** | ~30 min | ≤ 12 hours | etcd snapshot every 12h |
| **Vaultwarden data** | ~15 min | ≤ 24 hours | SQLite backup daily at 02:00 |
| **GitLab repos & CI** | ~20 min | ≤ 24 hours | `gitlab-backup` daily at 03:00 |
| **Registry images** | ~30 min | 0 (rebuild from source) | Images rebuilt from Git; no state |
| **NFS volumes** | ~1 hour | external backup cadence | No automated off-site backup yet |
| **Monitoring data** | ~10 min | loss accepted | Loki/Prometheus data is ephemeral |

**Overall cluster RTO: ~2 hours** (all services operational)
**Worst-case data RPO: 24 hours** (GitLab/Vaultwarden backups)

---

## Step 1 — Provision new VMs with Terraform

Terraform state is not committed. Without it, `terraform apply` creates fresh
VMs; that is the expected outcome after host loss.

```bash
# Infra VM: NFS server + GitLab CE
cd terraform/
terraform init && terraform apply        # infra_enabled = true in terraform.tfvars

# kubeadm nodes
cd ../terraform-kubeadm/
terraform init && terraform apply        # writes ansible/inventory/kubeadm.ini
../scripts/libvirt-reserve-lab-range.sh  # keep DHCP below .200
../scripts/kubeadm-known-hosts.sh        # pin host keys of all VMs + hypervisor
```

## Step 2 — Bootstrap the cluster with Ansible

```bash
cd ansible/
ansible-playbook -i inventory/kubeadm.ini playbooks/kubeadm-bootstrap.yml
ansible-playbook -i inventory/kubeadm.ini playbooks/kubeadm-addons.yml
```

Restore the NFS server and GitLab on the infra VM first if they were lost
(Step 4 and Step 5), the add-ons need `/data` exported.

## Step 3 — Restore etcd (only if you want the old cluster state back)

With a snapshot, the cluster objects come back as they were. Without one, skip
to Step 6 and re-apply the manifests.

```bash
# On the new kubeadm-control node (192.168.122.240), snapshot copied to /tmp/etcd-snapshot.db:
sudo systemctl stop kubelet
sudo rm -rf /var/lib/etcd
sudo ETCDCTL_API=3 etcdctl snapshot restore /tmp/etcd-snapshot.db --data-dir /var/lib/etcd
sudo systemctl start kubelet
```

A snapshot taken on the old cluster carries the old node objects and CA; use
it only when the control node was rebuilt with the same `kubeadm` certificates
(`/etc/kubernetes/pki` backed up). Otherwise prefer a clean cluster plus Step 6.

Verify:
```bash
kubectl get nodes
kubectl get pods -A
```

## Step 4 — Restore NFS data

The NFS server runs on the infra VM (.230) at `/data`. If you have an external backup:
```bash
# Restore NFS data from backup
ssh labadmin@192.168.122.230 sudo rsync -av /backup/nfs-data/ /data/
```

## Step 5 — Restore GitLab

```bash
# Copy the newest gitlab-backup tarball to /var/opt/gitlab/backups on the infra VM, then:
ssh labadmin@192.168.122.230 'sudo gitlab-ctl stop puma && sudo gitlab-ctl stop sidekiq'
ssh labadmin@192.168.122.230 'sudo gitlab-backup restore BACKUP=<timestamp>'
ssh labadmin@192.168.122.230 'sudo gitlab-ctl restart && sudo gitlab-rake gitlab:check SANITIZE=true'
```

## Step 6 — Re-apply the workloads

```bash
cd ansible/
ansible-playbook -i inventory/kubeadm.ini playbooks/kubeadm-workloads.yml
# then the pieces the playbook does not own:
kubectl apply -f kubernetes/monitoring/lab-network-config.yaml   # your copy of the example
kubectl apply -f kubernetes/monitoring/
kubectl apply -f kubernetes/self-healing/
kubectl apply -f kubernetes/deployments/alertmanager-webhook.yaml
```

Recreate the Secrets from the `.example` templates and Vaultwarden
(`docs/runbooks/applications/secret-rotation.md` lists them).

## Step 7 — Rebuild registry images

The registry keeps its images on NFS; if `/data` was lost, re-trigger the CI pipelines:
```bash
# For each project with images in the local registry:
# Go to GitLab → Project → CI/CD → Pipelines → Run Pipeline
```

Or manually:
```bash
docker build -t 192.168.122.240:30500/pylab:latest /path/to/pylab
docker push 192.168.122.240:30500/pylab:latest
```

## Step 8 — Verify everything

```bash
kubectl get nodes              # All Ready
kubectl get pods -A            # All Running
curl -H 'Host: prometheus.homelab.local' http://192.168.122.200/-/healthy   # Prometheus
curl -H 'Host: grafana.homelab.local' http://192.168.122.200/api/health     # Grafana
```

---

## Monthly Recovery Drill

Run this monthly to verify the process works:

1. Take a manual snapshot: `kubectl create job --from=cronjob/etcd-snapshot manual-snap -n kube-system`
2. Spin up a throwaway control node (`terraform-kubeadm` workspace) and run `kubeadm init` on it
3. Restore the snapshot to the test node (Step 3)
4. Verify `kubectl get nodes` works
5. Destroy the test VM

Document the result and any issues found.
