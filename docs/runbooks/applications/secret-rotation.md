# Secret Rotation

## When to Rotate

- Suspected credential compromise
- Team member departure
- Scheduled rotation policy (quarterly recommended)
- After any production incident involving credential exposure

---

## Secrets Inventory

| Secret Name | Namespace | Keys | Used By |
|---|---|---|---|
| `pylab-secrets` | pylab | `DB_URL`, `API_KEY` | pylab deployment |
| `vaultwarden-push` | vaultwarden | `installation-id`, `installation-key` | vaultwarden push notifications |
| `webhook-ssh-key` | monitoring, kube-system | `id_rsa`, `known_hosts` | webhook + drift check (monitoring), backup cronjobs (kube-system) |
| `alertmanager-token` | monitoring | `token` | Alertmanager → webhook bearer auth, drift check |
| `registry-auth` | registry | `htpasswd` | registry basic auth |

The kubeadm bootstrap token used to join nodes is not a stored secret: it
expires after 24 hours and a new one is minted by `kubeadm token create` (or
the worker play of `playbooks/kubeadm-bootstrap.yml`) when a node is joined.

---

## Rotation Procedures

### pylab-secrets

```bash
# 1. Generate new values
NEW_API_KEY=$(openssl rand -hex 32)
NEW_DB_PASS=$(openssl rand -base64 20 | tr -dc 'a-zA-Z0-9' | head -c 20)

# 2. Update database password (if rotating DB_URL)
ssh labadmin@192.168.122.230 "sudo -u postgres psql -c \
  \"ALTER USER pylab WITH PASSWORD '$NEW_DB_PASS';\""

# 3. Update Kubernetes secret
kubectl create secret generic pylab-secrets \
  --namespace pylab \
  --from-literal=DB_URL="postgresql://pylab:${NEW_DB_PASS}@postgres.pylab.svc:5432/pylab" \
  --from-literal=API_KEY="${NEW_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Roll the deployment to pick up new secret
kubectl rollout restart deployment/pylab -n pylab
kubectl rollout status deployment/pylab -n pylab --timeout=60s
```

Verify:
```bash
kubectl get pods -n pylab   # All Running
curl http://pylab.local/ready  # 200 OK
```

### webhook-ssh-key

The webhook pod and the drift check (namespace `monitoring`) and the backup
cronjobs (namespace `kube-system`) use this key to SSH into the cluster nodes,
the infra VM and the hypervisor. A pod can only mount a Secret from its own
namespace, so the Secret exists once per namespace and both copies are rotated
together. The Secret also carries `known_hosts`, the pinned host keys the pods
verify against (`SSH_KNOWN_HOSTS=/root/.ssh/known_hosts`). Regenerate it with
`scripts/kubeadm-known-hosts.sh` on the hypervisor whenever a VM or the
hypervisor gets new host keys, then re-apply the Secrets.

```bash
# 1. Generate new key pair on the hypervisor
ssh-keygen -t ed25519 -f /tmp/webhook-new-key -N "" -C "homelab@ansible-$(date +%Y%m)"

# 2. Add new public key to authorized_keys on all machines BEFORE removing old key
for HOST in 192.168.122.240 192.168.122.241 192.168.122.242 192.168.122.230 192.168.122.220; do
  ssh-copy-id -i /tmp/webhook-new-key.pub labadmin@$HOST
done
# Also add to hypervisor itself (for virsh/docker commands)
cat /tmp/webhook-new-key.pub >> ~/.ssh/authorized_keys

# 3. Update the Kubernetes Secret in both namespaces (webhook: monitoring, cronjobs: kube-system)
./scripts/kubeadm-known-hosts.sh   # refresh ansible/inventory/known_hosts
for NS in monitoring kube-system; do
  kubectl create secret generic webhook-ssh-key \
    --namespace "$NS" \
    --from-file=id_rsa=/tmp/webhook-new-key \
    --from-file=known_hosts=ansible/inventory/known_hosts \
    --dry-run=client -o yaml | kubectl apply -f -
done

# 4. Restart webhook deployment
kubectl rollout restart deployment/alertmanager-webhook -n monitoring
kubectl rollout status deployment/alertmanager-webhook -n monitoring --timeout=60s

# 5. Remove OLD public key from authorized_keys on all machines
# Get old key fingerprint first:
OLD_FINGERPRINT=$(kubectl get secret webhook-ssh-key -n monitoring \
  -o jsonpath='{.data.id_rsa}' | base64 -d | ssh-keygen -y -f /dev/stdin | \
  ssh-keygen -lf /dev/stdin)
# Then on each machine, remove it from authorized_keys manually or via:
for HOST in 192.168.122.240 192.168.122.241 192.168.122.242 192.168.122.230 192.168.122.220; do
  echo "Remove old key from $HOST manually"
done

# 6. Clean up temp files
rm /tmp/webhook-new-key /tmp/webhook-new-key.pub
```

### alertmanager-token

```bash
NEW_TOKEN=$(openssl rand -hex 32)
kubectl create secret generic alertmanager-token --namespace monitoring \
  --from-literal=token="$NEW_TOKEN" --dry-run=client -o yaml | kubectl apply -f -
# Put the same value in monitoring/values/kube-prometheus-stack.yaml
# (alertmanager.config receivers → webhook_configs → http_config.authorization.credentials)
# and upgrade the release, then restart the webhook:
kubectl rollout restart deployment/alertmanager-webhook -n monitoring
```

### registry-auth (htpasswd)

```bash
# 1. Generate new htpasswd entry
NEW_PASS=$(openssl rand -base64 16)
HTPASSWD=$(htpasswd -nbB registry "$NEW_PASS")

# 2. Update secret
kubectl create secret generic registry-auth \
  --namespace registry \
  --from-literal=htpasswd="$HTPASSWD" \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Restart registry
kubectl rollout restart deployment/registry -n registry

# 4. Update the containerd registry config on ALL nodes with the new credentials.
# The k8s_common role renders /etc/containerd/certs.d/<registry>/hosts.toml
# (ansible/roles/k8s_common/templates/registry-hosts.toml.j2); re-run it:
cd ansible && ansible-playbook -i inventory/kubeadm.ini playbooks/kubeadm-bootstrap.yml --tags common
```

---

## After Any Rotation

```bash
# Verify no pods are in CrashLoopBackOff or Init:Error
kubectl get pods -A | grep -vE "Running|Completed"

# Check recent events for secret-related errors
kubectl get events -A --sort-by='.lastTimestamp' | grep -i "secret\|forbidden\|unauthorized" | tail -20
```

## Related

- `pod-crashloop.md` — if rotation causes pods to crash
- `registry-down.md` — if registry auth breaks image pulls
