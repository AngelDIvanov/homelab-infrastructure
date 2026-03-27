#!/usr/bin/env python3
"""
DevOps Home Lab - Unified Control Panel
========================================
Author: Angel
Usage:  python3 lab-control.py
"""

import subprocess
import sys
import os
import re
import time

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
SCRIPTS_DIR       = os.path.dirname(os.path.abspath(__file__))
TERRAFORM_DIR     = os.path.expanduser("~/homelab/terraform")
ANSIBLE_DIR       = os.path.expanduser("~/homelab/ansible")
ANSIBLE_INVENTORY = os.path.join(ANSIBLE_DIR, "inventory/homelab.ini")

K3S_CONTROL_IP = "192.168.122.218"
K3S_INFRA_IP   = "192.168.122.230"
CI_RUNNER_IP   = "192.168.122.220"
K3S_WORKER1_IP = "192.168.122.219"
K3S_TOKEN      = "K10f56614b297cb7bf1aefdf9729e609f96a532f1a166949beecadda41ad9f834ad::server:86629278cae454833dba29ea33c9b15e"
K3S_URL        = f"https://{K3S_CONTROL_IP}:6443"
BASE_IP_OCTET  = 221   # k3s-worker-2 = .221, worker-3 = .222 ...

ALERTMANAGER_URL = f"http://{K3S_CONTROL_IP}:30093"

# All permanent VMs (start/stop all)
PERMANENT_VMS = [
    "k3s-control",
    "k3s-infra",
    "k3s-worker-1",
    "ci-runner",
]

SSH_OPTS = "-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no"
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

def g(t):    return f"{C.GREEN}{t}{C.END}"
def r(t):    return f"{C.RED}{t}{C.END}"
def y(t):    return f"{C.YELLOW}{t}{C.END}"
def b(t):    return f"{C.BLUE}{t}{C.END}"
def c(t):    return f"{C.CYAN}{t}{C.END}"
def bold(t): return f"{C.BOLD}{t}{C.END}"
def dim(t):  return f"{C.DIM}{t}{C.END}"

# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
def run(cmd, capture=False, check=False):
    if not capture:
        print(dim(f"$ {cmd}"))
    result = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
    if check and result.returncode != 0 and capture:
        print(r(f"  Error: {result.stderr.strip()}"))
    return result

def run_script(name):
    path = os.path.join(SCRIPTS_DIR, name)
    if not os.path.isfile(path):
        print(r(f"  Script not found: {path}"))
        print(y(f"  Make sure {name} is in the same directory as lab-control.py"))
        return
    run(f"bash {path}")

def pause():
    input(f"\n{c('Press Enter to continue...')}")

def divider(title=""):
    line = "-" * 60
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
    print(y(f"  waiting for SSH on {ip}..."))
    start = time.time()
    while time.time() - start < timeout:
        if run(f"ssh {SSH_OPTS} andy@{ip} 'echo ok' 2>/dev/null", capture=True).returncode == 0:
            print(g(f"  SSH ready on {ip}"))
            return True
        time.sleep(5)
    print(r(f"  SSH timeout for {ip}"))
    return False

def join_k3s(ip, name):
    print(y(f"  joining {name} to k3s..."))
    cmd = f'ssh {SSH_OPTS} andy@{ip} "curl -sfL https://get.k3s.io | K3S_URL={K3S_URL} K3S_TOKEN={K3S_TOKEN} sh -"'
    if run(cmd).returncode != 0:
        print(r(f"  failed to install k3s on {name}"))
        return False
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl restart k3s-agent"', capture=True)
    time.sleep(5)
    result = run(f'ssh {SSH_OPTS} andy@{ip} "systemctl is-active k3s-agent"', capture=True)
    if result.stdout.strip() == "active":
        print(g(f"  {name} joined cluster"))
        return True
    print(r(f"  k3s-agent failed on {name}"))
    return False

def drain_node(name):
    print(y(f"  draining {name}..."))
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl drain {name} --ignore-daemonsets --delete-emptydir-data --force 2>/dev/null || true"')
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl delete node {name} 2>/dev/null || true"')
    print(g(f"  {name} removed"))

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
    print(g("  Ansible inventory updated"))

