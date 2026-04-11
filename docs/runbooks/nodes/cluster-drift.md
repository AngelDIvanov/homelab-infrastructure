# Cluster Drift Detected

## Alert
`ClusterDrift` (severity: critical)

## Symptoms
- A VM exists in virsh but is absent from `kubectl get nodes`
- Node is running but k3s-agent never joined the cluster
- Possible causes: wrong K3S_TOKEN, stale certificates, agent crash on boot

## Re-run the Check Immediately

Don't wait 5 minutes for the next scheduled run:

```bash
kubectl create job --from=cronjob/cluster-drift-check drift-manual -n monitoring
kubectl logs -n monitoring -l job-name=drift-manual -f
```

## Root Cause Checklist

- [ ] Which node is affected?
  ```bash
  virsh list --all
  kubectl get nodes
  ```

- [ ] Is the VM running?
  ```bash
  virsh start <node-name>   # if shut off
  ```

- [ ] Is k3s-agent running on the node?
  ```bash
  ssh andy@<ip> sudo systemctl status k3s-agent
  ```

- [ ] What is the agent error?
  ```bash
  ssh andy@<ip> sudo journalctl -u k3s-agent -n 20 --no-pager
  ```

## Recovery Steps

### Invalid token (`invalid token format` in agent logs)

The token in the agent env file must match the **full** token from the control plane,
including the `::server:<secret>` suffix. A common mistake is the token being stored
without the suffix (e.g. set during initial provisioning before the server token was
fully written).

```bash
# Get the correct token from control plane
ssh andy@192.168.122.218 sudo cat /var/lib/rancher/k3s/server/node-token
# Expected format: K10<hash>::server:<secret>

# Check what token the agent is using — compare carefully
ssh andy@<node-ip> sudo cat /etc/systemd/system/k3s-agent.service.env
# If it's missing ::server:<secret> that's the problem

# Update the token on the node
CORRECT_TOKEN="<full token including ::server: suffix>"
ssh andy@<node-ip> "sudo sed -i \"s|K3S_TOKEN=.*|K3S_TOKEN='${CORRECT_TOKEN}'|\" /etc/systemd/system/k3s-agent.service.env"
ssh andy@<node-ip> sudo systemctl daemon-reload
ssh andy@<node-ip> sudo systemctl restart k3s-agent
```

**Root cause (2026-04-11):** k3s-worker-2 was provisioned with a truncated token —
the `::server:` suffix was missing. Token must be copied in full from
`/var/lib/rancher/k3s/server/node-token` on the control plane.

**Also:** running `curl -sfL https://get.k3s.io | ... sh -s - agent` to upgrade k3s
**wipes `/etc/systemd/system/k3s-agent.service.env`**, removing K3S_TOKEN and K3S_URL.
Always restore the env file and restart the agent after an in-place upgrade:

```bash
TOKEN=$(ssh andy@192.168.122.218 sudo cat /var/lib/rancher/k3s/server/node-token)
printf 'K3S_TOKEN=%s\nK3S_URL=https://192.168.122.218:6443\n' "$TOKEN" \
  | sudo tee /etc/systemd/system/k3s-agent.service.env
sudo systemctl daemon-reload && sudo systemctl restart k3s-agent
```

### Stale certificates (`certificate signed by unknown authority`)

```bash
ssh andy@<node-ip> sudo systemctl stop k3s-agent
ssh andy@<node-ip> sudo rm -f /var/lib/rancher/k3s/agent/client-ca.crt
ssh andy@<node-ip> sudo rm -f /var/lib/rancher/k3s/agent/server-ca.crt
ssh andy@<node-ip> sudo systemctl start k3s-agent
```

### Agent crash / never started

```bash
ssh andy@<node-ip> sudo systemctl restart k3s-agent
ssh andy@<node-ip> sudo journalctl -u k3s-agent -f --no-pager
```

## Verify Recovery

Re-run the drift check to confirm all nodes are back:

```bash
kubectl create job --from=cronjob/cluster-drift-check drift-verify -n monitoring
kubectl logs -n monitoring -l job-name=drift-verify -f
# Expected: "All nodes healthy — no drift detected."
```

Then verify in kubectl:
```bash
kubectl get nodes
# All nodes should show Ready
```

## Node IP Reference

| Node | IP | Role |
|---|---|---|
| k3s-control | 192.168.122.218 | control-plane |
| k3s-worker-1 | 192.168.122.219 | worker |
| k3s-worker-2 | 192.168.122.221 | worker |
| k3s-infra | 192.168.122.230 | infra |

## Related Alerts
- `K3sWorkerNodeDown` — fired by kube-state-metrics when a joined node goes NotReady
- `KubeNodeNotReady` — fired if k3s-infra goes NotReady
