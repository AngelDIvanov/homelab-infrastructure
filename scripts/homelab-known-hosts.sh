#!/usr/bin/env bash
# Pin the SSH host keys of the k3s lab VMs for Ansible.
#
# Mirrors scripts/kubeadm-known-hosts.sh: the keys are read through the QEMU
# guest agent (virsh qemu-agent-command), not over the network, so a host on
# the libvirt bridge cannot substitute its own key. The result is written to
# ansible/inventory/known_hosts-k3s, which homelab.ini uses with
# StrictHostKeyChecking=yes.
#
# Run after the VMs have booted (qemu-guest-agent is installed by cloud-init):
#   ./scripts/homelab-known-hosts.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
INVENTORY=ansible/inventory/homelab.ini
KNOWN_HOSTS=ansible/inventory/known_hosts-k3s
KEY_TYPES="ed25519 ecdsa rsa"

guest_exec() {
    # guest_exec <vm> <command...>: run a command in the guest and print its stdout
    local vm=$1; shift
    local args pid status
    args=$(printf '%s\n' "$@" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))')
    pid=$(virsh qemu-agent-command "$vm" \
        "{\"execute\":\"guest-exec\",\"arguments\":{\"path\":\"/bin/sh\",\"arg\":$args,\"capture-output\":true}}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["return"]["pid"])')
    for _ in $(seq 1 20); do
        status=$(virsh qemu-agent-command "$vm" \
            "{\"execute\":\"guest-exec-status\",\"arguments\":{\"pid\":$pid}}")
        if python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["return"]["exited"] else 1)' <<< "$status"; then
            python3 -c 'import json,sys,base64; r=json.load(sys.stdin)["return"]; print(base64.b64decode(r.get("out-data","")).decode().strip())' <<< "$status"
            return 0
        fi
        sleep 0.5
    done
    echo "Error: guest command on $vm did not finish." >&2
    return 1
}

if ! virsh list --name >/dev/null 2>&1; then
    echo "Error: cannot talk to libvirt." >&2
    exit 1
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

while read -r vm ip; do
    if [ "$(virsh domstate "$vm" 2>/dev/null)" != "running" ]; then
        echo "Error: $vm is not running; start the VMs first." >&2
        exit 1
    fi
    cat_cmd="cat"
    for keytype in $KEY_TYPES; do
        cat_cmd="$cat_cmd /etc/ssh/ssh_host_${keytype}_key.pub"
    done
    keys=$(guest_exec "$vm" -c "$cat_cmd 2>/dev/null")
    if [ -z "$keys" ]; then
        echo "Error: no host keys read from $vm." >&2
        exit 1
    fi
    while read -r keytype keydata _; do
        printf '%s %s %s\n' "$ip" "$keytype" "$keydata" >> "$tmp"
    done <<< "$keys"
    echo "pinned $vm ($ip)"
done < <(awk '/^[a-z0-9.-]+[[:space:]]+ansible_host=/ { split($2, a, "="); print $1, a[2] }' "$INVENTORY")

install -m 0644 "$tmp" "$KNOWN_HOSTS"
echo "wrote $KNOWN_HOSTS"