def sync_images(ip, name):
    print(y(f"  syncing images to {name}..."))
    image = ""
    source_ip = ""
    for candidate_ip in [K3S_WORKER1_IP, K3S_CONTROL_IP]:
        res = run(f'ssh {SSH_OPTS} andy@{candidate_ip} "sudo k3s crictl images 2>/dev/null | grep trengo-search | head -1"', capture=True)
        line = res.stdout.strip()
        if line and "trengo-search" in line:
            parts = line.split()
            if len(parts) >= 2:
                image = f"{parts[0]}:{parts[1]}"
                source_ip = candidate_ip
                break
    if not image or not source_ip:
        print(y("  no trengo-search image found on any node"))
        return
    print(dim(f"  image: {image} from {source_ip}"))
    run(f'ssh {SSH_OPTS} andy@{source_ip} "sudo k3s ctr images export /tmp/sync.tar \'{image}\'"')
    run(f'scp {SSH_OPTS} andy@{source_ip}:/tmp/sync.tar /tmp/')
    run(f'scp {SSH_OPTS} /tmp/sync.tar andy@{ip}:/tmp/')
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo k3s ctr images import /tmp/sync.tar && sudo rm /tmp/sync.tar"')
    run(f'ssh {SSH_OPTS} andy@{source_ip} "sudo rm -f /tmp/sync.tar"', capture=True)
    print(g(f"  synced to {name}"))

# ─────────────────────────────────────────────────────────────
#  BANNER
# ─────────────────────────────────────────────────────────────
def vm_states():
    all_result     = run("virsh list --all 2>/dev/null", capture=True)
    running_result = run("virsh list --state-running 2>/dev/null", capture=True)
    all_vms = []
    for line in all_result.stdout.splitlines():
        parts = line.split()
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
    print(f"\n{B}+==============================================================+{E}")
    print(f"{B}|{E}           DevOps Home Lab  --  Control Panel                {B}|{E}")
    print(f"{B}+==============================================================+{E}")
    print(f"{B}|{E}  {C.BOLD}VM STATUS{E}                                                    {B}|{E}")
    print(f"{B}+==============================================================+{E}")
    states = vm_states()
    vms    = list(states.items())
    for i in range(0, len(vms), 2):
        lname, lon = vms[i]
        lic    = g("*") if lon  else r("*")
        lstate = g("on ") if lon else r("off")
        if i + 1 < len(vms):
            rname, ron = vms[i + 1]
            ric    = g("*") if ron else r("*")
            rstate = g("on ") if ron else r("off")
            vis = f"  * {'on ' if lon else 'off'} {lname:<16}    * {'on ' if ron else 'off'} {rname:<16}"
            col = f"  {lic} {lstate} {lname:<16}    {ric} {rstate} {rname:<16}"
        else:
            vis = f"  * {'on ' if lon else 'off'} {lname:<16}"
            col = f"  {lic} {lstate} {lname:<16}"
        pad = 62 - len(vis)
        print(f"{B}|{E}{col}{' ' * pad}{B}|{E}")
    print(f"{B}+==============================================================+{E}\n")

# ─────────────────────────────────────────────────────────────
#  MENUS
# ─────────────────────────────────────────────────────────────
def main_menu():
    banner()
    print(f"""\
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('ENVIRONMENTS')}                                             {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('1.')}  Stop ALL           shutdown everything              {c('|')}
{c('|')}  {bold('2.')}  Start ALL          boot all permanent VMs           {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('LAB TOOLS')}                                               {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('3.')}  Health Check       run check-lab                   {c('|')}
{c('|')}  {bold('4.')}  Scale              add / remove workers             {c('|')}
{c('|')}  {bold('5.')}  Ansible            run playbooks                   {c('|')}
{c('|')}  {bold('6.')}  Sync Images        push to all nodes                {c('|')}
{c('|')}  {bold('7.')}  Rejoin Workers     re-attach nodes to k3s           {c('|')}
{c('|')}  {bold('8.')}  Cluster Status     nodes + virsh overview           {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('ALERTING')}                                                {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('9.')}  Alerting Tests     app nuke, RAM nuke,              {c('|')}
{c('|')}       {dim('               auto-remediation demos')}                {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('SERVICES')}                                                {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('10.')} Service Links      all URLs and access info         {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('0.')}  Exit                                               {c('|')}
{c('+----------------------------------------------------------+')}""")
    return input(f"\n{y('Select option: ')}")

