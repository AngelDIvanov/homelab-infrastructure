#!/usr/bin/env bash
# Pre-push IaC security scan — mirrors the `security:snyk-iac` GitLab CI job so
# you catch Terraform/Kubernetes misconfigurations locally before they reach CI.
#
# Usage:  ./scripts/snyk-iac-scan.sh
# Requires: snyk CLI authenticated (`snyk auth`). Exits non-zero on high+ issues.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v snyk >/dev/null 2>&1; then
  echo "snyk CLI not found — install it or skip the scan." >&2
  exit 0
fi

# Only scan dirs that exist (terraform-kubeadm is optional).
TARGETS=()
for d in terraform terraform-kubeadm kubernetes; do
  [ -d "$d" ] && TARGETS+=("$d")
done

echo "Running Snyk IaC scan on: ${TARGETS[*]}"
snyk iac test "${TARGETS[@]}" --severity-threshold=high
echo "Snyk IaC scan passed (no high+ severity issues)."
