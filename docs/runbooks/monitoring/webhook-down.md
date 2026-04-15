# Alertmanager Webhook Down

## Alert
- `AlertmanagerFailedToSendAlerts` (severity: warning) — Alertmanager cannot POST to the webhook receiver

## Symptoms
- Alertmanager logs repeat: `notify retry canceled after N attempts: Post "<url>": EOF`
- Slack receives no alert notifications
- GitLab incidents are not being auto-created
- Alertmanager UI shows receiver errors under Status → Receivers

## Triage

### 1. Is the webhook pod running?
```bash
kubectl get pods -n monitoring -l app=alertmanager-webhook
kubectl logs -n monitoring deployment/alertmanager-webhook --tail=30
```

### 2. EOF on every request — pod starts but crashes on first request
The most common cause is a `NameError` for an undefined config variable in `webhook.py`.
The pod starts (Python import succeeds) but dies when the first request hits
a line that references the undefined name.

```bash
# Look for NameError / undefined variable in the traceback
kubectl logs -n monitoring deployment/alertmanager-webhook --tail=50 | grep -A3 "NameError\|not defined"
```

Known variables that must be defined in `webhook.py` (check `scripts/webhook.py` lines 12–32):

| Variable | Default |
|---|---|
| `GITLAB_URL` | `http://192.168.122.230:8929` |
| `GITLAB_TOKEN` | `''` |
| `GITLAB_PROJECT_ID` | `'1'` |
| `SSH_KEY` | `/root/.ssh/id_rsa` |
| `SSH_USER` | `andy` |
| `K3S_CONTROL_IP` | `192.168.122.218` |
| `HYPERVISOR_IP` | `192.168.122.1` |
| `REGISTRY_URL` | `http://192.168.122.218:30500` |
| `ALERTMANAGER_URL` | `http://192.168.122.218:30093` |
| `PROMETHEUS_URL` | `http://192.168.122.218:30090` |
| `ANTHROPIC_API_KEY` | `''` |
| `SLACK_BOT_TOKEN` | `''` |
| `SLACK_SIGNING_SECRET` | `''` |
| `SLACK_INCIDENTS` | `#incidents` |
| `SLACK_CRITICAL_URL` | `''` |
| `SLACK_WARNING_URL` | `''` |
| `SLACK_INFO_URL` | `''` |

### 3. Pod is not running at all
```bash
# Check for ImagePullError or CrashLoopBackOff
kubectl describe pod -n monitoring -l app=alertmanager-webhook
```

### 4. Connectivity — pod is healthy but Alertmanager still fails
```bash
# Confirm the Service resolves inside the cluster
kubectl get svc -n monitoring alertmanager-webhook

# Test from another pod
kubectl run tmp --rm -it --image=alpine --restart=Never -- \
  wget -qO- http://alertmanager-webhook.monitoring.svc.cluster.local:8080/
```

## Recovery Steps

### Fix a missing config variable
```bash
# Edit webhook.py locally, add the missing variable in the config block (lines 12–32):
# VAR_NAME = os.environ.get('VAR_NAME', 'default-value')

# Rebuild the ConfigMap and redeploy (run from repo root):
{
  echo '---'
  echo 'apiVersion: v1'
  echo 'kind: ConfigMap'
  echo 'metadata:'
  echo '  name: alertmanager-webhook'
  echo '  namespace: monitoring'
  echo 'data:'
  echo '  webhook.py: |'
  sed 's/^/    /' scripts/webhook.py
  echo ''
  awk '/^---$/{found++} found>=2{print}' scripts/webhook-deployment.yaml
} > /tmp/webhook-new.yaml

kubectl apply -f /tmp/webhook-new.yaml
kubectl rollout restart deployment/alertmanager-webhook -n monitoring
kubectl rollout status deployment/alertmanager-webhook -n monitoring --timeout=90s
```

### Restart the pod (transient crash)
```bash
kubectl rollout restart deployment/alertmanager-webhook -n monitoring
kubectl rollout status deployment/alertmanager-webhook -n monitoring
```

## Verify Recovery
```bash
# Pod should be Running with 0 restarts
kubectl get pods -n monitoring -l app=alertmanager-webhook

# Logs should show startup and no tracebacks
kubectl logs -n monitoring deployment/alertmanager-webhook --tail=10

# Alertmanager should stop the EOF retry loop within ~60s
kubectl logs -n monitoring alertmanager-monitoring-kube-prometheus-alertmanager-0 \
  -c alertmanager --tail=10 | grep -v EOF
```

## How to prevent missing variables
After any edit to `webhook.py`, run the AST scan before deploying:
```bash
python3 -c "
import ast

src = open('scripts/webhook.py').read()
tree = ast.parse(src)

assigned = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name): assigned.add(t.id)
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)): assigned.add(node.name)
    if isinstance(node, ast.Import):
        for a in node.names: assigned.add(a.asname or a.name.split('.')[0])
    if isinstance(node, ast.ImportFrom):
        for a in node.names: assigned.add(a.asname or a.name)

missing = {n for node in ast.walk(tree)
           if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
           for n in [node.id]
           if (n.isupper() or (n.replace('_','').isupper() and '_' in n))
           and n not in assigned and not n.startswith('__')}

print('Undefined CAPS vars:', sorted(missing) or 'none — OK')
"
```

## Related Alerts
- `AlertmanagerFailedToSendAlerts` — this runbook
- `PodCrashLooping` — if the webhook pod itself is in CrashLoopBackOff
