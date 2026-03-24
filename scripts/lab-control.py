#!/usr/bin/env python3
"""
DevOps Home Lab - Unified Control Panel
========================================
Combines: k3s-start/stop, crc-start/stop, lab-stop-all,
          check-lab, and all lab-manager operations.

Author: Angel
Usage:  python3 lab-control.py
"""

import subprocess
import sys
import os
import re
import time

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION  (edit these if your setup changes)
# ─────────────────────────────────────────────────────────────
SCRIPTS_DIR       = os.path.dirname(os.path.abspath(__file__))
TERRAFORM_DIR     = os.path.expanduser("~/homelab/terraform")
ANSIBLE_DIR       = os.path.expanduser("~/homelab/ansible")
ANSIBLE_INVENTORY = os.path.join(ANSIBLE_DIR, "inventory/homelab.ini")

K3S_CONTROL_IP = "192.168.122.218"
CI_RUNNER_IP   = "192.168.122.220"
K3S_TOKEN      = "K10f56614b297cb7bf1aefdf9729e609f96a532f1a166949beecadda41ad9f834ad::server:86629278cae454833dba29ea33c9b15e"
K3S_URL        = f"https://{K3S_CONTROL_IP}:6443"
BASE_IP_OCTET  = 221   # k3s-worker-2 = .221, worker-3 = .222 …

SSH_OPTS = "-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no"

# Tell Ansible where its config is regardless of working directory
os.environ['ANSIBLE_CONFIG'] = os.path.expanduser('~/homelab/ansible/ansible.cfg')

# ─────────────────────────────────────────────────────────────
#  COLORS
# ─────────────────────────────────────────────────────────────
class C:
    GREEN  = '\033[92m'
    RED    = '\033[91m'
    YELLOW = '\033[93m'
    BLUE   = '\033[94m'
    CYAN   = '\033[96m'
    BOLD   = '\033[1m'
    DIM    = '\033[2m'
    END    = '\033[0m'

def g(t): return f"{C.GREEN}{t}{C.END}"
def r(t): return f"{C.RED}{t}{C.END}"
def y(t): return f"{C.YELLOW}{t}{C.END}"
def b(t): return f"{C.BLUE}{t}{C.END}"
def c(t): return f"{C.CYAN}{t}{C.END}"
def bold(t): return f"{C.BOLD}{t}{C.END}"
def dim(t):  return f"{C.DIM}{t}{C.END}"

# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
def run(cmd, capture=False, check=False):
    """Run a shell command, optionally capturing output."""
    if not capture:
        print(dim(f"$ {cmd}"))
    result = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
    if check and result.returncode != 0 and capture:
        print(r(f"  Error: {result.stderr.strip()}"))
    return result

def run_script(name):
    """Run one of the lab bash scripts by name."""
    path = os.path.join(SCRIPTS_DIR, name)
    if not os.path.isfile(path):
        print(r(f"  Script not found: {path}"))
        print(y(f"  Make sure {name} is in the same directory as lab-control.py"))
        return
    run(f"bash {path}")

def pause():
    input(f"\n{c('Press Enter to continue...')}")

def divider(title=""):
    line = "─" * 60
    if title:
        print(f"\n{bold(b(line))}")
        print(f"{bold(c('  ' + title))}")
        print(f"{bold(b(line))}")
    else:
        print(f"\n{b(line)}")

def clear():
    os.system("clear")

def get_worker_ip(worker_num):
    return f"192.168.122.{BASE_IP_OCTET + worker_num - 2}"

def get_vm_count():
    tfvars = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
    try:
        content = open(tfvars).read()
        m = re.search(r'vm_count\s*=\s*(\d+)', content)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0

def set_vm_count(count):
    tfvars = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
    lines = open(tfvars).readlines()
    with open(tfvars, 'w') as f:
        for line in lines:
            f.write(f'vm_count       = {count}\n' if line.strip().startswith('vm_count') else line)

def wait_for_ssh(ip, timeout=120):
    print(y(f"  ⏳ Waiting for SSH on {ip}..."))
    start = time.time()
    while time.time() - start < timeout:
        if run(f"ssh {SSH_OPTS} andy@{ip} 'echo ok' 2>/dev/null", capture=True).returncode == 0:
            print(g(f"  ✓ SSH ready on {ip}"))
            return True
        time.sleep(5)
    print(r(f"  ✗ SSH timeout for {ip}"))
    return False

