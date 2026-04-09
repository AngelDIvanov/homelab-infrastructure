#!/usr/bin/env python3
"""
Lab Manager — scale workers, run Ansible, sync images
Usage: python3 lab-manager.py
"""

import subprocess
import sys
import os
import re
import time

TERRAFORM_DIR     = os.path.expanduser("~/homelab/terraform")
ANSIBLE_DIR       = os.path.expanduser("~/homelab/ansible")
ANSIBLE_INVENTORY = os.path.join(ANSIBLE_DIR, "inventory/homelab.ini")

# VM IPs — default libvirt NAT range, adjust for your network
K3S_CONTROL_IP = "192.168.122.218"
K3S_URL        = f"https://{K3S_CONTROL_IP}:6443"
BASE_IP_OCTET  = 221  # worker-2 = .221, worker-3 = .222, ...

# Static IP map
# 192.168.122.218 - k3s-control
# 192.168.122.219 - k3s-worker-1 (manual)
# 192.168.122.220 - ci-runner
# 192.168.122.221 - k3s-worker-2 (terraform)
# 192.168.122.222 - k3s-worker-3
# ...

def _get_secret(env_var, vault_item):
    value = os.environ.get(env_var, "")
    if value:
        return value
    result = subprocess.run(["bw", "get", "password", vault_item],
                            capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    print(f"Error: {env_var} not set and vault fetch failed.")
    print("Run: source ~/homelab/scripts/load-secrets.sh")
    sys.exit(1)

K3S_TOKEN = _get_secret("K3S_TOKEN", "homelab-k3s-token")
SSH_OPTS  = "-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no"

def get_worker_ip(n):
    return f"192.168.122.{BASE_IP_OCTET + n - 2}"

class C:
    GREEN  = '\033[92m'
    RED    = '\033[91m'
    YELLOW = '\033[93m'
    BLUE   = '\033[94m'
    CYAN   = '\033[96m'
    BOLD   = '\033[1m'
    END    = '\033[0m'

def g(t): return f"{C.GREEN}{t}{C.END}"
def r(t): return f"{C.RED}{t}{C.END}"
def y(t): return f"{C.YELLOW}{t}{C.END}"
def c(t): return f"{C.CYAN}{t}{C.END}"
def bold(t): return f"{C.BOLD}{t}{C.END}"

def header():
    print(f"""
{C.BLUE}╔═══════════════════════════════════════════════════════════╗
║                  Lab Manager                              ║
╚═══════════════════════════════════════════════════════════╝{C.END}
""")

def menu():
    print(f"""
{c('┌─────────────────────────────────────────┐')}
{c('│')}  1.  Upscale    — add worker             {c('│')}
{c('│')}  2.  Downscale  — remove worker          {c('│')}
{c('│')}  3.  Ansible    — run playbooks          {c('│')}
{c('│')}  4.  Status     — cluster overview       {c('│')}
{c('│')}  5.  Sync       — push images to nodes   {c('│')}
{c('│')}  6.  Rejoin     — re-attach nodes        {c('│')}
{c('│')}  0.  Exit                                {c('│')}
{c('└─────────────────────────────────────────┘')}
""")

def run(cmd, capture=False):
    print(f"{C.BOLD}$ {cmd}{C.END}")
    return subprocess.run(cmd, shell=True,
                          capture_output=capture, text=capture)

def get_vm_count():
    tfvars = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
    try:
        m = re.search(r'vm_count\s*=\s*(\d+)', open(tfvars).read())
        return int(m.group(1)) if m else 0
    except Exception:
        return 0

def set_vm_count(count):
    tfvars = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
    lines  = open(tfvars).readlines()
    with open(tfvars, 'w') as f:
        for line in lines:
            f.write(f'vm_count       = {count}\n'
                    if line.strip().startswith('vm_count') else line)

def wait_for_ssh(ip, timeout=120):
    print(y(f"  waiting for SSH on {ip}..."))
    start = time.time()
    while time.time() - start < timeout:
        if run(f"ssh {SSH_OPTS} andy@{ip} 'echo ok' 2>/dev/null",
               capture=True).returncode == 0:
            print(g(f"  SSH ready on {ip}"))
            return True
        time.sleep(5)
    print(r(f"  SSH timeout for {ip}"))
    return False

def join_k3s(ip, name):
    print(y(f"  joining {name}..."))
    cmd = (f'ssh {SSH_OPTS} andy@{ip} '
           f'"curl -sfL https://get.k3s.io | K3S_URL={K3S_URL} K3S_TOKEN={K3S_TOKEN} sh -"')
    if run(cmd).returncode != 0:
        print(r(f"  failed to install k3s on {name}"))
        return False
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl restart k3s-agent"', capture=True)
    time.sleep(5)
    res = run(f'ssh {SSH_OPTS} andy@{ip} "systemctl is-active k3s-agent"', capture=True)
    if res.stdout.strip() == "active":
        print(g(f"  {name} joined"))
        return True
    print(r(f"  k3s-agent failed on {name}"))
    return False

def drain_node(name):
    print(y(f"  draining {name}..."))
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
        f'"sudo k3s kubectl drain {name} --ignore-daemonsets --delete-emptydir-data --force 2>/dev/null || true"')
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
        f'"sudo k3s kubectl delete node {name} 2>/dev/null || true"')
    print(g(f"  {name} removed"))

