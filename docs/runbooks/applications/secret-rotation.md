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
| `vaultwarden-tls` | vaultwarden | `cert.pem`, `key.pem` | nginx sidecar TLS |
| `webhook-ssh-key` | kube-system | `id_rsa` | webhook, backup cronjobs |
| `registry-auth` | registry | `htpasswd` | registry basic auth |

---

## Rotation Procedures

### pylab-secrets

```bash
# 1. Generate new values
NEW_API_KEY=$(openssl rand -hex 32)
NEW_DB_PASS=$(openssl rand -base64 20 | tr -dc 'a-zA-Z0-9' | head -c 20)

# 2. Update database password (if rotating DB_URL)
ssh andy@192.168.122.230 "sudo -u postgres psql -c \
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

### vaultwarden-tls (Self-signed cert renewal)

```bash
# 1. Generate new self-signed cert (2 year validity)
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout /tmp/vaultwarden-key.pem \
  -out /tmp/vaultwarden-cert.pem \
  -days 730 \
  -subj "/CN=vaultwarden.local" \
  -addext "subjectAltName=DNS:vaultwarden.local,IP:192.168.122.218"

# 2. Update the secret
kubectl create secret generic vaultwarden-tls \
  --namespace vaultwarden \
  --from-file=cert.pem=/tmp/vaultwarden-cert.pem \
  --from-file=key.pem=/tmp/vaultwarden-key.pem \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Restart vaultwarden to pick up new cert
kubectl rollout restart deployment/vaultwarden -n vaultwarden
kubectl rollout status deployment/vaultwarden -n vaultwarden --timeout=60s

# 4. Clean up
rm /tmp/vaultwarden-{cert,key}.pem
```

Verify:
```bash
curl -k https://192.168.122.218:30900/  # 200 OK
openssl s_client -connect 192.168.122.218:30900 </dev/null 2>/dev/null | \
  openssl x509 -noout -dates  # Check new expiry
```

### webhook-ssh-key

The webhook pod and all backup cronjobs use this key to SSH into cluster nodes and the hypervisor.

```bash
# 1. Generate new key pair on the hypervisor
ssh-keygen -t ed25519 -f /tmp/webhook-new-key -N "" -C "homelab@ansible-$(date +%Y%m)"

# 2. Add new public key to authorized_keys on all nodes BEFORE removing old key
for HOST in 192.168.122.218 192.168.122.219 192.168.122.221 192.168.122.230; do
  ssh-copy-id -i /tmp/webhook-new-key.pub andy@$HOST
done
# Also add to hypervisor itself (for virsh/docker commands)
cat /tmp/webhook-new-key.pub >> ~/.ssh/authorized_keys

# 3. Update Kubernetes secret
kubectl create secret generic webhook-ssh-key \
  --namespace kube-system \
  --from-file=id_rsa=/tmp/webhook-new-key \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Restart webhook deployment
kubectl rollout restart deployment/webhook -n kube-system
kubectl rollout status deployment/webhook -n kube-system --timeout=60s

# 5. Remove OLD public key from authorized_keys on all nodes
# Get old key fingerprint first:
OLD_FINGERPRINT=$(kubectl get secret webhook-ssh-key -n kube-system \
  -o jsonpath='{.data.id_rsa}' | base64 -d | ssh-keygen -y -f /dev/stdin | \
  ssh-keygen -lf /dev/stdin)
# Then on each node, remove it from authorized_keys manually or via:
for HOST in 192.168.122.218 192.168.122.219 192.168.122.221 192.168.122.230; do
  echo "Remove old key from $HOST manually"
done

# 6. Clean up temp files
rm /tmp/webhook-new-key /tmp/webhook-new-key.pub
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

# 4. Update /etc/rancher/k3s/registries.yaml on ALL nodes with new credentials
# See: kubernetes/deployments/local-registry.yaml for the format
for HOST in 192.168.122.218 192.168.122.219 192.168.122.221; do
  ssh andy@$HOST "sudo sed -i 's/password: .*/password: \"$NEW_PASS\"/' \
    /etc/rancher/k3s/registries.yaml && sudo systemctl restart k3s"
done
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