def join_k3s(ip, name):
    print(y(f"  ⏳ Joining {name} to k3s..."))
    cmd = f'ssh {SSH_OPTS} andy@{ip} "curl -sfL https://get.k3s.io | K3S_URL={K3S_URL} K3S_TOKEN={K3S_TOKEN} sh -"'
    if run(cmd).returncode != 0:
        print(r(f"  ✗ Failed to install k3s on {name}"))
        return False
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl restart k3s-agent"', capture=True)
    time.sleep(5)
    result = run(f'ssh {SSH_OPTS} andy@{ip} "systemctl is-active k3s-agent"', capture=True)
    if result.stdout.strip() == "active":
        print(g(f"  ✓ {name} joined cluster"))
        return True
    print(r(f"  ✗ k3s-agent failed on {name}"))
    return False

def drain_node(name):
    print(y(f"  ⏳ Draining {name}..."))
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl drain {name} --ignore-daemonsets --delete-emptydir-data --force 2>/dev/null || true"')
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl delete node {name} 2>/dev/null || true"')
    print(g(f"  ✓ {name} removed"))

def update_ansible_inventory():
    vm_count = get_vm_count()
    content = """[all:vars]
ansible_user=andy
ansible_ssh_private_key_file=~/.ssh/id_rsa

[control_plane]
k3s-control ansible_host=192.168.122.218

[infra]
k3s-infra ansible_host=192.168.122.230

[workers]
k3s-worker-1 ansible_host=192.168.122.219
"""
    for i in range(vm_count):
        wnum = i + 2
        content += f"k3s-worker-{wnum} ansible_host={get_worker_ip(wnum)}\n"
    content += """
[ci_cd]
ci-runner ansible_host=192.168.122.220

[k8s_cluster:children]
control_plane
workers
"""
    os.makedirs(os.path.dirname(ANSIBLE_INVENTORY), exist_ok=True)
    open(ANSIBLE_INVENTORY, 'w').write(content)
    print(g("  ✓ Ansible inventory updated"))

def sync_images(ip, name):
    """Sync trengo-search image to a worker node."""
    print(y(f"  ⏳ Syncing images to {name}..."))

    # Find the image on any available node
    image = ""
    source_ip = ""
    for candidate_ip in ["192.168.122.219", "192.168.122.218"]:
        res = run(f'ssh {SSH_OPTS} andy@{candidate_ip} "sudo k3s crictl images 2>/dev/null | grep trengo-search | head -1"', capture=True)
        line = res.stdout.strip()
        if line and "trengo-search" in line:
            parts = line.split()
            if len(parts) >= 2:
                image = f"{parts[0]}:{parts[1]}"
                source_ip = candidate_ip
                break

    if not image or not source_ip:
        print(y("  ⚠ No trengo-search image found on any node"))
        return

    print(dim(f"  Image: {image} from {source_ip}"))

    run(f'ssh {SSH_OPTS} andy@{source_ip} "sudo k3s ctr images export /tmp/sync.tar \'{image}\'"')
    run(f'scp {SSH_OPTS} andy@{source_ip}:/tmp/sync.tar /tmp/')
    run(f'scp {SSH_OPTS} /tmp/sync.tar andy@{ip}:/tmp/')
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo k3s ctr images import /tmp/sync.tar && sudo rm /tmp/sync.tar"')
    run(f'ssh {SSH_OPTS} andy@{source_ip} "sudo rm -f /tmp/sync.tar"', capture=True)
    print(g(f"  ✓ Synced to {name}"))

# ─────────────────────────────────────────────────────────────
#  BANNER
# ─────────────────────────────────────────────────────────────
def vm_states():
    """Return dict {vm: bool} — True = running, discovered dynamically from virsh."""
    # Get all VMs
    all_result = run("virsh list --all 2>/dev/null", capture=True)
    running_result = run("virsh list --state-running 2>/dev/null", capture=True)

    all_vms = []
    for line in all_result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lstrip("-").isdigit() or (len(parts) >= 1 and parts[0] == "-"):
            # Extract VM name — skip header and separator lines
            if len(parts) >= 2 and parts[1] not in ["Name", "---"]:
                all_vms.append(parts[1])

    running = set()
    for line in running_result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] not in ["Name", "---"]:
            running.add(parts[1])

    return {vm: vm in running for vm in sorted(all_vms)}