# ── K3s sub-menu ──────────────────────────────────────────────
def k3s_menu():
    while True:
        banner()
        print(f"""\
{c('+-------------------------------------------+')}
{c('|')}  {bold('K3S CLUSTER')}                             {c('|')}
{c('+-------------------------------------------+')}
{c('|')}  {bold('1.')}  Start K3s                          {c('|')}
{c('|')}  {bold('2.')}  Stop K3s                           {c('|')}
{c('|')}  {bold('3.')}  Status                             {c('|')}
{c('|')}  {bold('0.')}  Back                               {c('|')}
{c('+-------------------------------------------+')}""")
        choice = input(f"\n{y('Select option: ')}")
        if choice == '1':
            divider("STARTING K3S CLUSTER")
            run_script("k3s-start.sh")
            pause()
        elif choice == '2':
            divider("STOPPING K3S CLUSTER")
            run_script("k3s-stop.sh")
            pause()
        elif choice == '3':
            divider("K3S STATUS")
            run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
            run("virsh list --all")
            pause()
        elif choice == '0':
            break

# ── Scale sub-menu ────────────────────────────────────────────
def scale_menu():
    while True:
        banner()
        vm_count = get_vm_count()
        print(f"""\
{c('+-------------------------------------------+')}
{c('|')}  {bold('SCALE WORKERS')}                           {c('|')}
{c('|')}  {dim(f"  Terraform-managed workers: {vm_count}")}          {c('|')}
{c('+-------------------------------------------+')}
{c('|')}  {bold('1.')}  Upscale   (add worker)             {c('|')}
{c('|')}  {bold('2.')}  Downscale (remove worker)          {c('|')}
{c('|')}  {bold('0.')}  Back                               {c('|')}
{c('+-------------------------------------------+')}""")
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
        playbook_dir = os.path.join(ANSIBLE_DIR, "playbooks")
        if os.path.isdir(playbook_dir):
            found = sorted([f for f in os.listdir(playbook_dir) if f.endswith(".yml")])
        else:
            found = []
        print(f"{c('+-------------------------------------------+')}")
        print(f"{c('|')}  {bold('ANSIBLE PLAYBOOKS')}                       {c('|')}")
        print(f"{c('+-------------------------------------------+')}")
        if found:
            for i, name in enumerate(found, start=1):
                label = name.replace(".yml", "").replace("-", " ").title()
                line = f"  {bold(str(i) + '.'):<4}  {label}"
                print(f"{c('|')}{line:<43}{c('|')}")
        else:
            print(f"{c('|')}  {y('No playbooks found'):<41}{c('|')}")
        print(f"{c('+-------------------------------------------+')}")
        print(f"{c('|')}  {bold('c.')}   Run custom playbook               {c('|')}")
        print(f"{c('|')}  {bold('0.')}   Back                              {c('|')}")
        print(f"{c('+-------------------------------------------+')}")
        choice = input(f"\n{y('Select option: ')}")
        if choice == '0':
            break
        elif choice == 'c':
            name = input(c("  Enter playbook filename (e.g. install-btop.yml): "))
            if name:
                divider(f"Running {name}")
                run(f"ansible-playbook -i {ANSIBLE_INVENTORY} {playbook_dir}/{name}")
            pause()
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(found):
                    selected = found[idx]
                    divider(f"Running {selected}")
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
    divider("STOPPING ALL ENVIRONMENTS")
    confirm = input(y("  This will FORCE KILL all running VMs. Proceed? (y/n): "))
    if confirm.lower() != 'y':
        print("  Cancelled.")
        return

    # Get all currently running VMs (skip Base — it's a disk template)
    result = run("virsh list --state-running 2>/dev/null", capture=True)
    running = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] not in ["Name", "---", "Base"]:
            running.append(parts[1])

    if not running:
        print(dim("  No VMs currently running."))
        return

    print(c(f"\n  Running VMs to stop: {', '.join(running)}"))
    print(y("  Attempting graceful shutdown first (5s), then force destroy..."))

    for vm in running:
        print(y(f"  stopping {vm}..."))
        run(f"virsh shutdown {vm} 2>/dev/null || true", capture=True)

    time.sleep(5)

    # Check what's still running and force-destroy
    result = run("virsh list --state-running 2>/dev/null", capture=True)
    still_running = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] not in ["Name", "---", "Base"]:
            still_running.append(parts[1])

    if still_running:
        print(r(f"\n  Still running after graceful shutdown: {', '.join(still_running)}"))
        print(r("  Force destroying..."))
        for vm in still_running:
            res = run(f"virsh destroy {vm} 2>/dev/null", capture=True)
            if res.returncode == 0:
                print(r(f"  {vm} force killed"))
            else:
                print(r(f"  {vm} failed: {res.stderr.strip()}"))

    print(g("\n  All VMs stopped."))
    run("virsh list --all")

