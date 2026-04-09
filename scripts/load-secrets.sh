#!/bin/bash
# Load homelab secrets from Vaultwarden.
# Sourced automatically from ~/.bashrc — unlocks vault once, persists session.
#
# Usage: source ~/homelab/scripts/load-secrets.sh

SESSION_FILE="$HOME/.config/homelab/.bw_session"
CERT_FILE="$HOME/.config/homelab/vaultwarden-cert.pem"

# Skip entirely if bw is not installed yet (first-boot before setup)
if ! command -v bw &>/dev/null; then
    return 0 2>/dev/null || exit 0
fi

# Trust the self-signed Vaultwarden cert
if [ -f "$CERT_FILE" ]; then
    export NODE_EXTRA_CA_CERTS="$CERT_FILE"
fi

# Try to use a cached session
if [ -f "$SESSION_FILE" ]; then
    export BW_SESSION
    BW_SESSION=$(cat "$SESSION_FILE")
fi

# Check if session is still valid; unlock if not
if ! bw unlock --check &>/dev/null; then
    echo "[vault] Session expired — unlocking Vaultwarden..."
    mkdir -p "$(dirname "$SESSION_FILE")"
    chmod 700 "$(dirname "$SESSION_FILE")"
    export BW_SESSION
    BW_SESSION=$(bw unlock --raw)
    echo "$BW_SESSION" > "$SESSION_FILE"
    chmod 600 "$SESSION_FILE"
fi

# Fetch secrets — use file cache so new tabs don't re-fetch from vault
SECRETS_CACHE="$HOME/.config/homelab/.secrets_cache"
export K3S_TOKEN GITLAB_TOKEN GMAIL_APP_PASS GMAIL_USER SEND_TO

if [ -f "$SECRETS_CACHE" ]; then
    # Load from cache (fast)
    # shellcheck source=/dev/null
    source "$SECRETS_CACHE"
else
    # Fetch from vault and write cache
    K3S_TOKEN=$(bw get password "homelab-k3s-token" 2>/dev/null || echo "")
    GITLAB_TOKEN=$(bw get password "homelab-gitlab-token" 2>/dev/null || echo "")
    GMAIL_APP_PASS=$(bw get password "homelab-gmail-pass" 2>/dev/null || echo "")
    GMAIL_USER=$(bw get password "homelab-gmail-user" 2>/dev/null || echo "")
    SEND_TO=$(bw get password "homelab-send-to" 2>/dev/null || echo "")
    # Write cache
    cat > "$SECRETS_CACHE" <<EOF
K3S_TOKEN="$K3S_TOKEN"
GITLAB_TOKEN="$GITLAB_TOKEN"
GMAIL_APP_PASS="$GMAIL_APP_PASS"
GMAIL_USER="$GMAIL_USER"
SEND_TO="$SEND_TO"
EOF
    chmod 600 "$SECRETS_CACHE"
fi