def banner():
    clear()
    B = f"{C.BLUE}{C.BOLD}"
    E = C.END

    print(f"\n{B}╔══════════════════════════════════════════════════════════════╗{E}")
    print(f"{B}║{E}          🏠  DevOps Home Lab — Control Panel  🏠             {B}║{E}")
    print(f"{B}╠══════════════════════════════════════════════════════════════╣{E}")
    print(f"{B}║{E}  {C.BOLD}VM STATUS{E}                                                    {B}║{E}")
    print(f"{B}╠══════════════════════════════════════════════════════════════╣{E}")

    # Live VM status — 2 per row, fixed width box (content = 62 chars)
    states = vm_states()
    vms    = list(states.items())
    for i in range(0, len(vms), 2):
        lname, lon = vms[i]
        lic  = f"{C.GREEN}●{E}" if lon  else f"{C.RED}●{E}"
        lstate = "on " if lon  else "off"
        ltxt = g(lstate) if lon else r(lstate)

        if i + 1 < len(vms):
            rname, ron = vms[i + 1]
            ric   = f"{C.GREEN}●{E}" if ron else f"{C.RED}●{E}"
            rstate = "on " if ron else "off"
            rtxt  = g(rstate) if ron else r(rstate)
            # visible for padding calc (no ansi)
            vis = f"  * {lstate} {lname:<16}    * {rstate} {rname:<16}"
            col = f"  {lic} {ltxt} {lname:<16}    {ric} {rtxt} {rname:<16}"
        else:
            vis = f"  * {lstate} {lname:<16}"
            col = f"  {lic} {ltxt} {lname:<16}"

        pad = 62 - len(vis)
        print(f"{B}║{E}{col}{' ' * pad}{B}║{E}")

    print(f"{B}╚══════════════════════════════════════════════════════════════╝{E}\n")

# ─────────────────────────────────────────────────────────────
#  MENUS
# ─────────────────────────────────────────────────────────────
def main_menu():
    banner()
    print(f"""\
{c('┌──────────────────────────────────────────────────────────┐')}
{c('│')}  {bold('ENVIRONMENTS')}                                             {c('│')}
{c('├──────────────────────────────────────────────────────────┤')}
{c('│')}  {bold('1.')}  ☸️   K3s Cluster        start / stop / status        {c('│')}
{c('│')}  {bold('2.')}  🛑  Stop ALL            shutdown everything           {c('│')}
{c('├──────────────────────────────────────────────────────────┤')}
{c('│')}  {bold('LAB TOOLS')}                                                {c('│')}
{c('├──────────────────────────────────────────────────────────┤')}
{c('│')}  {bold('3.')}  🩺  Health Check        run check-lab                {c('│')}
{c('│')}  {bold('4.')}  📈  Scale               add / remove workers         {c('│')}
{c('│')}  {bold('5.')}  📦  Ansible             run playbooks                {c('│')}
{c('│')}  {bold('6.')}  🔄  Sync Images         push to all nodes            {c('│')}
{c('│')}  {bold('7.')}  🔧  Rejoin Workers      re-attach nodes to k3s       {c('│')}
{c('│')}  {bold('8.')}  📊  Cluster Status      nodes + virsh overview       {c('│')}
{c('├──────────────────────────────────────────────────────────┤')}
{c('│')}  {bold('0.')}  🚪  Exit                                             {c('│')}
{c('└──────────────────────────────────────────────────────────┘')}""")
    return input(f"\n{y('Select option: ')}")

# ── K3s sub-menu ──────────────────────────────────────────────
def k3s_menu():
    while True:
        banner()
        print(f"""\
{c('┌─────────────────────────────────────────┐')}
{c('│')}  {bold('☸️  K3S CLUSTER')}                         {c('│')}
{c('├─────────────────────────────────────────┤')}
{c('│')}  {bold('1.')}  ▶️   Start K3s                       {c('│')}
{c('│')}  {bold('2.')}  ⏹️   Stop K3s                        {c('│')}
{c('│')}  {bold('3.')}  📊  Status                           {c('│')}
{c('│')}  {bold('0.')}  ↩️   Back                            {c('│')}
{c('└─────────────────────────────────────────┘')}""")
        choice = input(f"\n{y('Select option: ')}")
        if choice == '1':
            divider("▶️  STARTING K3S CLUSTER")
            run_script("k3s-start.sh")
            pause()
        elif choice == '2':
            divider("⏹️  STOPPING K3S CLUSTER")
            run_script("k3s-stop.sh")
            pause()
        elif choice == '3':
            divider("📊 K3S STATUS")
            run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
            run("virsh list --all")
            pause()
        elif choice == '0':
            break