def update_inventory():
    vm_count = get_vm_count()
    content  = """[all:vars]
ansible_user=andy
ansible_ssh_private_key_file=~/.ssh/id_rsa

[control_plane]
k3s-control ansible_host=192.168.122.218

[workers]
k3s-worker-1 ansible_host=192.168.122.219
"""
    for i in range(vm_count):
        n = i + 2
        content += f"k3s-worker-{n} ansible_host={get_worker_ip(n)}\n"
    content += """
[ci_cd]
ci-runner ansible_host=192.168.122.220

[k8s_cluster:children]
control_plane
workers
"""
    open(ANSIBLE_INVENTORY, 'w').write(content)
    print(g("  inventory updated"))

def sync_images(ip, name):
    print(y(f"  syncing images to {name}..."))
    res = run(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
        f'"sudo k3s ctr images list | grep trengo-search | head -1 | awk \'{{print $1}}\'"',
        capture=True
    )
    if not res.stdout.strip():
        print(y("  no trengo-search image found"))
        return
    image = res.stdout.strip()
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s ctr images export /tmp/sync.tar {image}"')
    run(f'scp {SSH_OPTS} andy@{K3S_CONTROL_IP}:/tmp/sync.tar /tmp/')
    run(f'scp {SSH_OPTS} /tmp/sync.tar andy@{ip}:/tmp/')
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo k3s ctr images import /tmp/sync.tar"')
    print(g(f"  synced to {name}"))

def upscale():
    current  = get_vm_count()
    new_n    = current + 2   # worker-1 is manual, terraform starts at worker-2
    new_name = f"k3s-worker-{new_n}"
    new_ip   = get_worker_ip(new_n)
    print(f"  {current} terraform workers → adding {new_name} ({new_ip})")
    if input(y("  Proceed? (y/n): ")).lower() != 'y':
        return

    set_vm_count(current + 1)
    os.chdir(TERRAFORM_DIR)
    if run("terraform apply -auto-approve").returncode != 0:
        print(r("  terraform failed")); return

    print(y("  waiting 60s for VM to boot..."))
    for i in range(6):
        print(f"  [{i*10}/60s]", end='\r', flush=True)
        time.sleep(10)
    print()

    if not wait_for_ssh(new_ip):
        print(r("  SSH not available")); return
    if not join_k3s(new_ip, new_name):
        print(r("  failed to join k3s")); return

    for i in range(12):
        res = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
            f'"sudo k3s kubectl get node {new_name} --no-headers 2>/dev/null | grep -q Ready"',
            capture=True
        )
        if res.returncode == 0:
            print(g(f"  {new_name} Ready")); break
        print(f"  waiting... [{i*10}/120s]")
        time.sleep(10)

    update_inventory()
    sync_images(new_ip, new_name)
    print(g(f"\n  done — {new_name} ({new_ip})"))

