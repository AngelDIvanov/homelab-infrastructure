# Cluster Drift Detected

## Alert
`ClusterDrift` (severity: critical)

## Symptoms
- A kubeadm VM is running in virsh but is absent from `kubectl get nodes`
- A node is registered but `NotReady` (kubelet or containerd not running)
- A node is registered but its VM no longer exists on the hypervisor

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
  kubectl get nodes -o wide
  ```

- [ ] Is the VM running?
  ```bash
  virsh start <vm-name>   # if shut off
  ```

- [ ] Are kubelet and containerd running on the node?
  ```bash
  ssh labadmin@<ip> sudo systemctl status kubelet containerd
  ```

- [ ] What does the kubelet log say?
  ```bash
  ssh labadmin@<ip> sudo journalctl -u kubelet -n 30 --no-pager
  ```

## Recovery Steps

### kubelet or containerd stopped

```bash
ssh labadmin@<node-ip> sudo systemctl restart containerd
ssh labadmin@<node-ip> sudo systemctl restart kubelet
kubectl get nodes -w
```

### Node was never joined, or was deleted from the cluster

Join tokens expire after 24 hours; mint a new one on the control node and
join from the worker. Never paste the join command into chat or a ticket, it
carries the bootstrap token.

```bash
# On kubeadm-control: print a fresh join command
ssh labadmin@192.168.122.240 sudo kubeadm token create --print-join-command

# On the worker: reset any half-joined state, then join
ssh labadmin@<node-ip> sudo kubeadm reset -f
ssh labadmin@<node-ip> 'sudo <join command from above>'
```

The same thing, without copying tokens around, is the worker play of the
bootstrap playbook (it mints the token itself):

```bash
cd ansible
ansible-playbook -i inventory/kubeadm.ini playbooks/kubeadm-bootstrap.yml --tags workers --limit <node-name>
```

### Certificate errors in the kubelet log (`certificate signed by unknown authority`)

The node's kubelet client certificate no longer matches the cluster CA, which
happens when the control plane was rebuilt. Reset and re-join the worker as
above.

### VM missing from virsh but node still registered

The VM was destroyed (for example `worker_count` was lowered in
`terraform-kubeadm`). Remove the stale node object:

```bash
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force
kubectl delete node <node-name>
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
| kubeadm-control | 192.168.122.240 | control-plane |
| kubeadm-worker-1 | 192.168.122.241 | worker |
| kubeadm-worker-2 | 192.168.122.242 | worker |

Worker addresses are `worker_ip_offset + N - 1` from `terraform-kubeadm`.

## Related Alerts
- `WorkerNodeDown` — fired by kube-state-metrics when a joined node goes NotReady
- `ControlPlaneNodeNotReady` — fired if kubeadm-control goes NotReady
