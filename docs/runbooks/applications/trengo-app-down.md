# Trengo App Down / Degraded

## Alerts
- `TrengoAppDown` (severity: critical) — 0 replicas available
- `TrengoAppDegraded` (severity: warning) — fewer replicas than desired

## Recovery Steps

### Check pod status
```bash
kubectl get pods -n default -l app=trengo-search
kubectl describe pod -n default -l app=trengo-search
```

### Image pull error (most common)
See [Pod Image Pull Error](pod-image-pull-error.md) — rebuild and push image to local registry.

### Accidental scale to 0
```bash
kubectl scale deployment trengo-search -n default --replicas=1
```

### Roll back to previous version
```bash
kubectl rollout history deployment/trengo-search -n default
kubectl rollout undo deployment/trengo-search -n default
```

## Verify Recovery
```bash
kubectl get deployment trengo-search -n default
# AVAILABLE should equal DESIRED
```
