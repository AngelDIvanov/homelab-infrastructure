#!/usr/bin/env python3
"""
DevOps Home Lab Manager
=======================
Orchestrates Terraform, Ansible, and k3s operations for easy scaling.

Author: Angel
Usage: python3 lab-manager.py
"""

import subprocess
import sys
import os
import re
import time

# Configuration
TERRAFORM_DIR = os.path.expanduser("~/homelab/terraform")
ANSIBLE_DIR = os.path.expanduser("~/homelab/ansible")
ANSIBLE_INVENTORY = os.path.join(ANSIBLE_DIR, "inventory/homelab.ini")

K3S_CONTROL_IP = "192.168.122.218"
K3S_URL = f"https://{K3S_CONTROL_IP}:6443"

def _get_secret(env_var, vault_item):
    """Read secret from env, falling back to Bitwarden CLI."""
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

SSH_OPTS = "-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no"

# Static IP scheme:
# 192.168.122.218 - k3s-control
# 192.168.122.219 - k3s-worker-1 (manual)
# 192.168.122.220 - ci-runner
# 192.168.122.221 - k3s-worker-2 (terraform, base_ip_octet=221)
# 192.168.122.222 - k3s-worker-3
# 192.168.122.223 - k3s-worker-4
# ...
BASE_IP_OCTET = 221  # First terraform worker starts here

def get_worker_static_ip(worker_num):
    """Calculate static IP for a worker based on its number."""
    # worker-2 = .221, worker-3 = .222, etc.
    return f"192.168.122.{BASE_IP_OCTET + worker_num - 2}"

# Colors
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header():
    print(f"""
{Colors.BLUE}╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║          DevOps Home Lab Manager                      ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝{Colors.END}
""")

def print_menu():
    print(f"""
{Colors.CYAN}┌─────────────────────────────────────────┐
│           MAIN MENU                     │
├─────────────────────────────────────────┤{Colors.END}
│  1. Upscale Upscale   - Add new worker VM    │
│  2. Downscale Downscale - Remove worker VM     │
│  3.  Ansible   - Run playbooks        │
│  4.  Status    - Show cluster status  │
│  5.  Sync      - Sync images to nodes │
│  6.  Rejoin    - Rejoin existing VMs  │
│  0.  Exit                             │
{Colors.CYAN}└─────────────────────────────────────────┘{Colors.END}
""")

def run_cmd(cmd, capture=False, check=True):
    """Run a shell command."""
    print(f"{Colors.BOLD}$ {cmd}{Colors.END}")
    if capture:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if check and result.returncode != 0:
            print(f"{Colors.RED}Error: {result.stderr}{Colors.END}")
        return result
    else:
        return subprocess.run(cmd, shell=True)

def get_current_vm_count():
    """Read current vm_count from terraform.tfvars."""
    tfvars_path = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
    with open(tfvars_path, 'r') as f:
        content = f.read()
    match = re.search(r'vm_count\s*=\s*(\d+)', content)
    return int(match.group(1)) if match else 0