# ── CRC helpers ───────────────────────────────────────────────
def crc_running():
    res = run("virsh list --state-running 2>/dev/null", capture=True)
    return "crc" in res.stdout

def crc_info_block():
    """Print CRC console URL and credentials if CRC is running."""
    if not crc_running():
        print(f"\n  {dim('CRC is not running — start it first.')}")
        return
    print(f"\n  {bold(c('── CRC Info ──────────────────────────────────────'))}")
    res = run("crc console --url 2>/dev/null", capture=True)
    url = res.stdout.strip()
    if url:
        print(f"  {bold('Console URL  :')} {g(url)}")
    else:
        print(f"  {bold('Console URL  :')} {y('not available yet — may still be booting')}")
    res = run("crc console --credentials 2>/dev/null", capture=True)
    creds = res.stdout.strip()
    if creds:
        import re as _re
        print()
        for line in creds.splitlines():
            # Extract just the oc login command from the sentence
            match = _re.search(r"(oc login[^']+)", line)
            if not match:
                continue
            cmd = match.group(1).strip().rstrip("'")
            if "kubeadmin" in line:
                print(f"  {bold('Admin     :')} {g(cmd)}")
            elif "developer" in line:
                print(f"  {bold('Developer :')} {c(cmd)}")
    else:
        print(f"  {y('Credentials not available yet.')}")
    print()

# ── CRC sub-menu ──────────────────────────────────────────────
def crc_menu():
    while True:
        banner()
        running = crc_running()
        status  = g("● running") if running else r("● stopped")
        print(f"""\
{c('┌─────────────────────────────────────────┐')}
{c('│')}  {bold('🔴 CRC (OPENSHIFT)')}   {status}           {c('│')}
{c('├─────────────────────────────────────────┤')}
{c('│')}  {bold('1.')}  ▶️   Start CRC                       {c('│')}
{c('│')}  {bold('2.')}  ⏹️   Stop CRC                        {c('│')}
{c('│')}  {bold('3.')}  🔑  Show URL & Credentials           {c('│')}
{c('│')}  {bold('0.')}  ↩️   Back                            {c('│')}
{c('└─────────────────────────────────────────┘')}""")
        choice = input(f"\n{y('Select option: ')}")
        if choice == '1':
            divider("▶️  STARTING CRC")
            run_script("crc-start.sh")
            divider("🔑 CRC ACCESS INFO")
            crc_info_block()
            pause()
        elif choice == '2':
            divider("⏹️  STOPPING CRC")
            run_script("crc-stop.sh")
            pause()
        elif choice == '3':
            divider("🔑 CRC URL & CREDENTIALS")
            crc_info_block()
            pause()
        elif choice == '0':
            break

# ── Scale sub-menu ────────────────────────────────────────────
def scale_menu():
    while True:
        banner()
        vm_count = get_vm_count()
        print(f"""\
{c('┌─────────────────────────────────────────┐')}
{c('│')}  {bold('📈 SCALE WORKERS')}                        {c('│')}
{c('│')}  {dim(f"  Terraform-managed workers: {vm_count}")}          {c('│')}
{c('├─────────────────────────────────────────┤')}
{c('│')}  {bold('1.')}  📈  Upscale   (add worker)           {c('│')}
{c('│')}  {bold('2.')}  📉  Downscale (remove worker)        {c('│')}
{c('│')}  {bold('0.')}  ↩️   Back                            {c('│')}
{c('└─────────────────────────────────────────┘')}""")
        choice = input(f"\n{y('Select option: ')}")
        if choice == '1':
            do_upscale()
            pause()
        elif choice == '2':
            do_downscale()
            pause()
        elif choice == '0':
            break

