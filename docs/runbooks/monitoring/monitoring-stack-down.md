# Monitoring Stack Down

## Symptoms
- Grafana unreachable at http://grafana.homelab.local
- Alertmanager unreachable at http://alertmanager.homelab.local
- Prometheus unreachable at http://prometheus.homelab.local
- No Slack alerts firing for real incidents

## Root Cause Checklist
- [ ] Are the monitoring pods running?
  ```bash
  kubectl get pods -n monitoring -o wide
  ```
- [ ] Is the node they run on Ready?
  ```bash
  kubectl get nodes
  ```
- [ ] Is Traefik up and does the hostname resolve to the MetalLB address (192.168.122.200)?
  ```bash
  kubectl get pods -n traefik
  kubectl get svc -n traefik
  getent hosts grafana.homelab.local
  ```
- [ ] Are the NFS-backed volumes bound? (Grafana and Loki keep state on NFS)
  ```bash
  kubectl get pvc -n monitoring
  ```

## Recovery Steps

### A node is down
See [Worker Node Not Ready](../nodes/worker-node-not-ready.md). Pods reschedule
once the node is back or drained.

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

### Hostname does not resolve
Add the `*.homelab.local` entries pointing at `192.168.122.200` to `/etc/hosts`
or the lab DNS (see `ansible/README.md`).

## Verify Recovery
```bash
kubectl get pods -n monitoring
# All Running
curl -s http://prometheus.homelab.local/-/healthy
curl -s http://alertmanager.homelab.local/-/healthy
```

## Note
If the whole cluster is down, Prometheus can't send alerts — you won't receive any Slack notifications during the outage. Check Grafana manually if alerts stop completely.
