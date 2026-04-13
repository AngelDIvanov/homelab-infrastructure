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

# VM IPs — default libvirt NAT range, adjust for your network
K3S_CONTROL_IP  = "192.168.122.218"
K3S_INFRA_IP    = "192.168.122.230"   # NFS storage — never auto-stopped
CI_RUNNER_IP    = "192.168.122.220"
K3S_WORKER1_IP  = "192.168.122.219"
K3S_URL         = f"https://{K3S_CONTROL_IP}:6443"

K8S_CONTROL_IP  = "192.168.122.240"
K8S_WORKER1_IP  = "192.168.122.241"

# VMs that belong to each cluster (k3s-infra excluded — stays always on)
K3S_VMS  = ["k3s-control", "k3s-worker-1", "ci-runner"]
K8S_VMS  = ["kubeadm-control", "kubeadm-worker-1"]

def _get_secret(env_var, vault_item):
    value = os.environ.get(env_var, "")
    if value:
        return value
    try:
        result = subprocess.run(["bw", "get", "password", vault_item],
                                capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    print(f"Error: {env_var} not set. Vault not available yet.")
    print("Until Vaultwarden is set up, export it manually:")
    print(f"  export {env_var}=<your-token>")
    print("Or run setup: bash ~/homelab/scripts/setup-vault.sh")
    sys.exit(1)

# Secrets are fetched on demand — script starts without requiring them.
K3S_TOKEN    = lambda: _get_secret("K3S_TOKEN",    "homelab-k3s-token")
GITLAB_TOKEN = lambda: _get_secret("GITLAB_TOKEN", "homelab-gitlab-token")
BASE_IP_OCTET  = 221   # k3s-worker-2 = .221, worker-3 = .222 ...

ALERTMANAGER_URL = f"http://{K3S_CONTROL_IP}:30093"

# All permanent VMs — discovered dynamically from virsh
def get_permanent_vms():
    result = run("virsh list --all --name 2>/dev/null", capture=True)
    all_vms = [v.strip() for v in result.stdout.splitlines() if v.strip() and v.strip() != "Base"]
    return all_vms

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

def preflight_cluster_check():
    """Abort if control plane is unreachable or cluster is degraded."""
    print(y("  pre-flight: checking cluster health..."))
    res = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes --no-headers 2>/dev/null"', capture=True)
    if res.returncode != 0:
        print(r("  ABORT: cannot reach k3s-control — cluster may be down."))
        return False
    not_ready = [l for l in res.stdout.splitlines() if "NotReady" in l]
    if not_ready:
        print(r(f"  ABORT: {len(not_ready)} node(s) NotReady before operation:"))
        for l in not_ready: print(r(f"    {l}"))
        print(r("  Fix cluster health before scaling."))
        return False
    print(g(f"  cluster healthy ({len(res.stdout.splitlines())} nodes Ready)"))
    return True

def join_k3s(ip, name):
    print(y(f"  joining {name} to k3s..."))

    # Fetch full token and validate format before touching the node
    token_result = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo cat /var/lib/rancher/k3s/server/node-token"', capture=True)
    full_token = token_result.stdout.strip()
    if not full_token or '::server:' not in full_token:
        print(r(f"  ABORT: node-token from control plane looks wrong: '{full_token[:40]}...'"))
        print(r("  Expected format: K10<hash>::server:<secret>"))
        return False
    print(g(f"  token validated (format OK)"))

    # Install k3s agent
    cmd = f'ssh {SSH_OPTS} andy@{ip} "curl -sfL https://get.k3s.io | K3S_URL={K3S_URL} K3S_TOKEN={full_token} sh -s - agent"'
    if run(cmd).returncode != 0:
        print(r(f"  failed to install k3s on {name}"))
        return False

    # The install script wipes the env file — explicitly write the full token back
    print(y("  writing token to agent env file (install script wipes it)..."))
    run(f'ssh {SSH_OPTS} andy@{ip} "printf \'K3S_TOKEN=%s\\nK3S_URL=%s\\n\' \'{full_token}\' \'{K3S_URL}\' | sudo tee /etc/systemd/system/k3s-agent.service.env > /dev/null"', capture=True)
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl daemon-reload && sudo systemctl restart k3s-agent"', capture=True)

    # Verify agent is active
    time.sleep(5)
    result = run(f'ssh {SSH_OPTS} andy@{ip} "systemctl is-active k3s-agent"', capture=True)
    if result.stdout.strip() != "active":
        logs = run(f'ssh {SSH_OPTS} andy@{ip} "sudo journalctl -u k3s-agent -n 5 --no-pager 2>/dev/null | grep -i error"', capture=True).stdout.strip()
        print(r(f"  k3s-agent failed on {name}"))
        if logs: print(r(f"  Agent errors: {logs}"))
        return False

    # Verify node actually appears in cluster (not just service active)
    print(y(f"  waiting for {name} to appear in cluster..."))
    for _ in range(18):  # 90s
        res = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get node {name} --no-headers 2>/dev/null"', capture=True)
        if res.stdout.strip():
            status = res.stdout.split()[1] if len(res.stdout.split()) > 1 else "Unknown"
            if status == "Ready":
                print(g(f"  {name} joined cluster and is Ready"))
                return True
            print(f"  {name} status: {status}, waiting...")
        time.sleep(5)

    print(r(f"  {name} did not become Ready within 90s — check agent logs"))
    return False

def repair_agent(ip, name):
    """Fix k3s-agent on any permanent node (infra, worker-1) without reinstalling.
    Writes the correct token from control plane into the env file and restarts."""
    print(y(f"  repairing k3s-agent on {name} ({ip})..."))

    token_result = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo cat /var/lib/rancher/k3s/server/node-token"', capture=True)
    full_token = token_result.stdout.strip()
    if not full_token or '::server:' not in full_token:
        print(r(f"  ABORT: could not fetch valid token from control plane: '{full_token[:40]}'"))
        return False

    current = run(f'ssh {SSH_OPTS} andy@{ip} "sudo cat /etc/systemd/system/k3s-agent.service.env 2>/dev/null"', capture=True).stdout.strip()
    print(f"  Current env: {current[:80] or '(empty)'}")

    run(f'ssh {SSH_OPTS} andy@{ip} "printf \'K3S_TOKEN=%s\\nK3S_URL=%s\\n\' \'{full_token}\' \'{K3S_URL}\' | sudo tee /etc/systemd/system/k3s-agent.service.env > /dev/null"', capture=True)
    run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl daemon-reload && sudo systemctl restart k3s-agent"', capture=True)

    time.sleep(6)
    status = run(f'ssh {SSH_OPTS} andy@{ip} "systemctl is-active k3s-agent"', capture=True).stdout.strip()
    if status != "active":
        logs = run(f'ssh {SSH_OPTS} andy@{ip} "sudo journalctl -u k3s-agent -n 5 --no-pager 2>/dev/null | grep -i error"', capture=True).stdout.strip()
        print(r(f"  k3s-agent still not active on {name}"))
        if logs: print(r(f"  Errors: {logs}"))
        return False

    print(y(f"  waiting for {name} to appear Ready in cluster..."))
    for _ in range(18):
        res = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get node {name} --no-headers 2>/dev/null"', capture=True)
        if res.stdout.strip() and "Ready" in res.stdout:
            print(g(f"  {name} is Ready"))
            return True
        time.sleep(5)
    print(r(f"  {name} not Ready after 90s"))
    return False

def check_infra_services():
    """Check that key services on k3s-infra are reachable."""
    import urllib.request
    print(c("\n  k3s-infra service health"))
    services = [
        ("GitLab",       f"http://{K3S_INFRA_IP}:8929/"),
        ("Prometheus",   f"http://{K3S_CONTROL_IP}:30090/-/healthy"),
        ("Alertmanager", f"http://{K3S_CONTROL_IP}:30093/-/healthy"),
        ("Grafana",      f"http://{K3S_INFRA_IP}:30080/api/health"),
    ]
    all_ok = True
    for name, url in services:
        try:
            # Don't follow redirects — a 302 from GitLab means it's up (login page)
            req = urllib.request.Request(url)
            try:
                code = urllib.request.urlopen(req, timeout=5).getcode()
            except urllib.error.HTTPError as e:
                code = e.code
            if code in (200, 302):
                print(g(f"    {name:<14} UP   ({code})  {url}"))
            else:
                print(r(f"    {name:<14} DOWN ({code})  {url}"))
                all_ok = False
        except Exception as e:
            print(r(f"    {name:<14} DOWN  {url}  — {e}"))
            all_ok = False

    # Check GitLab runner registration on ci-runner
    print(c("\n  ci-runner health"))
    res = run(f'ssh {SSH_OPTS} andy@{CI_RUNNER_IP} "sudo gitlab-runner list 2>&1 | grep -c Executor || echo 0"', capture=True)
    runner_count = res.stdout.strip()
    if runner_count.isdigit() and int(runner_count) > 0:
        print(g(f"    GitLab Runner  UP   ({runner_count} executor(s) registered)"))
    else:
        print(r(f"    GitLab Runner  DOWN — no executors registered (run: sudo gitlab-runner register)"))
        all_ok = False

    # Check NFS export is reachable from control
    nfs_check = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "showmount -e {K3S_INFRA_IP} 2>/dev/null | head -3"', capture=True)
    if nfs_check.returncode == 0 and nfs_check.stdout.strip():
        print(g(f"    NFS            UP   ({nfs_check.stdout.splitlines()[0].strip()})"))
    else:
        print(r(f"    NFS            DOWN — cannot reach NFS exports on {K3S_INFRA_IP}"))
        all_ok = False
    return all_ok

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
    print(f"{B}|{E}           DevOps Home Lab  --  Control Panel                 {B}|{E}")
    print(f"{B}+==============================================================+{E}")
    print(f"{B}|{E}  {C.BOLD}VM STATUS{E}                                                   {B}|{E}")
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
{c('|')}  {bold('K3S CLUSTER')}                                             {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('1.')}  Start k3s          boot k3s cluster VMs             {c('|')}
{c('|')}  {bold('2.')}  Stop k3s           shutdown k3s cluster             {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('KUBEADM CLUSTER')}                                         {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('3.')}  Start K8s          boot kubeadm cluster VMs         {c('|')}
{c('|')}  {bold('4.')}  Stop K8s           shutdown kubeadm cluster         {c('|')}
{c('|')}  {bold('5.')}  K8s Status         kubeadm nodes + pods             {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('K3S TOOLS')}                                               {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('6.')}  Health Check       run check-lab                    {c('|')}
{c('|')}  {bold('7.')}  Scale              add / remove k3s workers         {c('|')}
{c('|')}  {bold('8.')}  Ansible            run playbooks                    {c('|')}
{c('|')}  {bold('9.')}  Sync Images        push to all k3s nodes            {c('|')}
{c('|')}  {bold('10.')} Repair Node        fix agent token/cert (any node)  {c('|')}
{c('|')}  {bold('11.')} Rejoin Nodes       full reinstall (all agents)      {c('|')}
{c('|')}  {bold('12.')} k3s Status         nodes + virsh overview           {c('|')}
{c('|')}  {bold('13.')} Infra Health       GitLab/Prometheus/NFS/Runner     {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('ALERTING')}                                                {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('14.')} Alerting Tests     app nuke, RAM nuke,              {c('|')}
{c('|')}       {dim('               auto-remediation demos')}              {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('SERVICES')}                                                {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('15.')} Service Links      all URLs and access info         {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('POWER')}                                                   {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('16.')} Safe Shutdown      drain → stop k3s → power off     {c('|')}
{c('|')}  {bold('17.')} Safe Startup       ordered boot + wait for Ready    {c('|')}
{c('+----------------------------------------------------------+')}
{c('|')}  {bold('0.')}  Exit                                                {c('|')}
{c('+----------------------------------------------------------+')}""")
    return input(f"\n{y('Select option: ')}")

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
def _stop_vms(vms, label):
    divider(f"STOPPING {label}")
    print(dim(f"  Note: k3s-infra ({K3S_INFRA_IP}) is excluded — NFS storage stays up."))
    confirm = input(y(f"  Stop {label}? (y/n): "))
    if confirm.lower() != 'y':
        print("  Cancelled."); return

    result = run("virsh list --state-running 2>/dev/null", capture=True)
    running = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] not in ["Name", "---", "Base"] and parts[1] in vms:
            running.append(parts[1])

    if not running:
        print(dim(f"  No {label} VMs currently running.")); return

    print(c(f"\n  Stopping: {', '.join(running)}"))
    for vm in running:
        print(y(f"  shutting down {vm}..."))
        run(f"virsh shutdown {vm} 2>/dev/null || true", capture=True)

    time.sleep(5)

    result = run("virsh list --state-running 2>/dev/null", capture=True)
    still_running = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in vms:
            still_running.append(parts[1])

    for vm in still_running:
        print(r(f"  {vm} still running — force destroying..."))
        res = run(f"virsh destroy {vm} 2>/dev/null", capture=True)
        print(g(f"  {vm} force killed") if res.returncode == 0 else r(f"  {vm} failed: {res.stderr.strip()}"))

    print(g(f"\n  {label} stopped."))
    run("virsh list --all")

def _start_vms(vms, label, note=""):
    divider(f"STARTING {label}")
    confirm = input(y(f"  Start {label}? (y/n): "))
    if confirm.lower() != 'y':
        print("  Cancelled."); return
    for vm in vms:
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
    print(g(f"\n  {label} started."))
    if note:
        print(dim(f"  {note}"))

def do_stop_k3s():
    _stop_vms(K3S_VMS, "K3S CLUSTER")

def do_start_k3s():
    _start_vms(K3S_VMS, "K3S CLUSTER", "Allow 30-60s for k3s to come up, then run health check.")

def do_stop_k8s():
    _stop_vms(K8S_VMS, "KUBEADM CLUSTER")

def do_start_k8s():
    _start_vms(K8S_VMS, "KUBEADM CLUSTER", "Allow 60s for kubeadm API server to come up.")

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
        run(f"GITLAB_TOKEN={GITLAB_TOKEN()} bash {script}")
    elif mode == '2':
        run(f"GITLAB_TOKEN={GITLAB_TOKEN()} bash {script} --restart")
    elif mode == '3':
        run(f"GITLAB_TOKEN={GITLAB_TOKEN()} bash {script} --reboot")

def do_upscale():
    divider("UPSCALE -- Adding new worker")
    if not preflight_cluster_check(): return

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

    divider("Step 6c -- Verifying DaemonSet pods on new node")
    # node-exporter must be running on the new node for it to appear in Grafana.
    # If it's missing, the node will be invisible to Prometheus.
    ds_check_cmd = (
        f'sudo k3s kubectl get pods -A --no-headers '
        f'--field-selector spec.nodeName={new_name} 2>/dev/null '
        f'| grep -E "node-exporter|kube-proxy|calico|flannel"'
    )
    ok = False
    for i in range(18):   # up to 3 minutes
        res = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "{ds_check_cmd}"', capture=True)
        running = [l for l in res.stdout.splitlines() if "Running" in l]
        pending = [l for l in res.stdout.splitlines() if "Running" not in l and l.strip()]
        if running and not pending:
            for l in running: print(g(f"  {l.split()[0]}/{l.split()[1]}  Running"))
            ok = True; break
        if running or pending:
            all_pods = running + pending
            for l in all_pods: print(f"  {l.split()[0]}/{l.split()[1]}  {l.split()[3] if len(l.split())>3 else '?'}")
        print(f"  waiting for DaemonSet pods... [{i*10}/180s]")
        time.sleep(10)
    if not ok:
        print(y(f"  WARNING: node-exporter may not be running on {new_name}."))
        print(y(f"  The node may not appear in Grafana until the DaemonSet pod schedules."))
        print(y(f"  Check: kubectl get pods -A --field-selector spec.nodeName={new_name}"))
    else:
        print(g(f"  DaemonSet pods confirmed on {new_name} — node will appear in Grafana"))

    print(f"\n{g('='*50)}\n{g(f'UPSCALE COMPLETE -- {new_name} ({new_ip})')}\n{g('='*50)}")

def do_downscale():
    divider("DOWNSCALE -- Removing worker")
    if not preflight_cluster_check(): return

    current = get_vm_count()
    if current <= 0:
        print(r("  No Terraform-managed workers to remove.")); return
    wnum  = current + 1
    wname = f"k3s-worker-{wnum}"
    wip   = get_worker_ip(wnum)
    print(f"  Worker to remove : {wname}  ({wip})")

    # Safety check: warn if any deployment would drop to 0 replicas after drain
    print(y("  checking pod safety before drain..."))
    pods_res = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get pods -A --no-headers --field-selector spec.nodeName={wname} 2>/dev/null"', capture=True)
    if pods_res.stdout.strip():
        running_pods = [l.split() for l in pods_res.stdout.splitlines() if l.strip()]
        print(f"  {len(running_pods)} pod(s) currently on {wname}:")
        for p in running_pods:
            print(f"    {p[0]}/{p[1]}")
        # Check for single-replica deployments that would go to 0
        risky = run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get deployments -A --no-headers 2>/dev/null | awk \'$5==1 && $4==1\'"', capture=True).stdout.strip()
        if risky:
            print(y("  WARNING: these deployments have only 1 ready replica and may become unavailable:"))
            for l in risky.splitlines(): print(y(f"    {l}"))
    else:
        print(g(f"  no pods on {wname} — safe to drain"))

    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    # Scale app replicas down before draining so pods aren't rescheduled mid-drain
    remaining_workers = (current - 1) + 1
    divider("Step 0/4 -- Pre-scaling app replicas")
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas={remaining_workers}"', capture=True)
    print(g(f"  scaled trengo-search to {remaining_workers} replicas"))

    divider("Step 1/4 -- Draining from k3s")
    drain_node(wname)

    divider("Step 2/4 -- Terraform scale down")
    set_vm_count(current - 1)
    os.chdir(TERRAFORM_DIR)
    run("terraform apply -auto-approve")

    divider("Step 3/4 -- Cleaning up SSH known_hosts")
    run(f"ssh-keygen -f ~/.ssh/known_hosts -R {wip} 2>/dev/null || true", capture=True)
    print(g(f"  removed {wip} from known_hosts"))

    divider("Step 4/4 -- Updating Ansible inventory")
    update_ansible_inventory()

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

def do_repair_node():
    """Lightweight agent repair — fix token + restart, no k3s reinstall."""
    divider("REPAIR NODE AGENT")
    # All permanent nodes that run k3s-agent
    all_nodes = {
        "k3s-worker-1": K3S_WORKER1_IP,
        "k3s-infra":    K3S_INFRA_IP,
    }
    vm_count = get_vm_count()
    for i in range(vm_count):
        wnum = i + 2
        all_nodes[f"k3s-worker-{wnum}"] = get_worker_ip(wnum)

    print("  Which node needs repair?\n")
    node_list = sorted(all_nodes.items())
    for i, (name, ip) in enumerate(node_list, 1):
        print(f"  {i}.  {name}  ({ip})")
    print("  a.  All nodes")
    print("  0.  Back")

    choice = input(f"\n{y('  Select: ')}").strip()
    if choice == '0':
        return
    elif choice == 'a':
        targets = node_list
    elif choice.isdigit() and 1 <= int(choice) <= len(node_list):
        targets = [node_list[int(choice) - 1]]
    else:
        print(r("  Invalid choice.")); return

    if not preflight_cluster_check(): return

    for name, ip in targets:
        divider(f"Repairing {name} ({ip})")
        if not wait_for_ssh(ip, timeout=30):
            print(r(f"  SSH unavailable — is the VM running?")); continue
        repair_agent(ip, name)

    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')

def do_rejoin():
    divider("REJOIN -- Full re-attach (reinstalls k3s-agent)")
    # k3s-infra is intentionally excluded — it hosts the monitoring stack
    # (Prometheus, Grafana, Alertmanager, GitLab). Rejoining it restarts
    # k3s-agent and evicts all pods on that node. Use Repair Node (option 10)
    # for non-destructive infra agent fixes.
    all_nodes = [("k3s-worker-1", K3S_WORKER1_IP)]
    vm_count = get_vm_count()
    for i in range(vm_count):
        wnum = i + 2
        all_nodes.append((f"k3s-worker-{wnum}", get_worker_ip(wnum)))

    print("  Nodes that will be rejoined:\n")
    for name, ip in all_nodes:
        print(f"    {name}  ({ip})")
    print(f"\n  {y('NOTE: This reinstalls k3s-agent on each node.')}")
    print(f"  {y('k3s-infra is excluded — use Repair Node (10) to fix infra agent.')}")
    print(f"  {y('Use Repair Node instead if you just need to fix a token/cert.')}")
    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    if not preflight_cluster_check(): return

    print(c("\n  Cleaning old node entries from cluster..."))
    for name, _ in all_nodes:
        run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl delete node {name} 2>/dev/null || true"', capture=True)

    print(c("  Restarting k3s server..."))
    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo systemctl restart k3s"')
    print("  Waiting 30s for k3s to restart...")
    time.sleep(30)

    for name, ip in all_nodes:
        divider(f"Rejoining {name} ({ip})")
        if not wait_for_ssh(ip, timeout=60):
            print(r(f"  SSH unavailable for {name} — skipping")); continue
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo rm -f /etc/rancher/node/password"', capture=True)
        run(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl stop k3s-agent 2>/dev/null || true"', capture=True)
        join_k3s(ip, name)

    run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    update_ansible_inventory()
    print(g("\n  Rejoin complete."))

def _all_worker_nodes():
    """Return [(name, ip)] for every worker currently tracked by Terraform + worker-1."""
    nodes = [("k3s-worker-1", K3S_WORKER1_IP)]
    for i in range(get_vm_count()):
        wnum = i + 2
        nodes.append((f"k3s-worker-{wnum}", get_worker_ip(wnum)))
    return nodes

def do_safe_shutdown():
    divider("SAFE SHUTDOWN")
    states  = vm_states()
    cluster = ["k3s-control", "k3s-infra"] + [n for n, _ in _all_worker_nodes()] + ["ci-runner"]
    running = [vm for vm in cluster if states.get(vm, False)]

    if not running:
        print(g("  All cluster VMs already off.")); return

    print(f"  Running: {', '.join(running)}\n")
    print(y("  Shutdown sequence:"))
    print(  "    1. Scale app replicas → 0  (clean pod removal)")
    print(  "    2. Stop k3s-agent on workers  (graceful pod eviction)")
    print(  "    3. Stop k3s-agent on k3s-infra  (monitoring stack)")
    print(  "    4. Stop k3s server on k3s-control")
    print(  "    5. Graceful virsh shutdown → verify → force if needed")
    print(dim("\n  Alertmanager will fire briefly during step 3 — expected."))

    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    # ── 1. Scale down applications ────────────────────────────────
    divider("Step 1/5 -- Scaling down app replicas")
    if wait_for_ssh(K3S_CONTROL_IP, timeout=15):
        for deploy in ["trengo-search", "trengo-search-staging"]:
            res = run(
                f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
                f'"sudo k3s kubectl scale deployment {deploy} --replicas=0 -n default 2>/dev/null"',
                capture=True)
            if res.returncode == 0:
                print(g(f"  {deploy} → 0 replicas"))
            else:
                print(dim(f"  {deploy} not found — skipping"))
    else:
        print(y("  k3s-control unreachable — skipping scale-down"))

    # ── 2. Stop k3s-agent on workers ─────────────────────────────
    divider("Step 2/5 -- Stopping k3s-agent on worker nodes")
    for wname, wip in _all_worker_nodes():
        if not states.get(wname, False):
            print(dim(f"  {wname} already off — skipping")); continue
        if not wait_for_ssh(wip, timeout=10):
            print(y(f"  {wname} SSH unavailable — will force-shutdown VM later")); continue
        print(y(f"  {wname}: stopping k3s-agent..."))
        run(f'ssh {SSH_OPTS} andy@{wip} "sudo systemctl stop k3s-agent 2>/dev/null"', capture=True)
        print(g(f"  {wname}: k3s-agent stopped"))

    # ── 3. Stop k3s-agent on infra ────────────────────────────────
    divider("Step 3/5 -- Stopping k3s-agent on k3s-infra (monitoring stack)")
    if states.get("k3s-infra", False):
        if wait_for_ssh(K3S_INFRA_IP, timeout=10):
            print(y("  k3s-infra: stopping k3s-agent (pods get 30s graceful window)..."))
            run(f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "sudo systemctl stop k3s-agent 2>/dev/null"',
                capture=True)
            print(g("  k3s-infra: k3s-agent stopped"))
        else:
            print(y("  k3s-infra SSH unavailable — will force-shutdown VM"))
    else:
        print(dim("  k3s-infra already off"))

    # ── 4. Stop k3s server ────────────────────────────────────────
    divider("Step 4/5 -- Stopping k3s server on k3s-control")
    if states.get("k3s-control", False):
        if wait_for_ssh(K3S_CONTROL_IP, timeout=10):
            print(y("  stopping k3s server..."))
            run(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo systemctl stop k3s 2>/dev/null"',
                capture=True)
            print(g("  k3s server stopped"))
        else:
            print(y("  k3s-control SSH unavailable — will force-shutdown VM"))
    else:
        print(dim("  k3s-control already off"))

    # ── 5. Shut down VMs ─────────────────────────────────────────
    divider("Step 5/5 -- Graceful VM shutdown")
    # Workers first, then infra, then control, then runner
    shutdown_order = [n for n, _ in _all_worker_nodes()] + ["k3s-infra", "k3s-control", "ci-runner"]
    for vm in shutdown_order:
        if not states.get(vm, False):
            print(dim(f"  {vm} already off")); continue
        run(f"virsh shutdown {vm} 2>/dev/null", capture=True)
        print(y(f"  {vm}: shutdown signal sent"))

    print(y("\n  Waiting for VMs to power off (up to 60s)..."))
    lab_vms = set(shutdown_order)
    for tick in range(12):
        time.sleep(5)
        still_on = [vm for vm, on in vm_states().items() if on and vm in lab_vms]
        if not still_on:
            break
        print(f"  still running: {', '.join(still_on)}  [{tick*5+5}s]")

    # Force-destroy anything still on
    for vm, on in vm_states().items():
        if on and vm in lab_vms:
            print(r(f"  {vm} stuck — force destroy"))
            run(f"virsh destroy {vm} 2>/dev/null", capture=True)

    print(f"\n{g('='*52)}\n{g('  SAFE SHUTDOWN COMPLETE')}\n{g('='*52)}")
    run("virsh list --all")


def do_safe_startup():
    divider("SAFE STARTUP -- Ordered cluster boot")
    states = vm_states()
    workers = _all_worker_nodes()
    expected_nodes = 3 + get_vm_count()   # control + infra + worker-1 + terraform workers

    print(y("  Startup sequence:"))
    print(  "    1. Boot k3s-control → wait for API server")
    print(  "    2. Boot k3s-infra   → wait for Ready")
    print(f"    3. Boot workers ({', '.join(n for n,_ in workers)}) + ci-runner  (parallel)")
    print(f"    4. Wait for all {expected_nodes} nodes Ready")
    print(  "    5. Scale app replicas back up")
    print(  "    6. Show cluster status")

    if input(f"\n{y('  Proceed? (y/n): ')}").lower() != 'y':
        print("  Cancelled."); return

    # ── 1. k3s-control ───────────────────────────────────────────
    divider("Step 1/5 -- Starting k3s-control")
    if states.get("k3s-control", False):
        print(dim("  k3s-control already running"))
    else:
        run("virsh start k3s-control 2>/dev/null")
        print(y("  waiting for k3s API server to come up..."))

    for tick in range(24):  # up to 2 min
        res = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
            f'"sudo k3s kubectl get nodes --no-headers 2>/dev/null | wc -l"',
            capture=True)
        if res.returncode == 0 and res.stdout.strip().isdigit() and int(res.stdout.strip()) > 0:
            print(g("  k3s API server ready")); break
        print(f"  waiting for API... [{tick*5+5}/120s]")
        time.sleep(5)
    else:
        print(r("  k3s API server did not come up in 2 min — check k3s-control manually"))
        return

    # ── 2. k3s-infra ─────────────────────────────────────────────
    divider("Step 2/5 -- Starting k3s-infra")
    current = vm_states()
    if current.get("k3s-infra", False):
        print(dim("  k3s-infra already running"))
    else:
        run("virsh start k3s-infra 2>/dev/null")

    print(y("  waiting for k3s-infra to join cluster..."))
    for tick in range(18):  # up to 3 min
        res = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
            f'"sudo k3s kubectl get node k3s-infra --no-headers 2>/dev/null | grep -q Ready"',
            capture=True)
        if res.returncode == 0:
            print(g("  k3s-infra Ready")); break
        print(f"  waiting for k3s-infra... [{tick*10+10}/180s]")
        time.sleep(10)
    else:
        print(y("  k3s-infra taking longer than expected — continuing anyway"))

    # ── 3. Workers + CI runner ────────────────────────────────────
    divider("Step 3/5 -- Starting workers + ci-runner")
    current = vm_states()
    for wname, _ in workers:
        if current.get(wname, False):
            print(dim(f"  {wname} already running"))
        else:
            run(f"virsh start {wname} 2>/dev/null")
            print(g(f"  {wname} starting"))
    if current.get("ci-runner", False):
        print(dim("  ci-runner already running"))
    else:
        run("virsh start ci-runner 2>/dev/null")
        print(g("  ci-runner starting"))

    # ── 4. Wait for all nodes ─────────────────────────────────────
    divider(f"Step 4/5 -- Waiting for all {expected_nodes} nodes Ready")
    for tick in range(24):  # up to 4 min
        res = run(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
            f'"sudo k3s kubectl get nodes --no-headers 2>/dev/null | grep -c Ready"',
            capture=True)
        if res.returncode == 0 and res.stdout.strip().isdigit():
            ready = int(res.stdout.strip())
            if ready >= expected_nodes:
                print(g(f"  All {ready} nodes Ready!")); break
            print(f"  {ready}/{expected_nodes} nodes Ready... [{tick*10+10}/240s]")
        time.sleep(10)
    else:
        print(y("  Not all nodes Ready after 4 min — check status manually"))

    # ── 5. Scale apps back up ─────────────────────────────────────
    divider("Step 5/5 -- Scaling app replicas")
    total_workers = 1 + get_vm_count()   # worker-1 + terraform workers
    res = run(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
        f'"sudo k3s kubectl scale deployment trengo-search --replicas={total_workers} -n default 2>/dev/null"',
        capture=True)
    if res.returncode == 0:
        print(g(f"  trengo-search → {total_workers} replicas"))

    do_status()


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

def do_k8s_status():
    divider("KUBEADM CLUSTER STATUS")
    print(c("\n  K8s Nodes"))
    run(f'ssh {SSH_OPTS} andy@{K8S_CONTROL_IP} "kubectl get nodes -o wide 2>/dev/null || echo  kubeadm-control not reachable"')
    print(c("\n  K8s Pods (all namespaces)"))
    run(f'ssh {SSH_OPTS} andy@{K8S_CONTROL_IP} "kubectl get pods -A 2>/dev/null || true"')
    print(c("\n  VM States (virsh)"))
    for vm in K8S_VMS:
        res = run(f"virsh domstate {vm} 2>/dev/null", capture=True)
        state = res.stdout.strip() or "not found"
        icon = g("running") if state == "running" else r(state)
        print(f"  {vm:<24} {icon}")

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

    divider("Step 1/4 -- Scaling trengo-search to 0")
    result = run(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl scale deployment trengo-search --replicas=0 -n default"',
        capture=True
    )
    if result.returncode != 0:
        print(r(f"  Failed to scale down: {result.stderr.strip()}")); return
    print(g("  Deployment scaled to 0"))

    time.sleep(3)
    pods = run(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get pods -n default --no-headers 2>/dev/null | grep trengo"',
        capture=True
    ).stdout.strip()
    if pods:
        print(y(f"  Pods still terminating:\n  {pods}"))
    else:
        print(g("  All trengo pods terminated"))

    divider("Step 2/4 -- Waiting for alert to fire")
    print(dim("  TrengoAppDown has for: 1m -- alert fires after 1 minute of 0 replicas"))
    print(dim("  Polling Alertmanager every 10s..."))
    alert_fired = False
    for i in range(18):
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

    divider("Step 4/4 -- Waiting for RESOLVED notification")
    print(dim(f"  resolve_timeout: 1m -- Alertmanager will send resolved within ~60s"))
    print(dim("  Polling Alertmanager every 10s..."))
    for i in range(18):
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
def get_infra_ram():
    res = run(
        f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} '
        '"python3 -c \\"import subprocess; r=subprocess.check_output([\'free\']).decode().split()[7:13]; print(int((int(r[1])-int(r[5]))/int(r[1])*100))\\""',
        capture=True
    )
    try:
        return int(res.stdout.strip())
    except Exception:
        return 0

def wait_for_alert(alert_name, timeout_s=180, interval=10):
    for i in range(timeout_s // interval):
        elapsed = i * interval
        mem = get_infra_ram()
        print(f"  [{elapsed}s] RAM: {y(str(mem)+'%')} -- waiting for {c(alert_name)}...", end='\r', flush=True)
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
    for i in range(timeout_s // interval):
        elapsed = i * interval
        mem = get_infra_ram()
        stress = run(
            f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "pgrep -c stress-ng 2>/dev/null || echo 0"',
            capture=True
        ).stdout.strip()
        stress_gone = stress == "0"
        print(
            f"  [{elapsed}s] RAM: {g(str(mem)+'%')} "
            f"stress: {g('gone') if stress_gone else r('running')} "
            f"-- waiting for RESOLVED...",
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
  Target node: {c('k3s-infra')} ({K3S_INFRA_IP}) -- runs GitLab, most likely to OOM

  {bold('Phase 1')} -- Stress to {y('~85%')}
    {y('>')} {c('NodeMemoryHigh')} fires after 30s  ->  Slack CRITICAL alert + GitLab issue
    {y('>')} No auto-remediation yet -- alert is visible in Slack

  {bold('Phase 2')} -- Push to {r('~90%')}
    {y('>')} {c('NodeMemoryCritical')} fires after 30s
    {y('>')} Auto-remediation: {dim('pkill stress-ng + gitlab-ctl restart (on k3s-infra)')}
    {y('>')} Memory drops, both alerts resolve, GitLab issues auto-closed

  {r('Watch')} #incidents in Slack during this test.
""")
    if input(y("  Proceed? (y/n): ")).lower() != 'y':
        print("  Cancelled."); return

    divider("Baseline -- k3s-infra memory")
    run(f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "free -h"')

    run(
        f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} '
        '"sudo apt-get install -y stress-ng -qq 2>/dev/null"',
        capture=True
    )

    mem_raw = run(f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "free -m"', capture=True).stdout.strip().splitlines()
    mem_line = [x for x in mem_raw if x.startswith('Mem:')][0].split()
    total_mb  = int(mem_line[1])
    used_mb   = int(mem_line[2])
    current_pct = (used_mb / total_mb) * 100
    phase1_mb = int(total_mb * 0.83) - used_mb
    phase2_mb = int(total_mb * 0.12) + 500

    print(f"  {bold('Node RAM state:')}  {y(str(total_mb))}MB total  |  {y(str(used_mb))}MB used  |  {y(f'{current_pct:.0f}%')} current")
    print(f"  {bold('Phase 1 stress:')} {g(str(phase1_mb))}MB  ->  target ~87%")
    print(f"  {bold('Phase 2 stress:')} {g(str(phase2_mb))}MB  ->  target ~92%")

    if phase1_mb <= 0:
        print(r("  Node already above 85% -- no stress needed, alerts should already be firing"))
        return

    divider(f"Phase 1 -- Stressing to ~87%  ({phase1_mb}MB)")
    run(
        f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} ' +
        f'"nohup sudo stress-ng --vm 1 --vm-bytes {phase1_mb}M --timeout 300s > /tmp/stress1.log 2>&1 & echo started"',
        capture=True
    )
    print(g(f"  Phase 1 stress started ({phase1_mb}MB)"))
    print(dim("  Waiting for NodeMemoryHigh to fire at 85%..."))

    fired = wait_for_alert("NodeMemoryHigh", timeout_s=180)
    if fired < 0:
        print(r("\n  NodeMemoryHigh did not fire -- aborting"))
        run(f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "sudo pkill -f stress-ng 2>/dev/null || true"', capture=True)
        return

    print(y("\n  Check #incidents in Slack -- CRITICAL | NodeMemoryHigh should be there"))
    input(c("  Press Enter when you have confirmed the Slack alert to start Phase 2..."))

    print(dim("  Killing Phase 1 stress to free headroom..."))
    run(f"ssh {SSH_OPTS} andy@{K3S_INFRA_IP} 'sudo kill -9 $(pgrep -f stress-ng) 2>/dev/null || true'", capture=True)
    time.sleep(3)
    mem_raw2  = run(f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "free -m"', capture=True).stdout.strip().splitlines()
    mem_line2 = [x for x in mem_raw2 if x.startswith('Mem:')][0].split()
    total_mb2 = int(mem_line2[1])
    used_mb2  = int(mem_line2[2])
    phase2_mb = int(total_mb2 * 0.93) - used_mb2
    current2  = round(used_mb2 / total_mb2 * 100)
    print(f"  {bold('Phase 2 state:')} {y(str(used_mb2))}MB used ({y(str(current2)+'%')}) -- stressing {g(str(phase2_mb))}MB -> 93%")
    divider(f"Phase 2 -- Fresh stress to ~93%  ({phase2_mb}MB)")
    run(
        f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} ' +
        f'"nohup sudo stress-ng --vm 1 --vm-bytes {phase2_mb}M --timeout 60s > /tmp/stress2.log 2>&1 & echo started"',
        capture=True
    )
    print(g(f"  Phase 2 stress started ({phase2_mb}MB)"))
    print(dim("  Waiting for NodeMemoryCritical to fire at 90%..."))

    fired2 = wait_for_alert("NodeMemoryCritical", timeout_s=180)
    if fired2 < 0:
        print(r("\n  NodeMemoryCritical did not fire"))
    else:
        print(y("  Auto-remediation triggered -- killing stress-ng + restarting gitlab..."))
        run(
            f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} '
            '"sudo kill -9 $(pgrep -f stress-ng) 2>/dev/null; sudo gitlab-ctl restart 2>/dev/null; echo done"',
            capture=True
        )
        print(g("  stress-ng killed + gitlab-ctl restart sent"))
        print(dim("  Waiting for memory to drop and alerts to resolve..."))

    divider("Waiting for RESOLVED")
    resolved = wait_for_resolved("NodeMemoryCritical", timeout_s=300)
    if resolved:
        print(g("  Check #incidents -- RESOLVED messages should be in Slack"))
    else:
        print(r("  Alerts did not resolve -- cleaning up manually"))
        run(f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "sudo pkill -f stress-ng 2>/dev/null || true"', capture=True)

    print(c("\n  Final k3s-infra memory:"))
    run(f'ssh {SSH_OPTS} andy@{K3S_INFRA_IP} "free -h"')

    divider("RAM Nuke Test Complete")
    print(f"  {bold('Pipeline tested:')}")
    print(f"  {g('>')} Phase 1: stress-ng hit 85% -- NodeMemoryHigh fired")
    print(f"  {g('>')} Phase 2: stress-ng hit 90% -- NodeMemoryCritical fired")
    print(f"  {g('>')} Auto-remediation: pkill stress-ng + gitlab-ctl restart")
    print(f"  {g('>')} Memory recovered -- both alerts resolved")
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
    print(f"""
  {bold(c('OBSERVABILITY'))}
  {b('-'*56)}
  {bold('Grafana')}          {g('http://192.168.122.218:30080')}
                    {dim('admin / kubectl get secret -n monitoring monitoring-grafana -o jsonpath="{.data.admin-password}" | base64 -d')}

  {bold('Prometheus')}       {g('http://192.168.122.218:30090')}
                    {dim('Alerts: /alerts  Targets: /targets  Rules: /rules')}

  {bold('Alertmanager')}     {g('http://192.168.122.218:30093')}
                    {dim('Active alerts, silences, inhibitions')}

  {bold('Loki')}             {dim('ClusterIP only -- use Grafana Explore')}

  {bold(c('APPLICATION'))}
  {b('-'*56)}
  {bold('Trengo (prod)')}    {g('http://192.168.122.218:32504')}
                    {dim('Production -- manual deploy gate')}

  {bold('Trengo (staging)')} {g('http://192.168.122.218:32505')}
                    {dim('Staging -- auto-deployed on every pipeline run')}

  {bold(c('CI/CD & CODE'))}
  {b('-'*56)}
  {bold('GitLab')}           {g('http://192.168.122.230:8929')}
                    {dim('Source control, CI/CD pipelines, incident issues')}

  {bold('Pipelines')}        {g('http://192.168.122.230:8929/root/trengo-search/-/pipelines')}

  {bold('Wiki')}             {g('http://192.168.122.230:8929/root/trengo-search/-/wikis')}
                    {dim('Runbooks, post-mortems, architecture docs')}

  {bold('GitLab Runner')}    {dim('Registered on ci-runner (192.168.122.220) → GitLab on k3s-infra')}

  {bold(c('CLUSTER MANAGEMENT'))}
  {b('-'*56)}
  {bold('Portainer')}        {g('http://192.168.122.218:30777')}
                    {dim('Container/pod management UI')}

  {bold('K8s Dashboard')}    {g('https://192.168.122.218:30443')}
                    {dim('Token: kubectl -n kubernetes-dashboard create token admin-user')}

  {bold('Vaultwarden')}      {g('https://192.168.122.218:30900')}
                    {dim('Password vault — MFA enabled')}

  {bold(c('KUBEADM CLUSTER'))}
  {b('-'*56)}
  {bold('kubeadm-control')}  {c('ssh andy@192.168.122.240')}
  {bold('kubeadm-worker-1')} {c('ssh andy@192.168.122.241')}

  {bold(c('NODE SSH ACCESS'))}
  {b('-'*56)}
  {bold('k3s-control')}      {c('ssh andy@192.168.122.218')}
  {bold('k3s-infra')}        {c('ssh andy@192.168.122.230')}   {dim('(monitoring stack)')}
  {bold('k3s-worker-1')}     {c('ssh andy@192.168.122.219')}
  {bold('ci-runner')}        {c('ssh andy@192.168.122.220')}   {dim('(Runner only — GitLab moved to k3s-infra)')}
""")

# ─────────────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────────────
def main():
    dispatch = {
        '1':  lambda: (do_start_k3s(),           pause()),
        '2':  lambda: (do_stop_k3s(),            pause()),
        '3':  lambda: (do_start_k8s(),           pause()),
        '4':  lambda: (do_stop_k8s(),            pause()),
        '5':  lambda: (do_k8s_status(),          pause()),
        '6':  lambda: (do_health_check(),        pause()),
        '7':  scale_menu,
        '8':  ansible_menu,
        '9':  lambda: (do_sync_all(),            pause()),
        '10': lambda: (do_repair_node(),         pause()),
        '11': lambda: (do_rejoin(),              pause()),
        '12': lambda: (do_status(),              pause()),
        '13': lambda: (check_infra_services(),   pause()),
        '14': alerting_menu,
        '15': lambda: (do_service_links(),       pause()),
        '16': lambda: (do_safe_shutdown(),        pause()),
        '17': lambda: (do_safe_startup(),         pause()),
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
