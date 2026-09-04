# Public GitHub + Secrets/Pipeline Summary — 13-06-2026

## Status

Repository: `homelab-infrastructure`

Public GitHub repo was prepared and published after secret cleanup checks.

Final verified state before publishing:

- Real-token scans across all git history: passed.
- Real-token scans across all branch tips: passed.
- Real-token scans across working tree: passed.
- Commit message scans: passed.
- `main` matched `github/main` after fetch.
- Required secret-bearing files were removed from tracking and added to `.gitignore`.
- Non-main branch tips were cleaned so tracked secret-bearing files no longer appeared there.

No secret values are stored in this document.

## Files and paths that must never be committed

These paths/patterns are ignored in `.gitignore` and must stay untracked:

```gitignore
gitlab
gitlab.pub
*.pem
id_rsa*
id_ed25519*
*.key
scripts/alertmanager.yaml
**/init.json
*token*.txt
.secrets
kubernetes/deployments/grafana-gitlab-secret.yaml
kubernetes/deployments/alertmanager-config.yaml
```

Only `.example` templates are allowed in git for secret manifests.

## Verification commands before any future public release

Run from repo root:

```bash
cd ~/homelab
git fetch github --prune
```

Check ignored/tracked secret paths:

```bash
git ls-files -- \
  gitlab gitlab.pub "*.pem" "id_rsa*" "id_ed25519*" "*.key" \
  scripts/alertmanager.yaml \
  kubernetes/deployments/grafana-gitlab-secret.yaml \
  kubernetes/deployments/alertmanager-config.yaml
```

Expected output: empty.

Scan real-token patterns in history:

```bash
git rev-list --all --objects \
  | awk '{print $1}' \
  | git cat-file --batch-check 2>/dev/null \
  | awk '$2=="blob"{print $1}' \
  | while read o; do
      git cat-file -p "$o" 2>/dev/null \
        | grep -EH 'glpat-[A-Za-z0-9_.-]{15}|K10[a-f0-9]{20}|hooks\.slack\.com/services/T[A-Z0-9]+/B|BEGIN (OPENSSH|RSA) PRIVATE KEY|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20}|xox[baprs]-[0-9A-Za-z-]+' \
        && echo "HIT in $o"
    done
```

Expected output: empty.

Scan branch tips and working tree:

```bash
git grep -I -n -E 'glpat-[A-Za-z0-9_.-]{15}|K10[a-f0-9]{20}|hooks\.slack\.com/services/T[A-Z0-9]+/B|BEGIN (OPENSSH|RSA) PRIVATE KEY|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20}|xox[baprs]-[0-9A-Za-z-]+' $(git for-each-ref --format='%(refname:short)' refs/heads refs/remotes | grep -v '/HEAD$') --

grep -R -I -n -E 'glpat-[A-Za-z0-9_.-]{15}|K10[a-f0-9]{20}|hooks\.slack\.com/services/T[A-Z0-9]+/B|BEGIN (OPENSSH|RSA) PRIVATE KEY|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20}|xox[baprs]-[0-9A-Za-z-]+' . --exclude-dir=.git
```

Expected output: empty.

Scan commit messages:

```bash
git log --all --format='%H %s%n%b' \
  | grep -E 'glpat-[A-Za-z0-9_.-]{15}|K10[a-f0-9]{20}|hooks\.slack\.com/services/T[A-Z0-9]+/B|BEGIN (OPENSSH|RSA) PRIVATE KEY|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20}|xox[baprs]-[0-9A-Za-z-]+'
```

Expected output: empty.

## GitHub branch note

If GitHub shows old active branches and they cannot be deleted, force-update them to clean `main` so they expose the same clean history:

```bash
git push --force-with-lease github main:infra/remove-worker3-opus
git push --force-with-lease github main:infra/remove-worker3-gpt
git push --force-with-lease github main:audit/cluster-and-structure-2026-06-11
```

Then verify all remote branch hashes match `github/main`:

```bash
git fetch github --prune
git rev-parse github/main
git rev-parse github/infra/remove-worker3-opus
git rev-parse github/infra/remove-worker3-gpt
git rev-parse github/audit/cluster-and-structure-2026-06-11
```

## GitLab CI/CD variables

Secrets for pipelines must be stored in self-hosted GitLab, not GitHub and not repo files.

GitLab project CI/CD variables location:

```text
http://192.168.122.230:8929/root/homelab-infrastructure/-/settings/ci_cd
```

Path in UI:

```text
Project -> Settings -> CI/CD -> Variables -> Add variable
```

