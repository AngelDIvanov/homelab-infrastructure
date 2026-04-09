#!/bin/bash
# Generates a self-signed TLS cert for Vaultwarden and stores it as a K8s Secret.
# Run this once before deploying Vaultwarden.
#
# Usage: bash ~/homelab/scripts/gen-vaultwarden-cert.sh

set -euo pipefail

K3S_CONTROL="192.168.122.218"
VAULTWARDEN_IP="192.168.122.218"
CERT_DIR="/tmp/vaultwarden-certs"

mkdir -p "$CERT_DIR"

echo "Generating self-signed TLS certificate for $VAULTWARDEN_IP..."

# Generate cert with SAN for the IP address
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout "$CERT_DIR/key.pem" \
  -out    "$CERT_DIR/cert.pem" \
  -days   3650 \
  -subj   "/CN=vaultwarden/O=homelab" \
  -addext "subjectAltName=IP:$VAULTWARDEN_IP"

echo "Certificate generated."

# Copy certs to control node and create K8s Secret
echo "Creating K8s Secret on control node..."
scp -o StrictHostKeyChecking=no \
    "$CERT_DIR/cert.pem" "$CERT_DIR/key.pem" \
    "andy@$K3S_CONTROL:/tmp/"

ssh -o StrictHostKeyChecking=no "andy@$K3S_CONTROL" "
  sudo k3s kubectl create namespace vaultwarden --dry-run=client -o yaml | sudo k3s kubectl apply -f -
  sudo k3s kubectl delete secret vaultwarden-tls -n vaultwarden --ignore-not-found
  sudo k3s kubectl create secret generic vaultwarden-tls \
    --from-file=cert.pem=/tmp/cert.pem \
    --from-file=key.pem=/tmp/key.pem \
    -n vaultwarden
  rm -f /tmp/cert.pem /tmp/key.pem
"

mkdir -p "$HOME/.config/homelab"
chmod 700 "$HOME/.config/homelab"
cp "$CERT_DIR/cert.pem" "$HOME/.config/homelab/vaultwarden-cert.pem"
chmod 600 "$HOME/.config/homelab/vaultwarden-cert.pem"

rm -rf "$CERT_DIR"
echo "Done. TLS secret created in k8s, cert saved to ~/.config/homelab/vaultwarden-cert.pem"