def do_start_all():
    divider("STARTING ALL PERMANENT VMs")
    confirm = input(y("  This will start all permanent VMs. Proceed? (y/n): "))
    if confirm.lower() != 'y':
        print("  Cancelled.")
        return
    for vm in PERMANENT_VMS:
        print(y(f"  starting {vm}..."))
        result = run(f"virsh start {vm} 2>/dev/null", capture=True)
        if result.returncode == 0:
            print(g(f"  {vm} started"))
        else:
            out = result.stderr.strip()
            if "already active" in out:
                print(dim(f"  {vm} already running"))
            else:
                print(r(f"  {vm} failed: {out}"))
    print(g("\n  All permanent VMs started."))
    print(dim("  Allow 30-60s for k3s to come up, then run health check."))

def do_health_check():
    divider("HEALTH CHECK")
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
    divider("UPSCALE -- Adding new worker")
    current   = get_vm_count()
    new_count = current + 1
    new_wnum  = new_count + 1
    new_name  = f"k3s-worker-{new_wnum}"
    new_ip    = get_worker_ip(new_wnum)
    print(f"  Current Terraform workers : {current}")
    print(f"  New worker                : {new_name}  ({new_ip})")
    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    divider("Step 1/6 -- Terraform Apply")
    set_vm_count(new_count)
    os.chdir(TERRAFORM_DIR)
    if run("terraform apply -auto-approve").returncode != 0:
        print(r("  Terraform failed -- aborting.")); return

    divider("Step 2/6 -- Waiting for VM boot (60s)")
    for i in range(6):
        print(f"  [{i*10}/60s]", end='\r', flush=True)
        time.sleep(10)
    print(g("  Boot delay done          "))

    divider("Step 3/6 -- Waiting for SSH")
    run(f"ssh-keygen -f ~/.ssh/known_hosts -R {new_ip} 2>/dev/null || true", capture=True)
    if not wait_for_ssh(new_ip):
        print(r("  SSH unavailable -- check VM manually.")); return

    divider("Step 4/6 -- Joining k3s")
    if not join_k3s(new_ip, new_name):
        print(r("  k3s join failed.")); return

    divider("Step 5/6 -- Waiting for node Ready")
    for i in range(12):
        res = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get node {new_name} --no-headers 2>/dev/null | grep -q Ready"', capture=True)
        if res.returncode == 0:
            print(g(f"  {new_name} is Ready!")); break
        print(f"  waiting... [{i*10}/120s]")
        time.sleep(10)

    divider("Step 6/6 -- Post-setup")
    update_ansible_inventory()
    sync_images(new_ip, new_name)

    total_workers = new_count + 1
    divider("Step 6b -- Scaling app replicas")
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas={total_workers}"')
    print(g(f"  scaled trengo-search to {total_workers} replicas"))
    print(f"\n{g('='*50)}\n{g(f'UPSCALE COMPLETE -- {new_name} ({new_ip})')}\n{g('='*50)}")

def do_downscale():
    divider("DOWNSCALE -- Removing worker")
    current = get_vm_count()
    if current <= 0:
        print(r("  No Terraform-managed workers to remove.")); return
    wnum  = current + 1
    wname = f"k3s-worker-{wnum}"
    wip   = get_worker_ip(wnum)
    print(f"  Worker to remove : {wname}  ({wip})")
    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    divider("Step 1/3 -- Draining from k3s")
    drain_node(wname)

    divider("Step 2/3 -- Terraform scale down")
    set_vm_count(current - 1)
    os.chdir(TERRAFORM_DIR)
    run("terraform apply -auto-approve")

    divider("Step 3/3 -- Updating Ansible inventory")
    update_ansible_inventory()

    remaining_workers = (current - 1) + 1
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas={remaining_workers}"', capture=True)
    print(g(f"  scaled trengo-search to {remaining_workers} replicas"))
    print(f"\n{g('='*50)}\n{g(f'DOWNSCALE COMPLETE -- removed {wname}')}\n{g('='*50)}")

def do_sync_all():
    divider("SYNC IMAGES TO ALL WORKERS")
    workers = {"k3s-worker-1": K3S_WORKER1_IP}
    for i in range(get_vm_count()):
        wnum = i + 2
        workers[f"k3s-worker-{wnum}"] = get_worker_ip(wnum)
    for name, ip in sorted(workers.items()):
        sync_images(ip, name)
    print(g("\n  All workers synced."))

