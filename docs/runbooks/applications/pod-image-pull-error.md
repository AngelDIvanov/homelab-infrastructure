# Pod Image Pull Error

## Alert
`PodImagePullError` (severity: critical)

## Symptoms
- Pod status `ImagePullBackOff` or `ErrImagePull`
- `kubectl describe pod` shows: `Failed to pull image ... not found` or `unauthorized`

## Root Cause Checklist
- [ ] Does the image exist in the registry?
  ```bash
  curl http://192.168.122.240:30500/v2/_catalog
  curl http://192.168.122.240:30500/v2/<image>/tags/list
  ```
- [ ] Is the local registry pod running?
  ```bash
  kubectl get pods -n registry
  ```
- [ ] Is the registry mirror configured in containerd on the affected node?
  ```bash
  ssh labadmin@192.168.122.<node-ip> cat /etc/containerd/certs.d/192.168.122.240:30500/hosts.toml
  ```

## Recovery Steps

### Image missing from registry — rebuild and push via CI
```bash
# Trigger the GitLab CI pipeline for the affected project
# Or manually build and push:
docker build -t 192.168.122.240:30500/<image>:latest .
docker push 192.168.122.240:30500/<image>:latest
```

### Registry pod is down — restart it
```bash
kubectl rollout restart deployment/registry -n registry
# Wait for it to come up
kubectl get pods -n registry -w
# Images live on the NFS-backed PVC and survive the restart.
```

### containerd registry config missing on node
The `k8s_common` role renders `/etc/containerd/certs.d/<registry>/hosts.toml`
on every node (`ansible/roles/k8s_common/templates/registry-hosts.toml.j2`).
Re-run it instead of editing the file by hand:
```bash
cd ansible && ansible-playbook -i inventory/kubeadm.ini playbooks/kubeadm-bootstrap.yml --tags common --limit <node-name>
ssh labadmin@192.168.122.<node-ip> sudo systemctl restart containerd
```

## Affected Images
| Image | Source |
|-------|--------|
| `192.168.122.240:30500/pylab:latest` | Built by GitLab CI from pylab repo |
| `192.168.122.240:30500/trengo-search:latest` | Built by GitLab CI |

## Verify Recovery
```bash
kubectl get pods -A | grep -E "ImagePull|ErrImage"
# Should be empty
```