### Required variable: `KUBECONFIG_BASE64`

Needed by:

- `.gitlab-ci.yml`
- `deploy:kubernetes`
- `deploy:monitoring`

Generate value locally:

```bash
base64 -w0 ~/.kube/config
```

Recommended safer option: create a dedicated service account kubeconfig instead of using personal kubeconfig.

On k3s control node:

```bash
sudo k3s kubectl create serviceaccount gitlab-deploy -n kube-system
sudo k3s kubectl create clusterrolebinding gitlab-deploy-cluster-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=kube-system:gitlab-deploy
sudo k3s kubectl create token gitlab-deploy -n kube-system --duration=8760h
```

The deploy token/kubeconfig needs permissions for:

- Deployments, Services, ConfigMaps
- ServiceAccounts, Roles, RoleBindings
- ClusterRoles, ClusterRoleBindings
- Namespaces
- PersistentVolumes, PersistentVolumeClaims, StorageClass
- CronJobs
- NetworkPolicies

Because current manifests include cluster-scoped resources, `cluster-admin` is the simplest working permission. A narrower role can be built later.

### Optional variable: `SNYK_TOKEN`

Needed by:

- `.gitlab-ci.yml`
- `security:snyk-iac`

If missing, Snyk scan skips.

Recommended GitLab variable settings:

```text
Type: Variable
Environment scope: All
Masked: yes if accepted
Hidden: yes if available
Protected: only if pipelines run only on protected refs
```

## Kubernetes Secrets needed by workloads

Create these in the cluster with `kubectl`. Do not commit them.

### `gitlab-token`

Referenced by:

```text
kubernetes/deployments/alertmanager-webhook.yaml
```

Location:

```text
Namespace: monitoring
Secret: gitlab-token
Key: token
```

Create:

```bash
sudo k3s kubectl create secret generic gitlab-token \
  -n monitoring \
  --from-literal=token='<GITLAB_PAT>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- GitLab PAT with `api` scope.
- User should have project access sufficient to create, update, comment on, and close issues.

### `slack-webhook-urls`

Referenced by:

```text
kubernetes/deployments/alertmanager-webhook.yaml
```

Location:

```text
Namespace: monitoring
Secret: slack-webhook-urls
Keys: critical, warning
```

Create:

```bash
sudo k3s kubectl create secret generic slack-webhook-urls \
  -n monitoring \
  --from-literal=critical='<SLACK_CRITICAL_WEBHOOK_URL>' \
  --from-literal=warning='<SLACK_WARNING_WEBHOOK_URL>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- Slack incoming webhook URLs for target channels.

### `slack-bot-token`

Referenced by:

```text
kubernetes/deployments/alertmanager-webhook.yaml
```

Location:

```text
Namespace: monitoring
Secret: slack-bot-token
Key: token
```

Create:

```bash
sudo k3s kubectl create secret generic slack-bot-token \
  -n monitoring \
  --from-literal=token='<SLACK_BOT_TOKEN>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights/scopes:

- `chat:write`
- `commands` if slash commands are used
- possibly `channels:read`, depending on Slack app config

### `slack-signing-secret`

Referenced by:

```text
kubernetes/deployments/alertmanager-webhook.yaml
```

Location:

```text
Namespace: monitoring
Secret: slack-signing-secret
Key: secret
```

Create:

```bash
sudo k3s kubectl create secret generic slack-signing-secret \
  -n monitoring \
  --from-literal=secret='<SLACK_SIGNING_SECRET>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- No API rights. Used only to verify Slack request signatures.

### `anthropic-api-key`

Referenced by:

```text
kubernetes/deployments/alertmanager-webhook.yaml
```

Location:

```text
Namespace: monitoring
Secret: anthropic-api-key
Key: key
```

Create:

```bash
sudo k3s kubectl create secret generic anthropic-api-key \
  -n monitoring \
  --from-literal=key='<ANTHROPIC_API_KEY>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- Anthropic API key with access to the configured Claude model.

### `webhook-ssh-key`

Referenced by:

```text
kubernetes/deployments/alertmanager-webhook.yaml
kubernetes/monitoring/cluster-drift-cronjob.yaml
kubernetes/backup/gitlab-backup-cronjob.yaml
kubernetes/backup/etcd-snapshot-cronjob.yaml
```

Locations:

```text
Namespace: monitoring
Secret: webhook-ssh-key
Key: id_rsa
```

and if backup/maintenance jobs run in `kube-system`:

```text
Namespace: kube-system
Secret: webhook-ssh-key
Key: id_rsa
```

Create:

```bash
sudo k3s kubectl create secret generic webhook-ssh-key \
  -n monitoring \
  --from-file=id_rsa=/path/to/private/key \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -

