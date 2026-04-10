# PVC Stuck in Pending

## Alert
`PVCPending` (severity: critical)

## Symptoms
- `kubectl get pvc -A` shows phase `Pending`
- Dependent pods stuck in `ContainerCreating`

## Root Cause Checklist
- [ ] Is the NFS provisioner running?
  ```bash
  kubectl get pods -n default -l app=nfs-client-provisioner
  ```
- [ ] Is the NFS server reachable?
  ```bash
  ssh andy@192.168.122.218 sudo showmount -e 192.168.122.230
  ```
- [ ] What do provisioner logs say?
  ```bash
  kubectl logs -n default -l app=nfs-client-provisioner --tail=30
  ```

## Recovery Steps

### Step 1 — Check provisioner
```bash
kubectl get pods -n default -l app=nfs-client-provisioner
# If CrashLoopBackOff or not running:
kubectl rollout restart deployment nfs-client-provisioner -n default
```

### Step 2 — If NFS server is down
See [NFS Server Down](nfs-server-down.md).

### Step 3 — If PVC is stuck and provisioner is healthy
```bash
# Delete and recreate the PVC (WARNING: existing data on archiveOnDelete: true
# is preserved in the NFS directory with an 'archived-' prefix)
kubectl delete pvc <pvc-name> -n <namespace>
kubectl apply -f kubernetes/deployments/nfs-pvcs.yaml
```

## Verify Recovery
```bash
kubectl get pvc -A
# All should show Bound
```
