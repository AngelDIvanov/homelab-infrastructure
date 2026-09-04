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
mkdir -p "$(dirname "$SECRETS_CACHE")"
chmod 700 "$(dirname "$SECRETS_CACHE")"
export K3S_TOKEN GITLAB_TOKEN GMAIL_APP_PASS GMAIL_USER SEND_TO

load_cache() {
    [ -f "$SECRETS_CACHE" ] || return 1
    [ "$(head -n 1 "$SECRETS_CACHE")" = "CACHE_VERSION=2" ] || return 1

    local name encoded value
    while IFS='=' read -r name encoded; do
        case "$name" in
            K3S_TOKEN|GITLAB_TOKEN|GMAIL_APP_PASS|GMAIL_USER|SEND_TO)
                value=$(printf '%s' "$encoded" | base64 -d 2>/dev/null) || return 1
                [ -n "$value" ] || return 1
                printf -v "$name" '%s' "$value"
                ;;
        esac
    done < "$SECRETS_CACHE"
    # A truncated or partial cache must not count as a hit
    for name in K3S_TOKEN GITLAB_TOKEN GMAIL_APP_PASS GMAIL_USER SEND_TO; do
        [ -n "${!name}" ] || return 1
    done
}

write_cache_value() {
    local name=$1 value=$2
    printf '%s=' "$name"
    printf '%s' "$value" | base64 -w0
    printf '\n'
}

if ! load_cache; then
    K3S_TOKEN=$(bw get password "homelab-k3s-token" 2>/dev/null || echo "")
    GITLAB_TOKEN=$(bw get password "homelab-gitlab-token" 2>/dev/null || echo "")
    GMAIL_APP_PASS=$(bw get password "homelab-gmail-pass" 2>/dev/null || echo "")
    GMAIL_USER=$(bw get password "homelab-gmail-user" 2>/dev/null || echo "")
    SEND_TO=$(bw get password "homelab-send-to" 2>/dev/null || echo "")

    if [ -n "$K3S_TOKEN" ] && [ -n "$GITLAB_TOKEN" ] && [ -n "$GMAIL_APP_PASS" ] && [ -n "$GMAIL_USER" ] && [ -n "$SEND_TO" ]; then
        (
            umask 077
            {
                echo "CACHE_VERSION=2"
                write_cache_value K3S_TOKEN "$K3S_TOKEN"
                write_cache_value GITLAB_TOKEN "$GITLAB_TOKEN"
                write_cache_value GMAIL_APP_PASS "$GMAIL_APP_PASS"
                write_cache_value GMAIL_USER "$GMAIL_USER"
                write_cache_value SEND_TO "$SEND_TO"
            } > "$SECRETS_CACHE"
            chmod 600 "$SECRETS_CACHE"
        )
    fi
fi
