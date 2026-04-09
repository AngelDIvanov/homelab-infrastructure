#!/bin/bash
# =============================================================================
# Homelab Vault Setup — One-time bootstrap
# =============================================================================
# What this does:
#   1. Deploys Vaultwarden to k3s via Ansible
#   2. Installs Bitwarden CLI (bw)
#   3. Logs in and seeds vault with all homelab secrets
#   4. Strips secrets from ~/.bashrc and replaces with load-secrets.sh
#
# Usage: bash ~/homelab/scripts/setup-vault.sh
# =============================================================================

set -euo pipefail

HOMELAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$HOMELAB_DIR/scripts"
ANSIBLE_DIR="$HOMELAB_DIR/ansible"
SESSION_FILE="$HOME/.config/homelab/.bw_session"
VAULTWARDEN_URL="https://192.168.122.218:30900"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR ]${NC} $*"; exit 1; }
step()    { echo -e "\n${CYAN}══════════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}══════════════════════════════════════════${NC}"; }

CERT_FILE="$HOME/.config/homelab/vaultwarden-cert.pem"

mkdir -p "$(dirname "$SESSION_FILE")"
chmod 700 "$(dirname "$SESSION_FILE")"

# Trust the self-signed cert for all bw CLI calls
if [ -f "$CERT_FILE" ]; then
    export NODE_EXTRA_CA_CERTS="$CERT_FILE"
fi

# ─── Step 1: Deploy Vaultwarden ──────────────────────────────────────────────
step "Step 1/5 — Deploying Vaultwarden to k3s"

MANIFEST="$HOMELAB_DIR/kubernetes/deployments/vaultwarden.yaml"

# Only generate cert once — skip if already present
if [ ! -f "$CERT_FILE" ]; then
    info "Generating TLS certificate..."
    bash "$SCRIPTS_DIR/gen-vaultwarden-cert.sh"
    export NODE_EXTRA_CA_CERTS="$CERT_FILE"
else
    success "TLS certificate already exists, skipping generation."
fi

# Always apply the manifest from the correct local path
info "Applying Vaultwarden manifest..."
ssh -o StrictHostKeyChecking=no andy@192.168.122.218 "cat > /home/andy/vaultwarden.yaml" < "$MANIFEST"
ssh -o StrictHostKeyChecking=no andy@192.168.122.218 "
  sudo k3s kubectl apply -f /home/andy/vaultwarden.yaml
  rm /home/andy/vaultwarden.yaml
"
success "Vaultwarden deployed at $VAULTWARDEN_URL"

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
    warn "Open your browser and go to: $VAULTWARDEN_URL"
    warn "Create your account there first, THEN press Enter to continue."
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
    json=$(printf '{"type":1,"name":"%s","login":{"password":"%s"}}' "$name" "$value")
    echo "$json" | bw encode | bw create item --quiet
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

echo ""
warn "Enter your K3S_TOKEN (not in .bashrc — check your k3s control node or use the new token after rotation):"
read -rsp "  K3S_TOKEN: " INPUT_K3S_TOKEN
echo ""

create_secret "homelab-k3s-token"    "$INPUT_K3S_TOKEN"
create_secret "homelab-gitlab-token" "$EXISTING_GITLAB_TOKEN"
create_secret "homelab-gmail-pass"   "$EXISTING_GMAIL_PASS"
create_secret "homelab-gmail-user"   "$EXISTING_GMAIL_USER"
create_secret "homelab-send-to"      "$EXISTING_SEND_TO"

# ─── Step 5: Clean ~/.bashrc ─────────────────────────────────────────────────
step "Step 5/5 — Removing hardcoded secrets from ~/.bashrc"

# Backup first
cp "$HOME/.bashrc" "$HOME/.bashrc.bak.$(date +%Y%m%d%H%M%S)"
success "Backed up ~/.bashrc"

# Remove secret exports
sed -i '/^export GITLAB_TOKEN=/d' "$HOME/.bashrc"
sed -i '/^export GMAIL_APP_PASS=/d' "$HOME/.bashrc"
sed -i '/^export GMAIL_USER=/d' "$HOME/.bashrc"
sed -i '/^export SEND_TO=/d' "$HOME/.bashrc"
success "Removed hardcoded secrets from ~/.bashrc"

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
echo -e "${GREEN}║  Vaultwarden: $VAULTWARDEN_URL   ║${NC}"
echo -e "${GREEN}║  Secrets: stored in vault                    ║${NC}"
echo -e "${GREEN}║  ~/.bashrc: cleaned                          ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  NEXT: rotate your GitLab + K3s tokens,      ║${NC}"
echo -e "${GREEN}║  then update them with:                      ║${NC}"
echo -e "${GREEN}║    bw edit item <item-id>                    ║${NC}"
echo -e "${GREEN}║  or via the Vaultwarden web UI.              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
info "Run 'source ~/.bashrc' or open a new terminal to pick up changes."
