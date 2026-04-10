# Node Disk Full

## Alerts
- `NodeDiskHigh` (>75%, severity: warning)
- `NodeDiskCritical` (>90%, severity: critical)

## Symptoms
- Pods failing with `no space left on device`
- New images can't be pulled
- Logs not being written

## Recovery Steps

### Step 1 — Prune unused container images (quickest win)
```bash
ssh andy@192.168.122.<ip> sudo k3s crictl rmi --prune
```

### Step 2 — Find large directories
```bash
ssh andy@192.168.122.<ip> sudo du -sh /var/lib/rancher/k3s/* | sort -h | tail -10
ssh andy@192.168.122.<ip> sudo du -sh /var/log/* | sort -h | tail -10
```

### Step 3 — Vacuum old journal logs
```bash
ssh andy@192.168.122.<ip> sudo journalctl --vacuum-size=200M
```

### Step 4 — On k3s-infra (.230): clean GitLab artifacts
```bash
ssh andy@192.168.122.230 sudo gitlab-rake gitlab:cleanup:orphan_job_artifact_files
ssh andy@192.168.122.230 sudo gitlab-ctl tail unicorn 2>/dev/null | tail -5
```

### Step 5 — Check Loki storage
```bash
kubectl exec -n monitoring -l app.kubernetes.io/name=loki -- df -h /var/loki
# If full, reduce retention or expand PVC
```

## Verify Recovery
```bash
ssh andy@192.168.122.<ip> df -h /
# Should be below 75%
```

## Related Alerts
- `K3sControlPlaneUnhealthy` — disk full on control plane causes k3s crash
- `PodImagePullError` — can't pull images when disk is full
