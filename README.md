# homelab-infrastructure

A self-hosted DevOps lab running on KVM/libvirt. Everything is defined as code — VMs, cluster config, monitoring, CI/CD pipelines, and alerting.

---

## What this demonstrates

- **Full infrastructure-as-code lifecycle** — VM provisioning through Terraform, configuration via Ansible, container orchestration on k3s
- **Production-grade observability** — Prometheus, Grafana, Loki, Alertmanager with custom dashboards per service
- **Complete incident management pipeline** — alert fires → Slack notification with runbook link → GitLab issue auto-created → auto-closed on resolution
- **GitOps CI/CD with 9 stages** including Trivy container scanning and Gitleaks secret detection
- **Self-healing automation** — crashloop recovery cronjobs, health check scripts with auto-fix
- **Custom Python control plane** — TUI and menu-driven interface for full lab management

---

## Screenshots

**Cluster & App**

![k3s control plane](docs/images/k3s-control-plane.png)
![API dashboard](docs/images/api-dashboard.png)
![Trengo app](docs/images/trengo-app.png)

**Control & Health**

![Control Panel TUI](docs/images/control-panel-tui.png)
![check-lab.sh output](docs/images/check-lab-output.png)

**CI/CD & GitLab**

![Pipeline visualized](docs/images/pipeline-visualized.png)
![Pipeline validation with job code](docs/images/pipeline-validation.png)
![Merge requests with checklists](docs/images/merge-requests-checklists.png)
![GitLab Issues from Alertmanager](docs/images/gitlab-issues-alertmanager.png)

**Alerting**

![Slack alert](docs/images/slack-alert.png)

---

## What's in here

| Layer | Tech |
|---|---|
| VM provisioning | Terraform + libvirt provider |
| Configuration management | Ansible |
| Container orchestration | k3s |
| CI/CD | GitLab CE (self-hosted) |
| Monitoring | Prometheus + Grafana + Loki + Alertmanager |
| Secrets | Vaultwarden |
| Registry | Local Docker registry in-cluster |

---

## Cluster layout

```
Host machine (KVM/libvirt, 32GB RAM)
├── k3s-control    .218   2 vCPU / 2GB   control plane
├── k3s-worker-1   .219   2 vCPU / 2GB   workloads
├── k3s-worker-2   .221   2 vCPU / 2GB   workloads (dynamic, Terraform-managed)
└── k3s-infra      .230   2 vCPU / 8GB   GitLab CE + monitoring stack + NFS
```

All IPs are in the default libvirt NAT range (`192.168.122.0/24`). Set `base_ip_octet` in `terraform.tfvars` if yours is different.

Additional workers (k3s-worker-3, ...) are spun up on demand via Terraform. The GitLab runner is embedded in k3s-infra rather than a dedicated VM.

---

## Repo structure

```
ansible/
  inventory/         hosts files for k3s and kubeadm clusters
  playbooks/         bootstrap, hardening, app installs

kubernetes/
  deployments/       k8s manifests (registry, NFS, pylab, vaultwarden...)
  self-healing-cronjobs.yaml

monitoring/
  fix-values.yaml              Helm overrides for kube-prometheus-stack
  grafana/                     dashboards, alert rules, datasources, silences
  promtail-values.yaml

scripts/
  lab-control.py    main control panel — start/stop VMs, deploy, run scenarios
  lab-tui.py        Textual TUI version of the above
  lab-manager.py    lower-level VM management helpers
  check-lab.sh      health check + auto-fix script
  deploy.sh         build and push the trengo-search app
  webhook.py        Alertmanager → GitLab issues bridge
  sync-dashboards.sh
  setup-vault.sh

terraform/          k3s worker VMs (dynamic scale)
terraform-kubeadm/  standalone kubeadm cluster (separate experiment)
```

---

## Getting started

### Prerequisites

- KVM/libvirt on the host
- Terraform >= 1.0
- Ansible >= 2.12
- `kubectl`, `k3s`, `virsh` on PATH

### 1. Provision VMs

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# edit terraform.tfvars — set your SSH public key and desired worker count
cd terraform
terraform init && terraform apply
```

### 2. Bootstrap the cluster

```bash
cd ansible
ansible-playbook -i inventory/homelab.ini playbooks/bootstrap.yml
ansible-playbook -i inventory/homelab.ini playbooks/install-apps.yml
```

### 3. Deploy monitoring

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f monitoring/fix-values.yaml
```

### 4. Control panel

```bash
python3 scripts/lab-control.py   # menu-driven
python3 scripts/lab-tui.py       # TUI (requires: pip install textual)
```

---

## Secrets

Secrets (`K3S_TOKEN`, `GITLAB_TOKEN`, ...) are pulled from environment variables or Vaultwarden via the Bitwarden CLI. See `scripts/load-secrets.sh` for the bootstrap flow and `scripts/setup-vault.sh` to set Vaultwarden up from scratch.

---

## Alerting

Alertmanager fires webhooks at `webhook.py` which opens/closes GitLab issues automatically. Alert rules live in `monitoring/grafana/homelab-alerts.yaml`.

---

## CI/CD

`.gitlab-ci.yml` runs on the self-hosted runner. Pipeline stages: build → scan (Trivy + Gitleaks) → deploy to k3s → smoke test.