# ── Ansible sub-menu ──────────────────────────────────────────
def ansible_menu():
    while True:
        banner()

        # Dynamically discover playbooks from disk
        playbook_dir = os.path.join(ANSIBLE_DIR, "playbooks")
        if os.path.isdir(playbook_dir):
            found = sorted([f for f in os.listdir(playbook_dir) if f.endswith(".yml")])
        else:
            found = []

        print(f"{c('┌─────────────────────────────────────────┐')}")
        print(f"{c('│')}  {bold('📦 ANSIBLE PLAYBOOKS')}                    {c('│')}")
        print(f"{c('├─────────────────────────────────────────┤')}")

        if found:
            for i, name in enumerate(found, start=1):
                label = name.replace(".yml", "").replace("-", " ").title()
                line = f"  {bold(str(i) + '.'):<4}  {label}"
                print(f"{c('│')}{line:<41}{c('│')}")
        else:
            print(f"{c('│')}  {y('No playbooks found'):<39}{c('│')}")
            print(f"{c('│')}  {dim(playbook_dir):<39}{c('│')}")

        print(f"{c('├─────────────────────────────────────────┤')}")
        print(f"{c('│')}  {bold('c.')}   Run custom playbook              {c('│')}")
        print(f"{c('│')}  {bold('0.')}   ↩️  Back                          {c('│')}")
        print(f"{c('└─────────────────────────────────────────┘')}")

        choice = input(f"\n{y('Select option: ')}")

        if choice == '0':
            break
        elif choice == 'c':
            name = input(c("  Enter playbook filename (e.g. install-btop.yml): "))
            if name:
                divider(f"▶ Running {name}")
                run(f"ansible-playbook -i {ANSIBLE_INVENTORY} {playbook_dir}/{name}")
            pause()
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(found):
                    selected = found[idx]
                    divider(f"▶ {selected}")
                    run(f"ansible-playbook -i {ANSIBLE_INVENTORY} {playbook_dir}/{selected}")
                    pause()
                else:
                    print(r("  Invalid option"))
                    time.sleep(1)
            except ValueError:
                print(r("  Invalid option"))
                time.sleep(1)

# ─────────────────────────────────────────────────────────────
#  OPERATIONS
# ─────────────────────────────────────────────────────────────
def do_stop_all():
    divider("🛑 STOPPING ALL ENVIRONMENTS")
    confirm = input(y("  This will shut down all VMs. Proceed? (y/n): "))
    if confirm.lower() != 'y':
        print("  Cancelled.")
        return
    run_script("lab-stop-all.sh")

def do_health_check():
    divider("🩺 HEALTH CHECK")
    print(f"""
  {bold('Run mode:')}
  {c('1.')} Normal check (auto-start if needed)
  {c('2.')} Force restart  (--restart)
  {c('3.')} Force hard reboot  (--reboot)
  {c('0.')} Cancel
""")
    mode = input(y("  Select: "))
    script = os.path.join(SCRIPTS_DIR, "check-lab.sh")
    if mode == '1':
        run(f"bash {script}")
    elif mode == '2':
        run(f"bash {script} --restart")
    elif mode == '3':
        run(f"bash {script} --reboot")

def do_upscale():
    divider("📈 UPSCALE — Adding new worker")
    current    = get_vm_count()
    new_count  = current + 1
    new_wnum   = new_count + 1
    new_name   = f"k3s-worker-{new_wnum}"
    new_ip     = get_worker_ip(new_wnum)

    print(f"  Current Terraform workers : {current}")
    print(f"  New worker                : {new_name}  ({new_ip})")
    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled.")
        return

    # 1 — Terraform
    divider("Step 1/6 — Terraform Apply")
    set_vm_count(new_count)
    os.chdir(TERRAFORM_DIR)
    if run("terraform apply -auto-approve").returncode != 0:
        print(r("  Terraform failed — aborting.")); return

    # 2 — Boot delay
    divider("Step 2/6 — Waiting for VM boot (60s)")
    for i in range(6):
        print(f"  [{i*10}/60s]", end='\r', flush=True)
        time.sleep(10)
    print(g("  ✓ Boot delay done          "))

    # 3 — SSH
    divider("Step 3/6 — Waiting for SSH")
    # Remove old host key to avoid known_hosts conflicts with reused IPs
    run(f"ssh-keygen -f ~/.ssh/known_hosts -R {new_ip} 2>/dev/null || true", capture=True)
    if not wait_for_ssh(new_ip):
        print(r("  SSH unavailable — check VM manually.")); return

    # 4 — Join k3s
    divider("Step 4/6 — Joining k3s")
    if not join_k3s(new_ip, new_name):
        print(r("  k3s join failed.")); return

    # 5 — Wait for Ready
    divider("Step 5/6 — Waiting for node Ready")
    for i in range(12):
        res = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get node {new_name} --no-headers 2>/dev/null | grep -q Ready"', capture=True)
        if res.returncode == 0:
            print(g(f"  ✓ {new_name} is Ready!")); break
        print(f"  Waiting... [{i*10}/120s]")
        time.sleep(10)

    # 6 — Post-setup
    divider("Step 6/6 — Post-setup")
    update_ansible_inventory()
    sync_images(new_ip, new_name)

    # Scale replicas to match total worker count (worker-1 + terraform workers)
    total_workers = new_count + 1  # +1 for manual worker-1
    divider("Step 6b — Scaling app replicas")
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas={total_workers}"')
    print(g(f"  ✓ Scaled trengo-search to {total_workers} replicas"))

    print(f"\n{g('═'*50)}\n{g(f'✓ UPSCALE COMPLETE — {new_name} ({new_ip})')}\n{g('═'*50)}")

