# Node Memory Pressure

## Alerts
- `NodeMemoryWarning` (>80%, severity: warning)
- `NodeMemoryHigh` (>85%, severity: critical) — auto-remediation triggered
- `NodeMemoryCritical` (>90%, severity: critical) — auto-remediation triggered
- `ContainerOOMKilled` (severity: warning)

## Symptoms
- Node memory usage approaching limit
- Containers being OOM-killed
- System swapping heavily
- New pods failing to schedule

## Root Cause Checklist
- [ ] Which process is consuming memory?
  ```bash
  ssh labadmin@192.168.122.<ip> ps aux --sort=-%mem | head -15
  ```
- [ ] Is GitLab (on the infra VM .230) consuming too much?
  ```bash
  ssh labadmin@192.168.122.230 sudo gitlab-ctl status
  ```
- [ ] Is Sidekiq bloated?
  ```bash
  ssh labadmin@192.168.122.230 sudo gitlab-ctl tail sidekiq 2>/dev/null | tail -5
  ```
- [ ] Is Prometheus retention eating RAM?
  ```bash
  kubectl top pods -n monitoring
  ```

## Recovery Steps

### Infra VM (.230) — GitLab host
```bash
# Restart Sidekiq (often the biggest consumer after long uptime)
ssh labadmin@192.168.122.230 sudo gitlab-ctl restart sidekiq

# Reduce Prometheus retention if still high
kubectl patch prometheus monitoring-kube-prometheus-prometheus -n monitoring \
  --type merge -p '{"spec":{"retention":"3d","resources":{"limits":{"memory":"700Mi"}}}}'
```

### Any node — clear caches
```bash
ssh labadmin@192.168.122.<ip> sudo sync && echo 3 | sudo tee /proc/sys/vm/drop_caches
```

### Any node — prune container images
```bash
ssh labadmin@192.168.122.<ip> sudo crictl rmi --prune
```

## Long-Term Fix
If a node is consistently above 80%, increase VM memory via Terraform:
```bash
# Cluster nodes: edit terraform-kubeadm/terraform.tfvars — control_memory / worker_memory
# Then: cd terraform-kubeadm && terraform apply
# Infra VM: terraform/terraform.tfvars — infra_memory, then cd terraform && terraform apply
# Note: this recreates the VM
```

## Verify Recovery
```bash
kubectl top nodes
ssh labadmin@192.168.122.<ip> free -h
```

## Related Alerts
- `ContainerOOMKilled` — individual container hit its memory limit (separate from node pressure)
- `WorkerNodeDown` — node OOM can cause kubelet to crash
