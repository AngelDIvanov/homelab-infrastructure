# NFS Server Down

## Alert
No dedicated alert — triggered by pods stuck in `ContainerCreating` with `mount.nfs` errors, or `PVCPending`.

## Symptoms
- Pods with NFS-backed PVCs stuck in `ContainerCreating`
- Events show: `MountVolume.SetUp failed ... mount.nfs: ... Connection refused`
- Affected apps: Grafana, Vaultwarden, Loki, Portainer

## Root Cause Checklist
- [ ] Is the NFS server process running on k3s-infra (.230)?
  ```bash
  ssh andy@192.168.122.230 sudo systemctl status nfs-server
  ```
- [ ] Are exports visible?
  ```bash
  ssh andy@192.168.122.230 sudo exportfs -v
  ```
- [ ] Can k3s-control mount the NFS share?
  ```bash
  ssh andy@192.168.122.218 sudo showmount -e 192.168.122.230
  ```

## Recovery Steps

### Step 1 — Restart NFS server
```bash
ssh andy@192.168.122.230 sudo systemctl restart nfs-server
ssh andy@192.168.122.230 sudo exportfs -ra
```

### Step 2 — Verify exports
```bash
ssh andy@192.168.122.230 sudo exportfs -v
# Should show: /data  192.168.122.0/24(rw,sync,no_root_squash,...)
```

### Step 3 — Force remount on worker nodes
```bash
# Restart any stuck pods (they'll remount on startup)
kubectl rollout restart deployment -n monitoring
kubectl rollout restart deployment -n vaultwarden
```

### Step 4 — If NFS exports are missing (server was rebuilt)
```bash
# Re-export manually on k3s-infra:
ssh andy@192.168.122.230 "echo '/data 192.168.122.0/24(rw,sync,no_subtree_check,no_root_squash)' | sudo tee -a /etc/exports"
ssh andy@192.168.122.230 sudo exportfs -ra
ssh andy@192.168.122.230 sudo systemctl enable --now nfs-server
```

## Verify Recovery
```bash
kubectl get pods -A | grep -v Running | grep -v Completed
# All pods should return to Running within 2-3 minutes
```

## Related Alerts
- `PVCPending` — PVC can't bind because NFS mount fails