def downscale():
    current = get_vm_count()
    if current <= 0:
        print(r("  no terraform-managed workers")); return
    n    = current + 1
    name = f"k3s-worker-{n}"
    ip   = get_worker_ip(n)
    print(f"  removing {name} ({ip})")
    if input(y("  Proceed? (y/n): ")).lower() != 'y':
        return
    drain_node(name)
    set_vm_count(current - 1)
    os.chdir(TERRAFORM_DIR)
    run("terraform apply -auto-approve")
    update_inventory()
    print(g(f"\n  done — removed {name}"))

def ansible_menu():
    os.chdir(ANSIBLE_DIR)
    playbook_dir = os.path.join(ANSIBLE_DIR, "playbooks")
    found = sorted([f for f in os.listdir(playbook_dir) if f.endswith(".yml")])
    print()
    for i, name in enumerate(found, 1):
        print(f"  {bold(str(i)+'.')}  {name}")
    print(f"  {bold('c.')}  custom")
    print(f"  {bold('0.')}  back\n")
    choice = input(y("  Select: "))
    if choice == '0':
        return
    elif choice == 'c':
        name = input(c("  playbook filename: "))
        if name:
            run(f"ansible-playbook -i {ANSIBLE_INVENTORY} {playbook_dir}/{name}")
    else:
        try:
            sel = found[int(choice) - 1]
            run(f"ansible-playbook -i {ANSIBLE_INVENTORY} {playbook_dir}/{sel}")
        except (IndexError, ValueError):
            print(r("  invalid"))

def show_status():
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    print()
    vm_count = get_vm_count()
    print(f"  terraform workers: {vm_count}")
    for i in range(vm_count):
        n = i + 2
        print(f"    k3s-worker-{n}: {get_worker_ip(n)}")
    print()
    run("virsh list --all")

def sync_all():
    workers = {"k3s-worker-1": "192.168.122.219"}
    for i in range(get_vm_count()):
        n = i + 2
        workers[f"k3s-worker-{n}"] = get_worker_ip(n)
    for name, ip in sorted(workers.items()):
        sync_images(ip, name)
    print(g("\n  all workers synced"))

def rejoin():
    vm_count = get_vm_count()
    if vm_count <= 0:
        print(r("  no terraform workers")); return
    workers = [(i+2, f"k3s-worker-{i+2}", get_worker_ip(i+2)) for i in range(vm_count)]
    for _, name, ip in workers:
        print(f"  {name}: {ip}")
    if input(y("  Rejoin all? (y/n): ")).lower() != 'y':
        return

    for _, name, _ in workers:
        run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
            f'"sudo k3s kubectl delete node {name} 2>/dev/null || true"', capture=True)

    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo systemctl restart k3s"')
    print(y("  waiting 30s for k3s..."))
    time.sleep(30)

    for _, name, ip in workers:
        if not wait_for_ssh(ip, timeout=60):
            print(r(f"  SSH unavailable for {name}")); continue
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo rm -f /etc/rancher/node/password"', capture=True)
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl stop k3s-agent 2>/dev/null || true"', capture=True)
        join_k3s(ip, name)

    time.sleep(30)
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    update_inventory()
    print(g("\n  rejoin complete"))

def main():
    header()
    while True:
        menu()
        choice = input(y("Select: "))
        if   choice == '1': upscale()
        elif choice == '2': downscale()
        elif choice == '3': ansible_menu()
        elif choice == '4': show_status()
        elif choice == '5': sync_all()
        elif choice == '6': rejoin()
        elif choice == '0':
            print(g("\nbye\n")); sys.exit(0)
        else:
            print(r("  invalid"))
        input(c("\nEnter to continue..."))

if __name__ == "__main__":
    main()