def do_rejoin():
    divider("REJOIN -- Re-attach workers to k3s")
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
            print(r(f"  SSH unavailable for {name}")); continue
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo rm -f /etc/rancher/node/password"', capture=True)
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl stop k3s-agent 2>/dev/null || true"', capture=True)
        join_k3s(ip, name)

    print(c("\n  Waiting for nodes to be Ready..."))
    time.sleep(30)
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    update_ansible_inventory()
    print(g("\n  Rejoin complete."))

def do_status():
    divider("CLUSTER STATUS")
    print(c("\n  K3s Nodes"))
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    print(c("\n  Terraform Workers"))
    vm_count = get_vm_count()
    print(f"  Terraform vm_count: {vm_count}")
    for i in range(vm_count):
        wnum = i + 2
        print(f"  k3s-worker-{wnum}: {get_worker_ip(wnum)}")
    print(c("\n  All VMs (virsh)"))
    run("virsh list --all")

# ─────────────────────────────────────────────────────────────
#  NUKE TEST
# ─────────────────────────────────────────────────────────────
def do_nuke_test():
    divider("NUKE TEST -- Full alert lifecycle")
    print(f"""
  This test will:
    {y('1.')} Scale trengo-search to 0 replicas
    {y('2.')} Wait for {c('TrengoAppDown')} CRITICAL alert to fire in Slack (~90s)
    {y('3.')} Scale trengo-search back to 1 replica
    {y('4.')} Wait for {g('RESOLVED')} notification in Slack (~60s)
    {y('5.')} Confirm alert cleared from Alertmanager

  {r('Watch')} #incidents in Slack during this test.
""")
    if input(y("  Proceed? (y/n): ")).lower() != 'y':
        print("  Cancelled."); return

    # Step 1 — Kill the app
    divider("Step 1/4 -- Scaling trengo-search to 0")
    result = run(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas=0 -n default"',
        capture=True
    )
    if result.returncode != 0:
        print(r(f"  Failed to scale down: {result.stderr.strip()}")); return
    print(g("  Deployment scaled to 0"))

    # Verify pods gone
    time.sleep(3)
    pods = run(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get pods -n default --no-headers 2>/dev/null | grep trengo"',
        capture=True
    ).stdout.strip()
    if pods:
        print(y(f"  Pods still terminating:\n  {pods}"))
    else:
        print(g("  All trengo pods terminated"))

    # Step 2 — Wait for alert
    divider("Step 2/4 -- Waiting for alert to fire")
    print(dim("  TrengoAppDown has for: 1m -- alert fires after 1 minute of 0 replicas"))
    print(dim("  Polling Alertmanager every 10s..."))
    alert_fired = False
    for i in range(18):  # max 3 minutes
        elapsed = i * 10
        print(f"  [{elapsed}s] checking Alertmanager...", end='\r', flush=True)
        result = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "curl -s http://localhost:30093/api/v2/alerts?active=true"',
            capture=True
        )
        if "TrengoAppDown" in result.stdout:
            print(f"\n{g('  ALERT FIRED')} at {elapsed}s elapsed")
            print(y("  Check #incidents in Slack -- CRITICAL | TrengoAppDown should be there"))
            alert_fired = True
            break
        time.sleep(10)

    if not alert_fired:
        print(r("\n  Alert did not fire within 3 minutes -- check Prometheus rules"))
        if input(y("  Continue anyway and restore the app? (y/n): ")).lower() != 'y':
            print(r("  WARNING: trengo-search is still at 0 replicas -- restore manually!"))
            return

    input(f"\n{c('  Press Enter when you have confirmed the Slack alert, then we restore the app...')}")

    # Step 3 — Restore
    divider("Step 3/4 -- Restoring trengo-search")
    result = run(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas=1 -n default"',
        capture=True
    )
    if result.returncode != 0:
        print(r(f"  Failed to scale up: {result.stderr.strip()}")); return
    print(g("  Deployment scaled back to 1"))

    print(dim("  Waiting for pod to be ready..."))
    for i in range(12):
        res = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get pods -n default --no-headers | grep trengo | grep Running"',
            capture=True
        )
        if res.stdout.strip():
            print(g(f"  Pod is Running: {res.stdout.strip()}"))
            break
        print(f"  [{i*5}s] waiting for pod...", end='\r', flush=True)
        time.sleep(5)

    # Step 4 — Wait for resolved
    divider("Step 4/4 -- Waiting for RESOLVED notification")
    print(dim(f"  resolve_timeout: 1m -- Alertmanager will send resolved within ~60s"))
    print(dim("  Polling Alertmanager every 10s..."))
    for i in range(18):  # max 3 minutes
        elapsed = i * 10
        print(f"  [{elapsed}s] checking Alertmanager...", end='\r', flush=True)
        result = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "curl -s http://localhost:30093/api/v2/alerts?active=true"',
            capture=True
        )
        if "TrengoAppDown" not in result.stdout:
            print(f"\n{g('  ALERT CLEARED from Alertmanager')} at {elapsed}s elapsed")
            print(g("  RESOLVED notification sent to Slack and GitLab issue auto-closed"))
            print(y("  Check #incidents -- RESOLVED | TrengoAppDown should be green"))
            break
        time.sleep(10)
    else:
        print(r("\n  Alert did not clear within 3 minutes"))
        print(dim("  Run: curl -s http://192.168.122.218:30093/api/v2/alerts?active=true | python3 -m json.tool"))

    # Summary
    divider("Nuke Test Complete")
    print(f"  {bold('Pipeline tested:')}")
    print(f"  {g('>')} Prometheus detected 0 replicas")
    print(f"  {g('>')} Alertmanager routed CRITICAL to Slack #incidents")
    print(f"  {g('>')} Alertmanager routed CRITICAL to GitLab webhook (issue created)")
    print(f"  {g('>')} App restored")
    print(f"  {g('>')} Alertmanager sent RESOLVED to Slack (green)")
    print(f"  {g('>')} GitLab issue auto-closed")

