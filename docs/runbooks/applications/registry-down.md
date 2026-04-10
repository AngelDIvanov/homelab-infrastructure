# Local Registry Down

## Alert
`LocalRegistryDown` (severity: critical)

## Symptoms
- New pod deployments fail with `ImagePullBackOff`
- `curl http://192.168.122.218:30500/v2/` returns connection refused
- Registry pod in CrashLoopBackOff or Pending

## Root Cause Checklist
- [ ] Is the registry pod running?
  ```bash
  kubectl get pods -n registry
  ```
- [ ] What do registry logs say?
  ```bash
  kubectl logs -n registry -l app=registry --tail=30
  ```
- [ ] Is the NFS PVC mounted correctly?
  ```bash
  kubectl describe pod -n registry -l app=registry | grep -A5 Volumes
  ```
- [ ] Is the NFS server up?
  ```bash
  ssh andy@192.168.122.230 sudo systemctl status nfs-server
  ```

## Recovery Steps

### Registry pod is down — restart it
```bash
kubectl rollout restart deployment/registry -n registry
kubectl rollout status deployment/registry -n registry --timeout=60s
```

### NFS PVC issue — check mount
```bash
kubectl describe pvc registry-data -n registry
# If Pending: see storage/nfs-server-down.md runbook
```

### Verify images are still present after restart
```bash
curl http://192.168.122.218:30500/v2/_catalog
# Should show: {"repositories":["pylab","trengo-search",...]}
```

### Images lost (registry was using emptyDir previously)
If the registry lost its images, rebuild and push:
```bash
docker build -t 192.168.122.218:30500/pylab:latest /home/andy/pylab/
docker push 192.168.122.218:30500/pylab:latest
docker build -t 192.168.122.218:30500/trengo-search:latest /home/andy/trengo-search/
docker push 192.168.122.218:30500/trengo-search:latest
```

## Verify Recovery
```bash
curl http://192.168.122.218:30500/v2/_catalog
kubectl get pods -A | grep -E "ImagePull|ErrImage"
# Should be empty
```

## Related Alerts
- `PodImagePullError` — fired by pods that can't pull
- `PVCPending` — if underlying NFS storage is missing
