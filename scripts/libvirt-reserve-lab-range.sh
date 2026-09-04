#!/usr/bin/env bash
# Keep libvirt's default-network DHCP range below .200 so the fixed lab
# addresses (MetalLB pool .200-.220, NFS .230, kubeadm nodes .240+) are
# never handed out as leases. Idempotent; applies to the live and
# persistent network definition and restores the previous ranges if the
# replacement cannot be added.
set -euo pipefail

NETWORK=${NETWORK:-default}
RANGE_START=${RANGE_START:-192.168.122.2}
RANGE_END=${RANGE_END:-192.168.122.199}
RESERVED_FROM=200

last_octet() { echo "${1##*.}"; }

if ! net_xml=$(virsh net-dumpxml "$NETWORK"); then
    echo "Error: cannot read network $NETWORK." >&2
    exit 1
fi
mapfile -t ranges < <(sed -n "s/.*<range start='\([^']*\)' end='\([^']*\)'.*/\1 \2/p" <<< "$net_xml")

if [ "${#ranges[@]}" -eq 1 ] && [ "${ranges[0]}" = "$RANGE_START $RANGE_END" ]; then
    echo "$NETWORK DHCP range already $RANGE_START-$RANGE_END"
    exit 0
fi

needs_change=false
for r in "${ranges[@]}"; do
    end=${r#* }
    if [ "$(last_octet "$end")" -ge "$RESERVED_FROM" ] || [ "${#ranges[@]}" -ne 1 ]; then
        needs_change=true
    fi
done
if [ "$needs_change" = false ]; then
    echo "$NETWORK DHCP ranges stay below .$RESERVED_FROM: ${ranges[*]}"
    exit 0
fi

# libvirt cannot modify a DHCP range in place, only delete and add.
# Delete every existing range, then add the reserved one; on failure put
# the original ranges back so the network never ends up without DHCP.
restore() {
    echo "Error: range replacement failed; restoring the previous ranges" >&2
    for r in "${ranges[@]}"; do
        # adding a range that still exists fails harmlessly
        virsh net-update "$NETWORK" add ip-dhcp-range \
            "<range start='${r% *}' end='${r#* }'/>" --live --config >/dev/null 2>&1 || true
    done
}
for r in "${ranges[@]}"; do
    if ! virsh net-update "$NETWORK" delete ip-dhcp-range \
            "<range start='${r% *}' end='${r#* }'/>" --live --config >/dev/null; then
        restore
        exit 1
    fi
done
if ! virsh net-update "$NETWORK" add ip-dhcp-range \
        "<range start='$RANGE_START' end='$RANGE_END'/>" --live --config >/dev/null; then
    restore
    exit 1
fi
echo "$NETWORK DHCP range changed from ${ranges[*]} to $RANGE_START-$RANGE_END"