# ─────────────────────────────────────────────────────────────
#  RAM NUKE TEST
# ─────────────────────────────────────────────────────────────
def get_ci_ram():
    """Get ci-runner RAM usage as integer percent."""
    res = run(
        f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} '
        '"python3 -c \\"import subprocess; r=subprocess.check_output([\'free\']).decode().split()[7:13]; print(int((int(r[1])-int(r[5]))/int(r[1])*100))\\""',
        capture=True
    )
    try:
        return int(res.stdout.strip())
    except Exception:
        return 0

def wait_for_alert(alert_name, timeout_s=180, interval=10):
    """Poll Alertmanager until alert_name is active. Returns elapsed seconds or -1."""
    for i in range(timeout_s // interval):
        elapsed = i * interval
        mem = get_ci_ram()
        print(f"  [{elapsed}s] RAM: {y(str(mem)+'%')} — waiting for {c(alert_name)}...", end='\r', flush=True)
        result = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
            '"curl -s http://localhost:30093/api/v2/alerts?active=true"',
            capture=True
        )
        if alert_name in result.stdout:
            print(f"\n{g(f'  {alert_name} FIRED')} at {elapsed}s  RAM: {mem}%")
            return elapsed
        time.sleep(interval)
    return -1

def wait_for_resolved(alert_name, timeout_s=180, interval=10):
    """Poll Alertmanager until alert_name is gone. Returns True if resolved."""
    for i in range(timeout_s // interval):
        elapsed = i * interval
        mem = get_ci_ram()
        stress = run(
            f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} "pgrep -c stress-ng 2>/dev/null || echo 0"',
            capture=True
        ).stdout.strip()
        stress_gone = stress == "0"
        print(
            f"  [{elapsed}s] RAM: {g(str(mem)+'%')} "
            f"stress: {g('gone') if stress_gone else r('running')} "
            f"— waiting for RESOLVED...",
            end='\r', flush=True
        )
        result = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
            '"curl -s http://localhost:30093/api/v2/alerts?active=true"',
            capture=True
        )
        if alert_name not in result.stdout:
            print(f"\n{g(f'  {alert_name} RESOLVED')} at {elapsed}s  RAM: {mem}%")
            return True
        time.sleep(interval)
    return False

