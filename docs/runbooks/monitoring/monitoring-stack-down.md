# Monitoring Stack Down

## Symptoms
- Grafana unreachable at http://192.168.122.218:30080
- Alertmanager unreachable at http://192.168.122.218:30093
- Prometheus unreachable at http://192.168.122.218:30090
- No Slack alerts firing for real incidents

## Root Cause Checklist
- [ ] Is k3s-infra VM running?
  ```bash
  virsh list --all | grep k3s-infra
  ```
- [ ] Are monitoring pods running?
  ```bash
  kubectl get pods -n monitoring
  ```
- [ ] Is there a node issue on k3s-infra?
  ```bash
  kubectl describe node k3s-infra
  ```

## Recovery Steps

### k3s-infra VM is down
```bash
virsh start k3s-infra
# Wait ~60s for VM to boot and pods to restart
kubectl get pods -n monitoring -w
```

### Pods are crashing
```bash
# Restart the monitoring stack
kubectl rollout restart deployment -n monitoring
kubectl rollout restart statefulset -n monitoring
```

### Prometheus out of memory
```bash
kubectl patch prometheus monitoring-kube-prometheus-prometheus -n monitoring \
  --type merge -p '{"spec":{"retention":"3d","resources":{"limits":{"memory":"700Mi"}}}}'
```

## Verify Recovery
```bash
kubectl get pods -n monitoring
# All Running
curl -s http://192.168.122.218:30090/-/healthy
curl -s http://192.168.122.218:30093/-/healthy
```

## Note
If k3s-infra goes down, Prometheus can't send alerts — you won't receive any Slack notifications during the outage. Check Grafana manually if alerts stop completely.
