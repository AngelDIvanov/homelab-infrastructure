# Homelab review rules

Prioritize correctness, security, recoverability, and safe operations over cosmetic changes.

## Secrets

- Never expose tokens, kubeconfigs, passwords, webhook URLs, private keys, or Kubernetes Secret values in logs, command arguments, generated files, examples, or review text.
- Flag shell commands that interpolate secrets into argv. Prefer process environments with restricted inheritance or stdin-based transfer.
- Only `.example` secret manifests may be tracked. Terraform state, variable files, provider binaries, caches, and local environment files must remain untracked.

## Operational safety

- Health and status commands must be read-only by default. Starting, stopping, deleting, pruning, draining, rolling back, or restarting resources requires an explicit remediation mode or confirmation.
- Graceful shutdown must have a bounded timeout and must not silently escalate to forced power-off.
- Destructive scaling and Terraform changes must check failures and avoid reporting success after partial completion.
- Do not log structured output containing secrets. Preserve logfmt output for non-interactive health checks.

## Infrastructure as code

- Keep Terraform dependency lock files tracked, but never track `.terraform/`, state, plans, or real `terraform.tfvars` files.
- Validate every Kubernetes manifest directory while excluding example-only files from deployment.
- Preserve default-deny NetworkPolicies, least-privilege RBAC, non-root execution, resource limits, probes, and immutable image references.
- CI dependencies and third-party images should use immutable digests. Downloads must use fixed versions and checksum verification.

## Homelab constraints

- This is a single-host KVM/libvirt lab using k3s with SQLite, not an HA production cluster. Do not recommend complexity that conflicts with those documented constraints unless it fixes a concrete issue.
- `k3s-infra` hosts persistent services and must not be stopped by routine k3s or lab shutdown scripts.
- Worker VMs are discovered dynamically; do not reintroduce hardcoded worker counts.