def do_ram_nuke_test():
    divider("RAM NUKE TEST -- Two-phase auto-remediation demo")
    print(f"""
  {bold('Phase 1')} — Stress to {y('~85%')}
    {y('>')} {c('NodeMemoryHigh')} fires after 30s  →  Slack CRITICAL alert + GitLab issue
    {y('>')} No auto-remediation yet — alert is visible in Slack

  {bold('Phase 2')} — Push to {r('~90%')}
    {y('>')} {c('NodeMemoryCritical')} fires after 30s
    {y('>')} Auto-remediation: {dim('pkill stress-ng + gitlab-ctl restart')}
    {y('>')} Memory drops, both alerts resolve, GitLab issues auto-closed

  {r('Watch')} #incidents in Slack during this test.
""")
    if input(y("  Proceed? (y/n): ")).lower() != 'y':
        print("  Cancelled."); return

    # ── Baseline ──────────────────────────────────────────────
    divider("Baseline — ci-runner memory")
    run(f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} "free -h"')

    # Ensure stress-ng is installed
    run(
        f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} '
        '"sudo apt-get install -y stress-ng -qq 2>/dev/null"',
        capture=True
    )

    # ── Dynamic calculation ───────────────────────────────────
    mem_raw = run(
        f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} "free -m"',
        capture=True
    ).stdout.strip().splitlines()
    mem_line = [x for x in mem_raw if x.startswith('Mem:')][0].split()
    total_mb  = int(mem_line[1])
    used_mb   = int(mem_line[2])
    current_pct = (used_mb / total_mb) * 100
    phase1_mb = int(total_mb * 0.87) - used_mb + 200   # aim for 87%
    phase2_mb = int(total_mb * 0.05) + 300              # push extra ~5% more

    print(f"  {bold('Node RAM state:')}  {y(str(total_mb))}MB total  |  {y(str(used_mb))}MB used  |  {y(f'{current_pct:.0f}%')} current")
    print(f"  {bold('Phase 1 stress:')} {g(str(phase1_mb))}MB  →  target ~87%")
    print(f"  {bold('Phase 2 stress:')} {g(str(phase2_mb))}MB  →  target ~92%")

    if phase1_mb <= 0:
        print(r("  Node already above 85% — no stress needed, alerts should already be firing"))
        return

    # ── Phase 1 — hit ~85% ────────────────────────────────────
    divider(f"Phase 1 — Stressing to ~87%  ({phase1_mb}MB)")
    run(
        f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} ' +
        f'"nohup sudo stress-ng --vm 1 --vm-bytes {phase1_mb}M --timeout 300s > /tmp/stress1.log 2>&1 & echo started"',
        capture=True
    )
    print(g(f"  Phase 1 stress started ({phase1_mb}MB)"))
    print(dim("  Waiting for NodeMemoryHigh to fire at 85%..."))

    fired = wait_for_alert("NodeMemoryHigh", timeout_s=180)
    if fired < 0:
        print(r("\n  NodeMemoryHigh did not fire — aborting"))
        run(f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} "sudo pkill -f stress-ng 2>/dev/null || true"', capture=True)
        return

    print(y("\n  Check #incidents in Slack — CRITICAL | NodeMemoryHigh should be there"))
    input(c("  Press Enter when you have confirmed the Slack alert to start Phase 2..."))

    # ── Phase 2 — push to ~92%+ ───────────────────────────────
    divider(f"Phase 2 — Pushing to ~92%+  (adding {phase2_mb}MB more)")
    run(
        f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} ' +
        f'"nohup sudo stress-ng --vm 1 --vm-bytes {phase2_mb}M --timeout 60s > /tmp/stress2.log 2>&1 & echo started"',
        capture=True
    )
    print(g(f"  Phase 2 stress started (additional {phase2_mb}MB)"))
    print(dim("  Waiting for NodeMemoryCritical to fire at 90%..."))

    fired2 = wait_for_alert("NodeMemoryCritical", timeout_s=180)
    if fired2 < 0:
        print(r("\n  NodeMemoryCritical did not fire"))
    else:
        print(y("  Auto-remediation triggered — pkill stress-ng + gitlab-ctl restart running"))
        print(dim("  Waiting for memory to drop and alerts to resolve..."))

    # ── Wait for both alerts to resolve ───────────────────────
    divider("Waiting for RESOLVED")
    resolved = wait_for_resolved("NodeMemoryCritical", timeout_s=300)
    if resolved:
        print(g("  Check #incidents — RESOLVED messages should be in Slack"))
    else:
        print(r("  Alerts did not resolve — cleaning up manually"))
        run(f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} "sudo pkill -f stress-ng 2>/dev/null || true"', capture=True)

    # ── Final state ───────────────────────────────────────────
    print(c("\n  Final ci-runner memory:"))
    run(f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} "free -h"')

    divider("RAM Nuke Test Complete")
    print(f"  {bold('Pipeline tested:')}")
    print(f"  {g('>')} Phase 1: stress-ng hit 85% — NodeMemoryHigh fired")
    print(f"  {g('>')} Phase 2: stress-ng hit 90% — NodeMemoryCritical fired")
    print(f"  {g('>')} Auto-remediation: pkill stress-ng + gitlab-ctl restart")
    print(f"  {g('>')} Memory recovered — both alerts resolved")
    print(f"  {g('>')} GitLab issues auto-created and auto-closed")

