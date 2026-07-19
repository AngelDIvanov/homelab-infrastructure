# AGENTS.md — homelab-infrastructure

Instructions for AI coding assistants working in this repo.

## What this is

A self-hosted DevOps lab on **KVM/libvirt**, fully defined as code: VM provisioning,
k3s cluster config, monitoring, CI/CD, alerting, and a Claude-driven auto-healing
loop. `README.md` has the full feature tour and screenshots — read it for context
before touching infra.

## Layout

| Path | Purpose |
|---|---|
| `terraform-kubeadm/` | Terraform + libvirt VM provisioning. Holds live `terraform.tfstate` — never edit by hand. |
| `ansible/` | Config management. `ansible.cfg`, `inventory/`, `playbooks/`. |
| `kubernetes/` | k3s manifests: deployments, services, monitoring, backup, policies, namespaces, self-healing. |
| `monitoring/` | Prometheus / Grafana / Loki / promtail values. |
| `scripts/` | Python control plane (`lab-manager.py`, `lab-control.py`, `lab-tui.py`, `webhook.py`) + bash ops scripts. |
| `docs/` | Runbooks, postmortems, audits, images. |
| `.gitlab/` | Self-hosted GitLab CI pipeline definitions. |

## Stack

Terraform (libvirt provider) · Ansible · k3s (SQLite, 12h snapshots) · GitLab CE
(self-hosted) · Prometheus + Grafana + Loki (31d) + Alertmanager · Vaultwarden
(secrets) · local Docker registry (NFS-backed) · Python control plane.

## Running things

```bash
./scripts/check-lab.sh            # health check (read-only)
./scripts/check-lab.sh --fix      # health check + in-place auto-fix
./scripts/check-lab.sh --restart  # graceful VM restart + auto-fix
./scripts/deploy.sh               # deploy stage
python3 scripts/lab-tui.py        # TUI control panel
```

`check-lab.sh` emits structured logfmt and suppresses ANSI when not a TTY (so
CronJob/Loki capture stays clean). Don't add raw color codes to log paths.

## Gotchas

- **Never hand-edit `terraform-kubeadm/terraform.tfstate`** or the `.terraform/`
  dir. Use `terraform` commands.
- **Secrets live in Vaultwarden**, not in the repo. Preserve image SHA pinning
  wherever it exists and keep NetworkPolicy default-deny when adding workloads.
- **k3s uses SQLite**, not etcd. Backups are SQLite snapshots; respect the backup
  CronJobs in `kubernetes/backup/`.
- CI runs lint → validate (kubeconform + promtool) → security scan (gitleaks +
  kubesec) → deploy → smoke test. Keep manifests passing kubeconform/promtool.

## Secrets

Never log, echo, print, or commit Vaultwarden secrets, kubeconfigs, GitLab tokens,
or `.env` values. Remote URLs may carry embedded GitLab tokens — never paste them
into output or commits.

Before any public GitHub release, verify the repo is clean across history, all
branches, commit messages, and the working tree. These paths/patterns must stay
ignored and untracked; only `.example` templates are allowed in git:

- `gitlab`, `gitlab.pub`, `*.pem`, `id_rsa*`, `id_ed25519*`, `*.key`
- `scripts/alertmanager.yaml`, `**/init.json`, `*token*.txt`, `.secrets`
- `kubernetes/deployments/grafana-gitlab-secret.yaml`
- `kubernetes/deployments/alertmanager-config.yaml`

Before making the repo public, confirm `git ls-files` returns no entries for the
paths above and scan all refs for real-token patterns such as GitLab PATs, Slack
webhooks/tokens, k3s join tokens, private key blocks, AWS keys, and Google API
keys. If any branch tip tracks those files, remove them from that branch before
publishing.

## Local operational memory

If Angel asks to rotate the k3s token, run this local script:

```bash
bash ~/k3s-rotate-token.sh
```

Treat this as a one-prompt trigger for phrases like "rotate k3s token", "k3s token rotation", or "run k3s rotate token". Do not print, echo, log, or summarize any token values from the script output. Report only success/failure, changed components, and safe next steps.

GitHub CLI is authenticated on this host:

```text
gh auth status completed successfully for github.com
git protocol: https
logged in account: AngelDIvanov
```

Use `gh` for GitHub operations when helpful, but never print `gh auth token`, OAuth tokens, PATs, or credential helper contents.

Session summary learned from `~/HOMELAB-SUMMARY-2026-06-14.md`:

- Public repo: `github.com/AngelDIvanov/homelab-infrastructure` is public after secret cleanup/history verification.
- GitHub secret scanning and push protection are enabled for `homelab-infrastructure`.
- k3s join token was rotated cluster-wide with `~/k3s-rotate-token.sh`; old token revoked; all 4 nodes verified Ready afterward.
- Keep `/var/lib/rancher/k3s/server/token.bak-rotate` on control if any pre-rotation k3s snapshot might be restored, because it may be needed to decrypt older snapshots.
- Agent `.env` files contain the new k3s token, so next k3s stop/start should come up clean.
- `GITLAB_TOKEN` in shell env or `.bashrc` may still be the revoked old PAT; replace it before future GitLab API work.
- Helper scripts `~/k3s-rotate-token.sh` and `~/close-trengo-mrs.sh` are kept as local runbooks and should not contain secrets.
- Trengo cleanup was completed: MRs !50 and !52 closed, remote `design/kimi` and `design/gpt` removed, local design worktrees/branches removed, winner was Opus MR !51.
- When Angel is using z-ai, use the ZAI Coding Plan subscription (not API key pay-per-token).

## Code style

- No emojis in code, comments, or commit messages unless asked.
- Default to no comments; only explain non-obvious *why*.
- Edit existing files over creating new ones.
