# Pod Image Pull Error

## Alert
`PodImagePullError` (severity: critical)

## Symptoms
- Pod status `ImagePullBackOff` or `ErrImagePull`
- `kubectl describe pod` shows: `Failed to pull image ... not found` or `unauthorized`

## Root Cause Checklist
- [ ] Does the image exist in the registry?
  ```bash
  curl http://192.168.122.218:30500/v2/_catalog
  curl http://192.168.122.218:30500/v2/<image>/tags/list
  ```
- [ ] Is the local registry pod running?
  ```bash
  kubectl get pods -n registry
  ```
- [ ] Is registries.yaml configured on the affected node?
  ```bash
  ssh andy@192.168.122.<node-ip> cat /etc/rancher/k3s/registries.yaml
  ```

## Recovery Steps

### Image missing from registry — rebuild and push via CI
```bash
# Trigger the GitLab CI pipeline for the affected project
# Or manually build and push:
docker build -t 192.168.122.218:30500/<image>:latest .
docker push 192.168.122.218:30500/<image>:latest
```

### Registry pod is down — restart it
```bash
kubectl rollout restart deployment/registry -n registry
# Wait for it to come up
kubectl get pods -n registry -w
# NOTE: registry uses emptyDir — all images are lost on restart!
# You MUST rebuild and push all images after registry restart.
```

### registries.yaml missing on node
```bash
ssh andy@192.168.122.<node-ip> sudo mkdir -p /etc/rancher/k3s
ssh andy@192.168.122.<node-ip> sudo tee /etc/rancher/k3s/registries.yaml <<EOF
mirrors:
  "192.168.122.218:30500":
    endpoint:
      - "http://192.168.122.218:30500"
EOF
ssh andy@192.168.122.<node-ip> sudo systemctl restart k3s-agent
```

## ⚠️ Known Issue: Registry Loses Images on Restart
The local registry uses `emptyDir` storage — images are lost every time the registry pod restarts.
**Fix:** Change registry to use a persistent volume. See `kubernetes/deployments/local-registry.yaml`.

## Affected Images
| Image | Source |
|-------|--------|
| `192.168.122.218:30500/pylab:latest` | Built by GitLab CI from pylab repo |
| `192.168.122.218:30500/trengo-search:latest` | Built by GitLab CI |

## Verify Recovery
```bash
kubectl get pods -A | grep -E "ImagePull|ErrImage"
# Should be empty
```
