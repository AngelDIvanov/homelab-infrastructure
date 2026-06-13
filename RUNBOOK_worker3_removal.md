# Runbook — Remove `k3s-worker-3` from the live cluster

Removes worker node `k3s-worker-3` (IP `192.168.122.222`, libvirt VM) from the
k3s cluster, leaving a 4-node cluster: `k3s-control`, `k3s-infra`,
`k3s-worker-1`, `k3s-worker-2`.

## Context / assumptions

- `k3s-worker-3` is a Terraform-managed worker. Terraform creates workers from
  the `vm_count` variable: worker `N` = `count.index + 2`, IP =
  `192.168.122.{base_ip_octet + count.index}` (base_ip_octet = 221).
  So with `vm_count = 2`: index 0 → `k3s-worker-2` (.221),
  index 1 → `k3s-worker-3` (.222).
  **Decreasing `vm_count` from 2 to 1 destroys exactly the highest-index VM,
  `k3s-worker-3`.** No other worker is touched.
- Terraform is run from the `terraform/` directory; state lives there.
- `kubectl` is run from this machine. If you do not have a local kubeconfig,
  prefix node commands with
  `ssh andy@192.168.122.218 "sudo k3s kubectl ..."` instead. Examples below
  use `kubectl` directly — adapt as needed.
- This branch already removed `k3s-worker-3` from the Ansible inventory; apply
  this branch (merge/checkout) before running so config matches reality.

## Pre-flight (read-only — verify state before any change)

```bash
# 1. Confirm the node exists and is currently Ready
kubectl get nodes -o wide

# 2. Confirm vm_count is 2 (worker-3 is the highest-index Terraform worker)
grep vm_count terraform/terraform.tfvars

# 3. See exactly what Terraform would destroy when vm_count -> 1 (READ-ONLY).
#    Confirm the plan only destroys *.worker[1] (k3s-worker-3 / -cloudinit / volume).
cd terraform
terraform plan -var 'vm_count=1'
cd ..

# 4. List pods currently scheduled on the node (know what will be evicted)
kubectl get pods -A -o wide --field-selector spec.nodeName=k3s-worker-3

# 5. Safety: check no single-replica Deployment lives only on worker-3.
#    Scale up anything at 1 replica that must stay available before draining.
kubectl get deploy -A
```

> STOP if `terraform plan` shows it would destroy or replace anything other
> than the `k3s-worker-3` triplet (domain / cloudinit disk / volume at index 1).
> A mismatch means `vm_count` is not 2 or the index assumption is wrong — do
> not proceed.

## Step 1 — Cordon the node (stop new scheduling)

```bash
kubectl cordon k3s-worker-3
kubectl get node k3s-worker-3        # verify: SchedulingDisabled
```

Rollback: `kubectl uncordon k3s-worker-3` (node returns to service, nothing lost).

## Step 2 — Drain the node (graceful eviction)

```bash
kubectl drain k3s-worker-3 \
  --ignore-daemonsets \
  --delete-emptydir-data \
  --force \
  --grace-period=120 \
  --timeout=300s
```

Flag rationale:
- `--ignore-daemonsets`: DaemonSet pods (node-exporter, kube-proxy, svclb)
  cannot be evicted and are expected on every node — ignore them.
- `--delete-emptydir-data`: allow eviction of pods using emptyDir scratch
  volumes (data there is ephemeral and lost by design).
- `--force`: evict any standalone pods not managed by a controller.
- `--grace-period=120` / `--timeout=300s`: give pods a clean shutdown window
  and bound the wait so a stuck pod does not hang the drain forever.

Verify the node has no workload pods left (only daemonsets remain):

```bash
kubectl get pods -A -o wide --field-selector spec.nodeName=k3s-worker-3
# Confirm rescheduled pods are Running on the remaining nodes:
kubectl get pods -A -o wide | grep -v k3s-worker-3
```

Rollback (mid-drain, before deleting the node):
`kubectl uncordon k3s-worker-3` — evicted pods stay where they rescheduled;
the node simply becomes schedulable again. No Terraform action has happened yet.

## Step 3 — Delete the node object from k3s

```bash
kubectl delete node k3s-worker-3
kubectl get nodes                    # verify: 4 nodes remain, all Ready
```

Rollback (before VM is destroyed): the VM still exists and runs `k3s-agent`.
Restart the agent to let it re-register:
`ssh andy@192.168.122.222 "sudo systemctl restart k3s-agent"`, then
`kubectl get nodes` until `k3s-worker-3` is Ready again, then `uncordon`.

## Step 4 — Destroy the VM with Terraform

Edit `terraform/terraform.tfvars` and change the worker count:

```bash
# from: vm_count       = 2
# to:   vm_count       = 1
sed -i 's/^vm_count.*/vm_count       = 1/' terraform/terraform.tfvars
grep vm_count terraform/terraform.tfvars   # verify it now reads 1
```

Plan and apply from the `terraform/` directory:

```bash
cd terraform
terraform plan      # re-confirm: only k3s-worker-3 (domain+cloudinit+volume) destroyed
terraform apply     # review the plan, type 'yes' to confirm
cd ..
```

Verify the VM is gone:

```bash
virsh list --all | grep -i worker      # k3s-worker-3 should be absent
```

Rollback (if apply fails or you change your mind before/after):
set `vm_count = 2` again and `terraform apply` to recreate the VM, then
re-provision it as a worker (join via `lab-control.py` Upscale, or
`scripts/create-worker.sh 3` + k3s join). Note: a recreated VM is a fresh
node and must rejoin the cluster from scratch.

## Step 5 — Clean up stale host key

```bash
ssh-keygen -f ~/.ssh/known_hosts -R 192.168.122.222 || true
```

## Step 6 — Final verification

```bash
kubectl get nodes -o wide              # exactly 4 nodes, all Ready
kubectl get pods -A | grep -iv running | grep -v Completed   # no stuck pods
virsh list --all                       # no k3s-worker-3 VM
grep -c worker terraform/terraform.tfvars  # vm_count = 1
```

Expected final cluster: `k3s-control`, `k3s-infra`, `k3s-worker-1`,
`k3s-worker-2` — all `Ready`.

## Rollback summary (by phase)

| Phase reached            | How to undo                                                        |
|--------------------------|--------------------------------------------------------------------|
| Cordoned only            | `kubectl uncordon k3s-worker-3`                                    |
| Drained, node not deleted| `kubectl uncordon k3s-worker-3`                                   |
| Node deleted, VM alive   | `ssh ...222 "sudo systemctl restart k3s-agent"`, wait Ready, uncordon |
| VM destroyed (Terraform) | `vm_count = 2` + `terraform apply`, then rejoin node as new worker |

Point of no (easy) return: Step 4 `terraform apply`. After the VM is destroyed,
recovery means provisioning a brand-new node, not restoring the old one.