sudo k3s kubectl create secret generic webhook-ssh-key \
  -n kube-system \
  --from-file=id_rsa=/path/to/private/key \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- SSH key must allow access as `andy` to required homelab nodes.
- Sudo access is needed for commands such as `sudo k3s kubectl`, `gitlab-backup`, service restarts, and diagnostics.
- This is highly sensitive. Never commit it.

### `grafana-gitlab-token`

Referenced by:

```text
kubernetes/deployments/grafana-gitlab-secret.yaml.example
monitoring/grafana/infinity-datasource.yaml
```

Location:

```text
Namespace: monitoring
Secret: grafana-gitlab-token
Key: GITLAB_TOKEN
```

Create:

```bash
sudo k3s kubectl create secret generic grafana-gitlab-token \
  -n monitoring \
  --from-literal=GITLAB_TOKEN='<GITLAB_PAT>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- GitLab PAT with `read_api` should be enough for dashboards.
- Token user must have access to the project being queried.

### `ngrok-token`

Referenced by:

```text
kubernetes/deployments/ngrok-webhook.yaml
```

Location:

```text
Namespace: monitoring
Secret: ngrok-token
Key: token
```

Create:

```bash
sudo k3s kubectl create secret generic ngrok-token \
  -n monitoring \
  --from-literal=token='<NGROK_AUTHTOKEN>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- Ngrok account authtoken.

### `vaultwarden-push`

Referenced by:

```text
kubernetes/deployments/vaultwarden.yaml
```

Location:

```text
Namespace: vaultwarden
Secret: vaultwarden-push
Keys: installation-id, installation-key
```

Create:

```bash
sudo k3s kubectl create secret generic vaultwarden-push \
  -n vaultwarden \
  --from-literal=installation-id='<VAULTWARDEN_PUSH_INSTALLATION_ID>' \
  --from-literal=installation-key='<VAULTWARDEN_PUSH_INSTALLATION_KEY>' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- Vaultwarden push relay credentials.

### `vaultwarden-tls`

Referenced by:

```text
kubernetes/deployments/vaultwarden.yaml
```

Location:

```text
Namespace: vaultwarden
Secret: vaultwarden-tls
Keys: tls.crt, tls.key
```

Helper script:

```text
scripts/gen-vaultwarden-cert.sh
```

Create with helper:

```bash
cd ~/homelab
scripts/gen-vaultwarden-cert.sh
```

Or manually:

```bash
sudo k3s kubectl create secret tls vaultwarden-tls \
  -n vaultwarden \
  --cert=/path/to/tls.crt \
  --key=/path/to/tls.key \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Rights:

- TLS private key. Never commit it.

## Quick missing-secret checks

Run on k3s control node:

```bash
sudo k3s kubectl get secrets -n monitoring \
  gitlab-token \
  slack-webhook-urls \
  slack-bot-token \
  slack-signing-secret \
  anthropic-api-key \
  webhook-ssh-key \
  grafana-gitlab-token \
  ngrok-token
```

```bash
sudo k3s kubectl get secrets -n vaultwarden \
  vaultwarden-push \
  vaultwarden-tls
```

```bash
sudo k3s kubectl get secret webhook-ssh-key -n kube-system
```

## Homelab runtime status after restart

K3s was started successfully.

Running VMs:

```text
ci-runner
k3s-control
k3s-worker-1
k3s-worker-2
k3s-infra
```

Kubernetes nodes verified Ready:

```text
k3s-control
k3s-infra
k3s-worker-1
k3s-worker-2
```

GitLab verified up:

```text
URL: http://192.168.122.230:8929
HTTP status: 200
GitLab version: 18.8.2
```

GitLab services verified running:

```text
gitaly
gitlab-workhorse
nginx
postgresql
puma
redis
sidekiq
exporters
```

GitLab runner verified running on `ci-runner` with shell executor.

## Important security follow-ups

- Rotate GitLab runner token if it may have been exposed during local command output.
- Rotate any old GitLab PATs, Slack webhooks, and k3s join tokens that were ever committed before history rewrite.
- Enable GitHub secret scanning and push protection if available.
- Keep real credentials in GitLab CI/CD variables, Kubernetes Secrets, or Vaultwarden only.
- Never print `.env`, kubeconfig, runner tokens, PATs, webhooks, SSH private keys, or Kubernetes Secret values.