# ─────────────────────────────────────────────────────────────
#  ALERTING MENU
# ─────────────────────────────────────────────────────────────
def alerting_menu():
    while True:
        banner()
        print(f"""\
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('ALERTING TESTS')}                                           {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('1.')}  App Nuke Test      kill app, wait for alert,        {c('|')}
{c('|')}       {dim('               restore, confirm resolved')}             {c('|')}
{c('|')}  {bold('2.')}  RAM Nuke Test      stress ci-runner RAM,            {c('|')}
{c('|')}       {dim('               auto-remediation restarts GitLab')}      {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('0.')}  Back                                               {c('|')}
{c('+----------------------------------------------------------+')}""")
        choice = input(f"\n{y('Select option: ')}")
        if choice == '1':
            do_nuke_test()
            pause()
        elif choice == '2':
            do_ram_nuke_test()
            pause()
        elif choice == '0':
            break

# ─────────────────────────────────────────────────────────────
#  SERVICE LINKS
# ─────────────────────────────────────────────────────────────
def do_service_links():
    divider("SERVICE LINKS")
    B = f"{C.BLUE}{C.BOLD}"
    E = C.END
    print(f"""
  {bold(c('OBSERVABILITY'))}
  {b('-'*56)}
  {bold('Grafana')}          {g('http://192.168.122.218:30080')}
                    {dim('admin / see: kubectl get secret -n monitoring monitoring-grafana -o jsonpath="{.data.admin-password}" | base64 -d')}

  {bold('Prometheus')}       {g('http://192.168.122.218:30090')}
                    {dim('Alerts: /alerts  Targets: /targets  Rules: /rules')}

  {bold('Alertmanager')}     {g('http://192.168.122.218:30093')}
                    {dim('Active alerts, silences, inhibitions')}

  {bold('Loki')}             {dim('http://loki:3100  (ClusterIP only -- use Grafana Explore)')}

  {bold(c('CI/CD & CODE'))}
  {b('-'*56)}
  {bold('GitLab')}           {g('http://192.168.122.220')}
                    {dim('Source control, CI/CD pipelines, incident issues')}

  {bold('GitLab Runner')}    {dim('Registered on ci-runner (192.168.122.220)')}

  {bold(c('CLUSTER MANAGEMENT'))}
  {b('-'*56)}
  {bold('Portainer')}        {g('http://192.168.122.218:30777')}
                    {dim('Container/pod management UI')}

  {bold('K8s Dashboard')}    {g('https://192.168.122.218:30443')}
                    {dim('Token: kubectl -n kubernetes-dashboard create token admin-user')}

  {bold(c('APPLICATION'))}
  {b('-'*56)}
  {bold('Trengo Search')}    {g('http://192.168.122.218:32504')}
                    {dim('The app being deployed via CI/CD')}

  {bold(c('NODE SSH ACCESS'))}
  {b('-'*56)}
  {bold('k3s-control')}      {c('ssh andy@192.168.122.218')}
  {bold('k3s-infra')}        {c('ssh andy@192.168.122.230')}   {dim('(monitoring stack)')}
  {bold('k3s-worker-1')}     {c('ssh andy@192.168.122.219')}
  {bold('ci-runner')}        {c('ssh andy@192.168.122.220')}   {dim('(GitLab + Runner)')}
""")

# ─────────────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────────────
def main():
    dispatch = {
        '1':  lambda: (do_stop_all(),       pause()),
        '2':  lambda: (do_start_all(),      pause()),
        '3':  lambda: (do_health_check(),   pause()),
        '4':  scale_menu,
        '5':  ansible_menu,
        '6':  lambda: (do_sync_all(),       pause()),
        '7':  lambda: (do_rejoin(),         pause()),
        '8':  lambda: (do_status(),         pause()),
        '9':  alerting_menu,
        '10': lambda: (do_service_links(),  pause()),
    }

    while True:
        choice = main_menu()
        if choice == '0':
            print(g("\nGoodbye.\n"))
            sys.exit(0)
        action = dispatch.get(choice)
        if action:
            action()
        else:
            print(r("  Invalid option"))
            time.sleep(1)

if __name__ == "__main__":
    main()
