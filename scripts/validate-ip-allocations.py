#!/usr/bin/env python3
"""Fail CI if the MetalLB pool can collide with static lab addresses.

Parses the pool range from ansible group vars and every ansible_host from the
inventory files, then asserts the two sets are disjoint. Also fails if the
pool reaches below .200, the boundary reserved for static addresses
(libvirt DHCP ends at .199 — see scripts/libvirt-reserve-lab-range.sh).

Exit codes: 0 ok, 1 collision or misconfiguration.
"""
import ipaddress
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GROUP_VARS = ROOT / "ansible" / "inventory" / "group_vars" / "kubeadm_all.yml"
INVENTORIES = [
    ROOT / "ansible" / "inventory" / "homelab.ini",
    ROOT / "ansible" / "inventory" / "kubeadm.ini",
]
DHCP_CEILING_LAST_OCTET = 200  # DHCP ends at .199; .200+ is static by convention


def parse_pool() -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
    text = GROUP_VARS.read_text(encoding="utf-8")
    match = re.search(r'metallb_pool_range:\s*"([^"]+)"', text)
    if not match:
        sys.exit(f"ERROR: metallb_pool_range not found in {GROUP_VARS}")
    start_s, end_s = match.group(1).split("-")
    start, end = ipaddress.IPv4Address(start_s), ipaddress.IPv4Address(end_s)
    if int(end) <= int(start):
        sys.exit(f"ERROR: pool range {start}-{end} is empty or inverted")
    return start, end


def parse_inventory_hosts() -> dict[str, ipaddress.IPv4Address]:
    hosts: dict[str, ipaddress.IPv4Address] = {}
    for inv in INVENTORIES:
        for line in inv.read_text(encoding="utf-8").splitlines():
            m = re.match(
                r"^([A-Za-z0-9.-]+)\s+ansible_host=(\d+\.\d+\.\d+\.\d+)", line.strip()
            )
            if m:
                hosts[m.group(1)] = ipaddress.IPv4Address(m.group(2))
    return hosts


def main() -> int:
    start, end = parse_pool()
    print(f"MetalLB pool: {start} - {end}  ({int(end) - int(start) + 1} addresses)")

    hosts = parse_inventory_hosts()
    if not hosts:
        print("ERROR: no ansible_host entries found in inventories")
        return 1

    collisions = {
        name: ip
        for name, ip in hosts.items()
        if int(start) <= int(ip) <= int(end)
    }
    if collisions:
        print(
            "ERROR: static hosts inside the MetalLB pool — MetalLB could "
            "allocate an address that is already in use:"
        )
        for name, ip in sorted(collisions.items(), key=lambda kv: int(kv[1])):
            print(f"  {ip}  {name}")
        return 1

    if int(str(start).split(".")[-1]) < DHCP_CEILING_LAST_OCTET:
        print(
            f"ERROR: pool starts below .{DHCP_CEILING_LAST_OCTET} "
            "(libvirt DHCP range) — raise the start or shrink the pool"
        )
        return 1

    print(
        f"OK: pool disjoint from {len(hosts)} static inventory hosts "
        f"({min(hosts.values(), key=int)} … {max(hosts.values(), key=int)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