def set_vm_count(count):
    """Update vm_count in terraform.tfvars."""
    tfvars_path = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
    with open(tfvars_path, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    for line in lines:
        if line.strip().startswith('vm_count'):
            new_lines.append(f'vm_count       = {count}\n')
        else:
            new_lines.append(line)
    
    with open(tfvars_path, 'w') as f:
        f.writelines(new_lines)

def get_terraform_outputs():
    """Get worker IPs from Terraform output."""
    os.chdir(TERRAFORM_DIR)
    result = run_cmd("terraform output -json worker_ips 2>/dev/null", capture=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        import json
        try:
            return json.loads(result.stdout)
        except:
            return {}
    return {}

def get_worker_ip(worker_num):
    """Get IP for a specific worker - uses static IP scheme."""
    return get_worker_static_ip(worker_num)

def wait_for_ssh(ip, timeout=120):
    """Wait for SSH to become available."""
    print(f"{Colors.YELLOW} Waiting for SSH on {ip}...{Colors.END}")
    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run(
            f"ssh {SSH_OPTS} andy@{ip} 'echo ok' 2>/dev/null",
            shell=True, capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}OK SSH ready on {ip}{Colors.END}")
            return True
        time.sleep(5)
    print(f"{Colors.RED}FAIL SSH timeout for {ip}{Colors.END}")
    return False

def join_k3s_cluster(ip, worker_name):
    """Join a worker to the k3s cluster."""
    print(f"{Colors.YELLOW} Joining {worker_name} to k3s cluster...{Colors.END}")
    
    # Install k3s agent
    cmd = f'ssh {SSH_OPTS} andy@{ip} "curl -sfL https://get.k3s.io | K3S_URL={K3S_URL} K3S_TOKEN={K3S_TOKEN} sh -"'
    result = run_cmd(cmd)
    
    if result.returncode != 0:
        print(f"{Colors.RED}FAIL Failed to install k3s on {worker_name}{Colors.END}")
        return False
    
    # Always start/restart the agent (in case "No change detected")
    print(f"{Colors.YELLOW} Starting k3s-agent service...{Colors.END}")
    run_cmd(f'ssh {SSH_OPTS} andy@{ip} "sudo systemctl restart k3s-agent"', capture=True)
    time.sleep(5)
    
    # Verify agent is running
    result = run_cmd(f'ssh {SSH_OPTS} andy@{ip} "systemctl is-active k3s-agent"', capture=True)
    if result.stdout.strip() == "active":
        print(f"{Colors.GREEN}OK {worker_name} joined cluster{Colors.END}")
        return True
    else:
        print(f"{Colors.RED}FAIL k3s-agent failed to start on {worker_name}{Colors.END}")
        return False

def drain_and_remove_node(worker_name):
    """Drain and remove a node from k3s cluster."""
    print(f"{Colors.YELLOW} Draining {worker_name} from cluster...{Colors.END}")
    
    # Drain node
    run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl drain {worker_name} --ignore-daemonsets --delete-emptydir-data --force 2>/dev/null || true"')
    
    # Delete node
    run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl delete node {worker_name} 2>/dev/null || true"')
    
    print(f"{Colors.GREEN}OK {worker_name} removed from cluster{Colors.END}")

def update_ansible_inventory():
    """Update Ansible inventory with current workers using static IPs."""
    vm_count = get_current_vm_count()
    
    inventory_content = """[all:vars]
ansible_user=andy
ansible_ssh_private_key_file=~/.ssh/id_rsa

[control_plane]
k3s-control ansible_host=192.168.122.218

[workers]
k3s-worker-1 ansible_host=192.168.122.219
"""
    
    # Add terraform-managed workers with static IPs
    for i in range(vm_count):
        worker_num = i + 2  # worker-2, worker-3, etc.
        worker_ip = get_worker_static_ip(worker_num)
        inventory_content += f"k3s-worker-{worker_num} ansible_host={worker_ip}\n"
    
    inventory_content += """
[ci_cd]
ci-runner ansible_host=192.168.122.220

[k8s_cluster:children]
control_plane
workers
"""
    
    with open(ANSIBLE_INVENTORY, 'w') as f:
        f.write(inventory_content)
    
    print(f"{Colors.GREEN}OK Ansible inventory updated{Colors.END}")

def sync_images_to_node(ip, worker_name):
    """Sync container images to a worker node."""
    print(f"{Colors.YELLOW} Syncing images to {worker_name}...{Colors.END}")
    
    # Get latest trengo image from control
    result = run_cmd(
        f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s ctr images list | grep trengo-search | head -1 | awk \'{{print \\$1}}\'"',
        capture=True
    )
    
    if not result.stdout.strip():
        print(f"{Colors.YELLOW}[WARN] No trengo-search image found to sync{Colors.END}")
        return
    
    image = result.stdout.strip()
    print(f"  Syncing image: {image}")
    
    # Export, copy, import
    run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s ctr images export /tmp/sync.tar {image}"')
    run_cmd(f'scp {SSH_OPTS} andy@{K3S_CONTROL_IP}:/tmp/sync.tar /tmp/')
    run_cmd(f'scp {SSH_OPTS} /tmp/sync.tar andy@{ip}:/tmp/')
    run_cmd(f'ssh {SSH_OPTS} andy@{ip} "sudo k3s ctr images import /tmp/sync.tar"')
    
    print(f"{Colors.GREEN}OK Images synced to {worker_name}{Colors.END}")

def upscale():
    """Add a new worker VM."""
    print(f"\n{Colors.BOLD}Upscale UPSCALE - Adding new worker{Colors.END}\n")
    
    current = get_current_vm_count()
    new_count = current + 1
    new_worker_num = new_count + 1  # +1 because worker-1 is manual
    new_worker_name = f"k3s-worker-{new_worker_num}"
    new_worker_ip = get_worker_static_ip(new_worker_num)
    
    print(f"Current workers (Terraform): {current}")
    print(f"New worker count: {new_count}")
    print(f"New worker name: {new_worker_name}")
    print(f"New worker IP: {new_worker_ip}")
    
    confirm = input(f"\n{Colors.YELLOW}Proceed? (y/n): {Colors.END}")
    if confirm.lower() != 'y':
        print("Cancelled.")
        return
    
    # Step 1: Update Terraform
    print(f"\n{Colors.CYAN}Step 1/6: Terraform Apply{Colors.END}")
    set_vm_count(new_count)
    os.chdir(TERRAFORM_DIR)
    result = run_cmd("terraform apply -auto-approve")
    if result.returncode != 0:
        print(f"{Colors.RED}Terraform failed!{Colors.END}")
        return
    
    # Step 2: Wait for VM to boot
    print(f"\n{Colors.CYAN}Step 2/6: Waiting for VM to boot (60s){Colors.END}")
    for i in range(6):
        print(f"  [{i*10}/60s]", end='\r')
        time.sleep(10)
    print(f"  {Colors.GREEN}OK Boot delay complete{Colors.END}")
    
    # Step 3: Wait for SSH
    print(f"\n{Colors.CYAN}Step 3/6: Waiting for SSH{Colors.END}")
    if not wait_for_ssh(new_worker_ip):
        print(f"{Colors.RED}SSH not available. Check VM manually.{Colors.END}")
        return
    
    # Step 4: Join k3s
    print(f"\n{Colors.CYAN}Step 4/6: Joining k3s cluster{Colors.END}")
    if not join_k3s_cluster(new_worker_ip, new_worker_name):
        print(f"{Colors.RED}Failed to join k3s. Check logs.{Colors.END}")
        return
    
    # Step 5: Wait for node to be ready
    print(f"\n{Colors.CYAN}Step 5/6: Waiting for node to be Ready{Colors.END}")
    for i in range(12):  # Wait up to 2 minutes
        result = run_cmd(
            f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get node {new_worker_name} --no-headers 2>/dev/null | grep -q Ready"',
            capture=True
        )
        if result.returncode == 0:
            print(f"  {Colors.GREEN}OK {new_worker_name} is Ready!{Colors.END}")
            break
        print(f"  Waiting... [{i*10}/120s]")
        time.sleep(10)
    
    # Step 6: Update inventory and sync images
    print(f"\n{Colors.CYAN}Step 6/6: Post-setup tasks{Colors.END}")
    update_ansible_inventory()
    sync_images_to_node(new_worker_ip, new_worker_name)
    
    print(f"\n{Colors.GREEN}{'='*50}")
    print(f"OK UPSCALE COMPLETE!")
    print(f"  Worker: {new_worker_name}")
    print(f"  IP: {new_worker_ip}")
    print(f"{'='*50}{Colors.END}")

def downscale():
    """Remove a worker VM."""
    print(f"\n{Colors.BOLD}Downscale DOWNSCALE - Removing worker{Colors.END}\n")
    
    current = get_current_vm_count()
    if current <= 0:
        print(f"{Colors.RED}No Terraform-managed workers to remove{Colors.END}")
        return
    
    worker_num = current + 1  # +1 because worker-1 is manual
    worker_name = f"k3s-worker-{worker_num}"
    worker_ip = get_worker_static_ip(worker_num)
    
    print(f"Current workers (Terraform): {current}")
    print(f"Worker to remove: {worker_name}")
    print(f"Worker IP: {worker_ip}")
    
    confirm = input(f"\n{Colors.YELLOW}Proceed? (y/n): {Colors.END}")
    if confirm.lower() != 'y':
        print("Cancelled.")
        return
    
    # Step 1: Drain and remove from k3s
    print(f"\n{Colors.CYAN}Step 1/3: Removing from k3s cluster{Colors.END}")
    drain_and_remove_node(worker_name)
    
    # Step 2: Terraform destroy
    print(f"\n{Colors.CYAN}Step 2/3: Terraform Apply (scale down){Colors.END}")
    set_vm_count(current - 1)
    os.chdir(TERRAFORM_DIR)
    result = run_cmd("terraform apply -auto-approve")
    
    # Step 3: Update inventory
    print(f"\n{Colors.CYAN}Step 3/3: Updating Ansible inventory{Colors.END}")
    update_ansible_inventory()
    
    print(f"\n{Colors.GREEN}{'='*50}")
    print(f"OK DOWNSCALE COMPLETE!")
    print(f"  Removed: {worker_name} ({worker_ip})")
    print(f"{'='*50}{Colors.END}")

def ansible_menu():
    """Ansible playbook submenu."""
    print(f"""
{Colors.CYAN}┌─────────────────────────────────────────┐
│         ANSIBLE PLAYBOOKS               │
├─────────────────────────────────────────┤{Colors.END}
│  1.  Install btop                     │
│  2.  Join k3s cluster                 │
│  3.  List available playbooks         │
│  4. ▶️  Run custom playbook              │
│  0. ↩️  Back to main menu               │
{Colors.CYAN}└─────────────────────────────────────────┘{Colors.END}
""")
    
    choice = input(f"{Colors.YELLOW}Select option: {Colors.END}")
    
    os.chdir(ANSIBLE_DIR)
    
    if choice == '1':
        run_cmd(f"ansible-playbook -i inventory/homelab.ini playbooks/install-btop.yml")
    elif choice == '2':
        run_cmd(f"ansible-playbook -i inventory/homelab.ini playbooks/join-k3s-cluster.yml")
    elif choice == '3':
        print(f"\n{Colors.CYAN}Available playbooks:{Colors.END}")
        run_cmd("ls -la playbooks/")
    elif choice == '4':
        playbook = input("Enter playbook name (e.g., install-btop.yml): ")
        run_cmd(f"ansible-playbook -i inventory/homelab.ini playbooks/{playbook}")
    elif choice == '0':
        return
    else:
        print(f"{Colors.RED}Invalid option{Colors.END}")

def show_status():
    """Show cluster status."""
    print(f"\n{Colors.BOLD} CLUSTER STATUS{Colors.END}\n")
    
    print(f"{Colors.CYAN}── K3s Nodes ──{Colors.END}")
    run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    
    print(f"\n{Colors.CYAN}── Terraform Workers (Static IPs) ──{Colors.END}")
    vm_count = get_current_vm_count()
    print(f"VM Count: {vm_count}")
    for i in range(vm_count):
        worker_num = i + 2
        worker_ip = get_worker_static_ip(worker_num)
        print(f"  k3s-worker-{worker_num}: {worker_ip}")
    
    print(f"\n{Colors.CYAN}── VMs (virsh) ──{Colors.END}")
    run_cmd("virsh list --all")

def sync_all_images():
    """Sync images to all worker nodes."""
    print(f"\n{Colors.BOLD} SYNC IMAGES TO ALL WORKERS{Colors.END}\n")
    
    # Manual worker-1
    all_workers = {"k3s-worker-1": "192.168.122.219"}
    
    # Add terraform-managed workers with static IPs
    vm_count = get_current_vm_count()
    for i in range(vm_count):
        worker_num = i + 2
        worker_ip = get_worker_static_ip(worker_num)
        all_workers[f"k3s-worker-{worker_num}"] = worker_ip
    
    for worker_name, ip in sorted(all_workers.items()):
        sync_images_to_node(ip, worker_name)
    
    print(f"\n{Colors.GREEN}OK All workers synced!{Colors.END}")

def rejoin_workers():
    """Rejoin existing Terraform VMs to k3s cluster."""
    print(f"\n{Colors.BOLD} REJOIN - Rejoin Terraform VMs to k3s{Colors.END}\n")
    
    vm_count = get_current_vm_count()
    if vm_count <= 0:
        print(f"{Colors.RED}No Terraform-managed workers found{Colors.END}")
        return
    
    print(f"Workers to rejoin:")
    workers = []
    for i in range(vm_count):
        worker_num = i + 2
        worker_name = f"k3s-worker-{worker_num}"
        worker_ip = get_worker_static_ip(worker_num)
        workers.append((worker_num, worker_name, worker_ip))
        print(f"  {worker_name}: {worker_ip}")
    
    confirm = input(f"\n{Colors.YELLOW}Rejoin all workers? (y/n): {Colors.END}")
    if confirm.lower() != 'y':
        print("Cancelled.")
        return
    
    # First, clean up old node entries from k3s
    print(f"\n{Colors.CYAN}Cleaning up old node entries...{Colors.END}")
    for worker_num, worker_name, worker_ip in workers:
        run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl delete node {worker_name} 2>/dev/null || true"', capture=True)
    
    # Restart k3s server to clear cached credentials
    print(f"\n{Colors.CYAN}Restarting k3s server...{Colors.END}")
    run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo systemctl restart k3s"')
    print("Waiting 30s for k3s to restart...")
    time.sleep(30)
    
    # Join each worker
    for worker_num, worker_name, worker_ip in workers:
        print(f"\n{Colors.CYAN}Processing {worker_name} ({worker_ip})...{Colors.END}")
        
        # Check SSH
        if not wait_for_ssh(worker_ip, timeout=60):
            print(f"{Colors.RED}  FAIL SSH not available for {worker_name}{Colors.END}")
            continue
        
        # Clean up old k3s agent state
        print(f"  Cleaning old k3s state...")
        run_cmd(f'ssh {SSH_OPTS} andy@{worker_ip} "sudo rm -f /etc/rancher/node/password"', capture=True)
        run_cmd(f'ssh {SSH_OPTS} andy@{worker_ip} "sudo systemctl stop k3s-agent 2>/dev/null || true"', capture=True)
        
        # Join k3s
        if join_k3s_cluster(worker_ip, worker_name):
            print(f"  {Colors.GREEN}OK {worker_name} joined{Colors.END}")
        else:
            print(f"  {Colors.RED}FAIL Failed to join {worker_name}{Colors.END}")
    
    # Wait and check status
    print(f"\n{Colors.CYAN}Waiting for nodes to be Ready...{Colors.END}")
    time.sleep(30)
    
    print(f"\n{Colors.CYAN}Final cluster status:{Colors.END}")
    run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl get nodes -o wide"')
    
    # Update Ansible inventory
    update_ansible_inventory()
    
    print(f"\n{Colors.GREEN}OK Rejoin complete!{Colors.END}")

def main():
    print_header()
    
    while True:
        print_menu()
        choice = input(f"{Colors.YELLOW}Select option: {Colors.END}")
        
        if choice == '1':
            upscale()
        elif choice == '2':
            downscale()
        elif choice == '3':
            ansible_menu()
        elif choice == '4':
            show_status()
        elif choice == '5':
            sync_all_images()
        elif choice == '6':
            rejoin_workers()
        elif choice == '0':
            print(f"\n{Colors.GREEN}Goodbye! {Colors.END}\n")
            sys.exit(0)
        else:
            print(f"{Colors.RED}Invalid option{Colors.END}")
        
        input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")

if __name__ == "__main__":
    main()