def do_downscale():
    divider("📉 DOWNSCALE — Removing worker")
    current = get_vm_count()
    if current <= 0:
        print(r("  No Terraform-managed workers to remove.")); return

    wnum    = current + 1
    wname   = f"k3s-worker-{wnum}"
    wip     = get_worker_ip(wnum)

    print(f"  Worker to remove : {wname}  ({wip})")
    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    divider("Step 1/3 — Draining from k3s")
    drain_node(wname)

    divider("Step 2/3 — Terraform scale down")
    set_vm_count(current - 1)
    os.chdir(TERRAFORM_DIR)
    run("terraform apply -auto-approve")

    divider("Step 3/3 — Updating Ansible inventory")
    update_ansible_inventory()

    # Scale replicas to match remaining worker count
    remaining_workers = (current - 1) + 1  # terraform workers - 1 + manual worker-1
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas={remaining_workers}"', capture=True)
    print(g(f"  ✓ Scaled trengo-search to {remaining_workers} replicas"))

    print(f"\n{g('═'*50)}\n{g(f'✓ DOWNSCALE COMPLETE — removed {wname}')}\n{g('═'*50)}")

def do_sync_all():
    divider("🔄 SYNC IMAGES TO ALL WORKERS")
    workers = {"k3s-worker-1": "192.168.122.219"}
    for i in range(get_vm_count()):
        wnum = i + 2
        workers[f"k3s-worker-{wnum}"] = get_worker_ip(wnum)
    for name, ip in sorted(workers.items()):
        sync_images(ip, name)
    print(g("\n  ✓ All workers synced!"))

def do_rejoin():
    divider("🔧 REJOIN — Re-attach workers to k3s")
    vm_count = get_vm_count()
    if vm_count <= 0:
        print(r("  No Terraform-managed workers found.")); return

    workers = [(i+2, f"k3s-worker-{i+2}", get_worker_ip(i+2)) for i in range(vm_count)]
    for _, name, ip in workers:
        print(f"  {name}: {ip}")
    if input(f"\n{y('  Rejoin all? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    print(c("\n  Cleaning old node entries..."))
    for _, name, _ in workers:
        run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl delete node {name} 2>/dev/null || true"', capture=True)

    print(c("  Restarting k3s server..."))
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo systemctl restart k3s"')
    print("  Waiting 30s for k3s to restart...")
    time.sleep(30)

    for _, name, ip in workers:
        divider(f"Processing {name} ({ip})")
        if not wait_for_ssh(ip, timeout=60):
            print(r(f"  ✗ SSH unavailable for {name}")); continue
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo rm -f /etc/rancher/node/password"', capture=True)
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl stop k3s-agent 2>/dev/null || true"', capture=True)
        join_k3s(ip, name)

    print(c("\n  Waiting for nodes to be Ready..."))
    time.sleep(30)
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    update_ansible_inventory()
    print(g("\n  ✓ Rejoin complete!"))

def do_status():
    divider("📊 CLUSTER STATUS")
    print(c("\n── K3s Nodes ──"))
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    print(c("\n── Terraform Workers ──"))
    vm_count = get_vm_count()
    print(f"  Terraform vm_count: {vm_count}")
    for i in range(vm_count):
        wnum = i + 2
        print(f"  k3s-worker-{wnum}: {get_worker_ip(wnum)}")
    print(c("\n── All VMs (virsh) ──"))
    run("virsh list --all")

# ─────────────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────────────
def main():
    dispatch = {
        '1': k3s_menu,
        '2': lambda: (do_stop_all(),    pause()),
        '3': lambda: (do_health_check(), pause()),
        '4': scale_menu,
        '5': ansible_menu,
        '6': lambda: (do_sync_all(),    pause()),
        '7': lambda: (do_rejoin(),      pause()),
        '8': lambda: (do_status(),      pause()),
    }

    while True:
        choice = main_menu()
        if choice == '0':
            print(g("\nGoodbye! 👋\n"))
            sys.exit(0)
        action = dispatch.get(choice)
        if action:
            action()
        else:
            print(r("  Invalid option"))
            time.sleep(1)

if __name__ == "__main__":
    main()
