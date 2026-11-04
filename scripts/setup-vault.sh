#!/bin/bash
# =============================================================================
# Homelab Vault Setup — One-time bootstrap
# =============================================================================
# What this does:
#   1. Checks that Vaultwarden (deployed by ansible/playbooks/kubeadm-workloads.yml)
#      answers at its ingress hostname
#   2. Installs Bitwarden CLI (bw)
#   3. Logs in and seeds vault with all homelab secrets
#   4. Strips secrets from ~/.bashrc and replaces with load-secrets.sh
#
# Usage: bash ~/homelab/scripts/setup-vault.sh
# =============================================================================

set -euo pipefail

SESSION_FILE="$HOME/.config/homelab/.bw_session"
# Served by the Traefik ingress (kubernetes/deployments/vaultwarden.yaml);
# the hostname must resolve to the Traefik load balancer address.
VAULTWARDEN_URL="${VAULTWARDEN_URL:-http://vault.homelab.local}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR ]${NC} $*"; exit 1; }
step()    { echo -e "\n${CYAN}══════════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}══════════════════════════════════════════${NC}"; }

mkdir -p "$(dirname "$SESSION_FILE")"
chmod 700 "$(dirname "$SESSION_FILE")"

# ─── Step 1: Vaultwarden reachable ───────────────────────────────────────────
step "Step 1/5 — Checking Vaultwarden at $VAULTWARDEN_URL"

if curl -fsS --max-time 10 -o /dev/null "$VAULTWARDEN_URL/alive"; then
    success "Vaultwarden is up"
else
    error "Vaultwarden does not answer at $VAULTWARDEN_URL. Deploy it first: ansible-playbook -i inventory/kubeadm.ini playbooks/kubeadm-workloads.yml"
fi

# ─── Step 2: Install bw CLI ──────────────────────────────────────────────────
step "Step 2/5 — Installing Bitwarden CLI"

if command -v bw &>/dev/null; then
    success "bw CLI already installed: $(bw --version)"
else
    info "Downloading bw CLI..."
    BW_VERSION="2024.2.0"
    BW_ZIP="/tmp/bw-linux.zip"
    curl -sL "https://github.com/bitwarden/clients/releases/download/cli-v${BW_VERSION}/bw-linux-${BW_VERSION}.zip" -o "$BW_ZIP"
    unzip -o "$BW_ZIP" -d /tmp/bw-extract
    sudo mv /tmp/bw-extract/bw /usr/local/bin/bw
    sudo chmod +x /usr/local/bin/bw
    rm -rf "$BW_ZIP" /tmp/bw-extract
    success "bw CLI installed: $(bw --version)"
fi

# ─── Step 3: Login to Vaultwarden ───────────────────────────────────────────
step "Step 3/5 — Connecting to Vaultwarden"

info "Pointing bw CLI at your Vaultwarden instance..."
bw config server "$VAULTWARDEN_URL"

if ! bw status 2>/dev/null | grep -q '"status":"unlocked"\|"status":"locked"'; then
    echo ""
    warn "Create your account with a Bitwarden client pointed at $VAULTWARDEN_URL"
    warn "(the web vault needs HTTPS; see ansible/README.md), THEN press Enter to continue."
    read -rp "Press Enter once your account is created..."
fi

info "Logging in..."
bw login 2>/dev/null || true

info "Unlocking vault and saving session..."
export BW_SESSION
BW_SESSION=$(bw unlock --raw)
echo "$BW_SESSION" > "$SESSION_FILE"
chmod 600 "$SESSION_FILE"
success "Session saved to $SESSION_FILE"

# ─── Step 4: Seed vault with secrets ────────────────────────────────────────
step "Step 4/5 — Seeding vault with secrets"

bw sync --quiet

