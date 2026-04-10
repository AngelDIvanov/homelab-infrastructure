# Pod CrashLooping

## Alert
- `PodCrashLooping` (severity: warning) — >3 restarts in 15 min
- `CronJobFailed` (severity: warning)
- `CheckLabFailed` (severity: warning)

## Symptoms
- Pod status `CrashLoopBackOff`
- Pod restarting repeatedly
- `kubectl get pods` shows high RESTARTS count

## Root Cause Checklist
- [ ] What are the pod logs?
  ```bash
  kubectl logs <pod> -n <namespace> --previous
  ```
- [ ] What does describe say?
  ```bash
  kubectl describe pod <pod> -n <namespace>
  ```
- [ ] Is it OOMKilled?
  ```bash
  kubectl get pod <pod> -n <namespace> -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'
  ```
- [ ] Is it a config/secret issue?
  ```bash
  kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -20
  ```

## Recovery Steps

### Application crash (bug in code)
```bash
# Check logs for the error
kubectl logs <pod> -n <namespace> --previous --tail=50

# Roll back to previous deployment revision
kubectl rollout undo deployment/<name> -n <namespace>
kubectl rollout status deployment/<name> -n <namespace>
```

### OOMKilled — container hit memory limit
```bash
# Increase memory limit in the deployment manifest
kubectl edit deployment <name> -n <namespace>
# Or patch it:
kubectl patch deployment <name> -n <namespace> \
  --type merge -p '{"spec":{"template":{"spec":{"containers":[{"name":"<container>","resources":{"limits":{"memory":"512Mi"}}}]}}}}'
```

### Config/Secret missing
```bash
kubectl get secrets -n <namespace>
kubectl get configmaps -n <namespace>
# Re-apply the missing resource then restart pod:
kubectl rollout restart deployment/<name> -n <namespace>
```

## Verify Recovery
```bash
kubectl get pods -n <namespace>
# Status should be Running with RESTARTS not increasing
```

## Related Alerts
- `ContainerOOMKilled` — separate alert for OOM events
- `PodImagePullError` — pod may be crashing because image can't pull
