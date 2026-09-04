#!/usr/bin/env bash
# Keep libvirt's default-network DHCP range below .200 so the fixed lab
# addresses (MetalLB pool .200-.220, NFS .230, kubeadm nodes .240+) are
# never handed out as leases. Idempotent; applies to the live and
# persistent network definition.
set -euo pipefail

NETWORK=${NETWORK:-default}
RANGE_START=${RANGE_START:-192.168.122.2}
RANGE_END=${RANGE_END:-192.168.122.199}

current=$(virsh net-dumpxml "$NETWORK" | sed -n "s/.*<range start='\([^']*\)' end='\([^']*\)'.*/\1 \2/p" | head -1)
if [ "$current" = "$RANGE_START $RANGE_END" ]; then
    echo "$NETWORK DHCP range already $RANGE_START-$RANGE_END"
    exit 0
fi
old_start=${current% *}
old_end=${current#* }
# libvirt cannot modify a DHCP range in place, only delete and add
virsh net-update "$NETWORK" delete ip-dhcp-range \
    "<range start='$old_start' end='$old_end'/>" --live --config >/dev/null
virsh net-update "$NETWORK" add ip-dhcp-range \
    "<range start='$RANGE_START' end='$RANGE_END'/>" --live --config >/dev/null
echo "$NETWORK DHCP range changed from ${current% *}-${old_end} to $RANGE_START-$RANGE_END"