# Helper to create a vault item (skip if it already exists)
create_secret() {
    local name="$1"
    local value="$2"
    if [ -z "$value" ]; then
        warn "  Skipping '$name' — no value provided"
        return
    fi
    if bw get item "$name" &>/dev/null; then
        warn "  '$name' already exists in vault, skipping."
        return
    fi
    local json
    json=$(
        { printf '%s\n' "$name"; printf '%s' "$value"; } |
            python3 -c 'import json, sys; name = sys.stdin.readline().rstrip("\n"); value = sys.stdin.read(); print(json.dumps({"type": 1, "name": name, "login": {"password": value}}))'
    )
    printf '%s' "$json" | bw encode | bw create item --quiet
    success "  Stored '$name' in vault"
}

# Read existing secrets from .bashrc automatically
EXISTING_GITLAB_TOKEN=$(grep -oP "(?<=export GITLAB_TOKEN=).*" "$HOME/.bashrc" | tr -d '"' | head -1 || true)
EXISTING_GMAIL_PASS=$(grep -oP "(?<=export GMAIL_APP_PASS=).*" "$HOME/.bashrc" | tr -d '"' | head -1 || true)
EXISTING_GMAIL_USER=$(grep -oP "(?<=export GMAIL_USER=).*" "$HOME/.bashrc" | tr -d '"' | head -1 || true)
EXISTING_SEND_TO=$(grep -oP "(?<=export SEND_TO=).*" "$HOME/.bashrc" | tr -d '"' | head -1 || true)

echo ""
info "Found these secrets in ~/.bashrc (will be moved to vault):"
[ -n "$EXISTING_GITLAB_TOKEN" ] && echo "  GITLAB_TOKEN  -> found"
[ -n "$EXISTING_GMAIL_PASS"   ] && echo "  GMAIL_APP_PASS -> found"
[ -n "$EXISTING_GMAIL_USER"   ] && echo "  GMAIL_USER    -> found"
[ -n "$EXISTING_SEND_TO"      ] && echo "  SEND_TO       -> found"

create_secret "homelab-gitlab-token" "$EXISTING_GITLAB_TOKEN"
create_secret "homelab-gmail-pass"   "$EXISTING_GMAIL_PASS"
create_secret "homelab-gmail-user"   "$EXISTING_GMAIL_USER"
create_secret "homelab-send-to"      "$EXISTING_SEND_TO"

# ─── Step 5: Clean ~/.bashrc ─────────────────────────────────────────────────
step "Step 5/5 — Removing hardcoded secrets from ~/.bashrc"

# Back up shell configuration, but sanitize the backup as well.
BASHRC_BACKUP="$HOME/.bashrc.bak.$(date +%Y%m%d%H%M%S)"
umask 077
cp "$HOME/.bashrc" "$BASHRC_BACKUP"
chmod 600 "$BASHRC_BACKUP"

# Remove secret exports from both files so the backup cannot become a leak.
for file in "$HOME/.bashrc" "$BASHRC_BACKUP"; do
    sed -i '/^export GITLAB_TOKEN=/d' "$file"
    sed -i '/^export GMAIL_APP_PASS=/d' "$file"
    sed -i '/^export GMAIL_USER=/d' "$file"
    sed -i '/^export SEND_TO=/d' "$file"
done
success "Backed up ~/.bashrc and removed hardcoded secrets from both copies"

# Add load-secrets sourcing if not already there
if ! grep -q "load-secrets.sh" "$HOME/.bashrc"; then
    cat >> "$HOME/.bashrc" <<'EOF'

# Load homelab secrets from Vaultwarden
if [ -f "$HOME/homelab/scripts/load-secrets.sh" ]; then
    source "$HOME/homelab/scripts/load-secrets.sh"
fi
EOF
    success "Added load-secrets.sh to ~/.bashrc"
fi

# ─── Done ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Setup complete!                             ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  Vaultwarden: $VAULTWARDEN_URL"
echo -e "${GREEN}║  Secrets: stored in vault                    ║${NC}"
echo -e "${GREEN}║  ~/.bashrc: cleaned                          ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  NEXT: rotate your GitLab token, then        ║${NC}"
echo -e "${GREEN}║  update it with:                             ║${NC}"
echo -e "${GREEN}║    bw edit item <item-id>                    ║${NC}"
echo -e "${GREEN}║  or via a Bitwarden client.                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
info "Run 'source ~/.bashrc' or open a new terminal to pick up changes."
