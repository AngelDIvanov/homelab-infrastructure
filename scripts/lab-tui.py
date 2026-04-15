#!/usr/bin/env python3
"""
DevOps Home Lab — TUI Control Panel
=====================================
A terminal UI with live VM status, keyboard navigation,
and integrated controls for K3s, CRC, Ansible, and Terraform.

Requirements: pip install textual
Usage:        python3 lab-tui.py
"""

import subprocess
import os
import re
import time
import threading
from textual.app import App, ComposeResult
from textual.widgets import (
    Header, Footer, Static, Button, Label, Log, ListView, ListItem
)
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.binding import Binding
from textual import work
from textual.screen import ModalScreen
from textual.widgets import ProgressBar

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
SCRIPTS_DIR    = os.path.dirname(os.path.abspath(__file__))
TERRAFORM_DIR  = os.path.expanduser("~/homelab/terraform")
ANSIBLE_DIR    = os.path.expanduser("~/homelab/ansible")
ANSIBLE_INV    = os.path.join(ANSIBLE_DIR, "inventory/homelab.ini")

K3S_CONTROL_IP = "192.168.122.218"
CI_RUNNER_IP   = "192.168.122.220"
K3S_URL        = f"https://{K3S_CONTROL_IP}:6443"

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
    raise SystemExit(1)

K3S_TOKEN = _get_secret("K3S_TOKEN", "homelab-k3s-token")
BASE_IP_OCTET  = 221
SSH_OPTS       = "-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no"

# Static VMs — workers are discovered dynamically from virsh
STATIC_VMS = ["k3s-control", "ci-runner", "crc"]

# Services to check
SERVICES = [
    ("Grafana",      f"http://{K3S_CONTROL_IP}:30080"),
    ("GitLab",       f"http://{CI_RUNNER_IP}"),
    ("Trengo App",   f"http://{K3S_CONTROL_IP}:32504"),
    ("Portainer",    f"http://{K3S_CONTROL_IP}:30777"),
    ("K8s Dashboard",f"https://{K3S_CONTROL_IP}:30443"),
]

# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
def run_cmd(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()

def vm_states():
    _, out, _ = run_cmd("virsh list --all 2>/dev/null")
    running = set()
    all_defined = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lstrip("-").isdigit() or (len(parts) >= 2 and parts[0] == "-"):
            name = parts[1] if parts[0] == "-" else parts[1]
            all_defined.append(name)
            if len(parts) >= 3 and parts[2] == "running":
                running.add(name)
    workers = sorted(vm for vm in all_defined if vm.startswith("k3s-worker-"))
    vms = ["k3s-control"] + workers + ["ci-runner", "crc"]
    return {vm: vm in running for vm in vms}

def service_states():
    """Return dict of {name: bool} — True = reachable."""
    states = {}
    for name, url in SERVICES:
        code, _, _ = run_cmd(f"curl -sfk --max-time 3 -o /dev/null {url}")
        states[name] = (code == 0)
    return states

def k3s_nodes():
    """Return list of (name, status) from kubectl."""
    _, out, _ = run_cmd(
        f"ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "
        f"'sudo k3s kubectl get nodes --no-headers 2>/dev/null'"
    )
    nodes = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            nodes.append((parts[0], parts[1]))
    return nodes

def get_vm_count():
    tfvars = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
    try:
        content = open(tfvars).read()
        m = re.search(r'vm_count\s*=\s*(\d+)', content)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0

def get_worker_ip(worker_num):
    return f"192.168.122.{BASE_IP_OCTET + worker_num - 2}"

def run_script(name, log_widget):
    """Run a bash script and stream output to log widget."""
    path = os.path.join(SCRIPTS_DIR, name)
    if not os.path.isfile(path):
        log_widget.write_line(f"[red]FAIL Script not found: {path}[/red]")
        return
    log_widget.write_line(f"[cyan]$ bash {path}[/cyan]")
    proc = subprocess.Popen(
        f"bash {path}", shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for line in proc.stdout:
        log_widget.write_line(line.rstrip())
    proc.wait()
    rc = proc.returncode
    if rc == 0:
        log_widget.write_line("[green]OK Done[/green]")
    else:
        log_widget.write_line(f"[red]FAIL Exited with code {rc}[/red]")

# ─────────────────────────────────────────────────────────────
#  CONFIRM DIALOG
# ─────────────────────────────────────────────────────────────
class ConfirmScreen(ModalScreen):
    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Label(self.message, id="confirm-msg")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes [Y]", variant="error", id="yes")
                yield Button("No [N]", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "yes")

    def action_confirm(self): self.dismiss(True)
    def action_cancel(self):  self.dismiss(False)

# ─────────────────────────────────────────────────────────────
#  STATUS PANEL
# ─────────────────────────────────────────────────────────────
class StatusPanel(Static):
    """Live-updating VM and service status panel."""

    vm_data:      reactive = reactive({})
    service_data: reactive = reactive({})
    node_data:    reactive = reactive([])

    def render(self) -> str:
        lines = []

        # ── VMs ──
        lines.append("[bold cyan]── Virtual Machines ──────────────────────[/bold cyan]")
        if not self.vm_data:
            lines.append("  [dim]Loading...[/dim]")
        else:
            row = []
            for vm in self.vm_data:
                on = self.vm_data.get(vm, False)
                icon = "[UP]" if on else "[DOWN]"
                row.append(f"{icon} [bold]{vm}[/bold]")
                if len(row) == 2:
                    lines.append(f"  {row[0]:<40} {row[1]}")
                    row = []
            if row:
                lines.append(f"  {row[0]}")

        # ── Services ──
        lines.append("")
        lines.append("[bold cyan]── Services ──────────────────────────────[/bold cyan]")
        if not self.service_data:
            lines.append("  [dim]Loading...[/dim]")
        else:
            for name, up in self.service_data.items():
                icon = "[UP]" if up else "[DOWN]"
                lines.append(f"  {icon} {name}")

        # ── K3s Nodes ──
        lines.append("")
        lines.append("[bold cyan]── K3s Nodes ─────────────────────────────[/bold cyan]")
        if not self.node_data:
            lines.append("  [dim]Not reachable or loading...[/dim]")
        else:
            for name, status in self.node_data:
                icon = "[UP]" if status == "Ready" else "[DOWN]"
                lines.append(f"  {icon} {name:<22} {status}")

        return "\n".join(lines)

# ─────────────────────────────────────────────────────────────
#  MAIN APP
# ─────────────────────────────────────────────────────────────
class LabTUI(App):
    CSS = """
    Screen {
        background: $surface;
    }

    #main-layout {
        layout: horizontal;
        height: 1fr;
    }

    #left-panel {
        width: 26;
        background: $panel;
        border-right: tall $primary;
        padding: 0 1;
    }

    #left-panel Label {
        text-style: bold;
        color: $accent;
        margin-top: 1;
    }

    #right-panel {
        width: 1fr;
        layout: vertical;
    }

    #status-panel {
        height: auto;
        min-height: 22;
        background: $panel;
        border: tall $primary;
        padding: 1 2;
        margin: 0 0 0 0;
    }

    #log-panel {
        height: 1fr;
        border: tall $primary;
        background: $surface;
        margin-top: 0;
    }

    #log-title {
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
        height: 1;
    }

    Button {
        width: 100%;
        margin: 0 0 0 0;
        height: 2;
    }

    Button.section-btn {
        background: $boost;
        color: $text;
        border: none;
    }

    Button.section-btn:hover {
        background: $accent;
    }

    Button.danger-btn {
        background: $error;
        color: $text;
    }

    Button.danger-btn:hover {
        background: $error 80%;
    }

    #confirm-dialog {
        width: 50;
        height: 10;
        background: $panel;
        border: tall $error;
        padding: 2 4;
        align: center middle;
    }

    #confirm-msg {
        text-align: center;
        margin-bottom: 2;
    }

    #confirm-buttons {
        align: center middle;
        height: 3;
    }

    #confirm-buttons Button {
        width: 12;
        margin: 0 1;
    }

    .divider {
        color: $primary;
        height: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    TITLE = " DevOps Home Lab"
    SUB_TITLE = "Control Panel"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-layout"):
            # ── Left: navigation buttons ──
            with Vertical(id="left-panel"):
                yield Label("  K3S CLUSTER")
                yield Button("▶  Start K3s",    id="k3s-start",   classes="section-btn")
                yield Button("  Stop K3s",     id="k3s-stop",    classes="section-btn")
                yield Label("  CRC")
                yield Button("▶  Start CRC",    id="crc-start",   classes="section-btn")
                yield Button("  Stop CRC",     id="crc-stop",    classes="section-btn")
                yield Label("  LAB")
                yield Button(" Stop ALL",      id="stop-all",    classes="danger-btn")
                yield Button(" Health Check",  id="health",      classes="section-btn")
                yield Button(" Refresh Status",id="refresh-btn", classes="section-btn")
                yield Label("  SCALE")
                yield Button("Upscale Upscale",       id="upscale",     classes="section-btn")
                yield Button("Downscale Downscale",     id="downscale",   classes="section-btn")
                yield Label("  TOOLS")
                yield Button(" Rejoin Workers",id="rejoin",      classes="section-btn")
                yield Button(" Sync Images",   id="sync",        classes="section-btn")

            # ── Right: status + log ──
            with Vertical(id="right-panel"):
                yield StatusPanel(id="status-panel")
                yield Static("   Output Log", id="log-title")
                yield Log(id="log-panel", auto_scroll=True)

        yield Footer()

    def on_mount(self):
        """Start background status refresh on mount."""
        self.refresh_status()
        self.set_interval(30, self.refresh_status)

    # ── Status refresh ────────────────────────────────────────
    @work(thread=True)
    def refresh_status(self):
        panel = self.query_one("#status-panel", StatusPanel)
        log   = self.query_one("#log-panel", Log)

        # VMs
        states = vm_states()
        self.call_from_thread(setattr, panel, "vm_data", states)

        # Services (only if k3s-control is running)
        if states.get("k3s-control") or states.get("ci-runner"):
            svcs = service_states()
            self.call_from_thread(setattr, panel, "service_data", svcs)

        # K3s nodes (only if control plane is up)
        if states.get("k3s-control"):
            nodes = k3s_nodes()
            self.call_from_thread(setattr, panel, "node_data", nodes)
        else:
            self.call_from_thread(setattr, panel, "node_data", [])

    def action_refresh(self):
        log = self.query_one("#log-panel", Log)
        log.write_line("[cyan]↻ Refreshing status...[/cyan]")
        self.refresh_status()

    # ── Button handler ────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed):
        btn = event.button.id

        if btn == "refresh-btn":
            self.action_refresh()

        elif btn == "k3s-start":
            self._run_script_async("k3s-start.sh", "▶ Starting K3s cluster...")

        elif btn == "k3s-stop":
            self.push_screen(
                ConfirmScreen("Stop K3s cluster?"),
                lambda ok: self._run_script_async("k3s-stop.sh", " Stopping K3s...") if ok else None
            )

        elif btn == "crc-start":
            self._run_script_async("crc-start.sh", "▶ Starting CRC (this takes ~60s)...")

        elif btn == "crc-stop":
            self.push_screen(
                ConfirmScreen("Stop CRC?"),
                lambda ok: self._run_script_async("crc-stop.sh", " Stopping CRC...") if ok else None
            )

        elif btn == "stop-all":
            self.push_screen(
                ConfirmScreen("[WARN] Stop ALL environments?\n(K3s + CRC + CI Runner)"),
                lambda ok: self._run_script_async("lab-stop-all.sh", " Stopping all environments...") if ok else None
            )

        elif btn == "health":
            self._run_script_async("check-lab.sh", " Running health check...")

        elif btn == "upscale":
            self._run_upscale()

        elif btn == "downscale":
            self._run_downscale()

        elif btn == "rejoin":
            self.push_screen(
                ConfirmScreen("Rejoin all Terraform workers to k3s?"),
                lambda ok: self._run_rejoin() if ok else None
            )

        elif btn == "sync":
            self._run_sync()

    # ── Async script runner ───────────────────────────────────
    @work(thread=True)
    def _run_script_async(self, script_name, header):
        log = self.query_one("#log-panel", Log)
        self.call_from_thread(log.write_line, f"\n[bold cyan]{'─'*50}[/bold cyan]")
        self.call_from_thread(log.write_line, f"[bold yellow]{header}[/bold yellow]")
        self.call_from_thread(log.write_line, f"[bold cyan]{'─'*50}[/bold cyan]")
        run_script(script_name, log)
        self.call_from_thread(self.refresh_status)

    # ── Scale operations ──────────────────────────────────────
    @work(thread=True)
    def _run_upscale(self):
        log = self.query_one("#log-panel", Log)
        self.call_from_thread(log.write_line, "\n[bold yellow]Upscale Starting upscale...[/bold yellow]")

        current   = get_vm_count()
        new_count = current + 1
        new_wnum  = new_count + 1
        new_name  = f"k3s-worker-{new_wnum}"
        new_ip    = get_worker_ip(new_wnum)

        self.call_from_thread(log.write_line, f"  Adding: [bold]{new_name}[/bold] ({new_ip})")

        # Terraform
        self.call_from_thread(log.write_line, "[cyan]→ Running terraform apply...[/cyan]")
        tfvars = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
        try:
            content = open(tfvars).read()
            content = re.sub(r'vm_count\s*=\s*\d+', f'vm_count       = {new_count}', content)
            open(tfvars, 'w').write(content)
        except Exception as e:
            self.call_from_thread(log.write_line, f"[red]FAIL Could not update tfvars: {e}[/red]")
            return

        rc, out, err = run_cmd(f"cd {TERRAFORM_DIR} && terraform apply -auto-approve")
        for line in out.splitlines(): self.call_from_thread(log.write_line, line)
        if rc != 0:
            self.call_from_thread(log.write_line, "[red]FAIL Terraform failed[/red]")
            return

        # Boot delay
        self.call_from_thread(log.write_line, "[cyan]→ Waiting 60s for VM to boot...[/cyan]")
        for i in range(6):
            time.sleep(10)
            self.call_from_thread(log.write_line, f"  [{(i+1)*10}/60s]")

        # SSH wait
        self.call_from_thread(log.write_line, "[cyan]→ Waiting for SSH...[/cyan]")
        ready = False
        for _ in range(12):
            rc, _, _ = run_cmd(f"ssh {SSH_OPTS} andy@{new_ip} 'echo ok' 2>/dev/null")
            if rc == 0:
                ready = True; break
            time.sleep(10)
        if not ready:
            self.call_from_thread(log.write_line, "[red]FAIL SSH timeout[/red]"); return

        # Join k3s
        self.call_from_thread(log.write_line, "[cyan]→ Joining k3s cluster...[/cyan]")
        rc, _, _ = run_cmd(
            f'ssh {SSH_OPTS} andy@{new_ip} '
            f'"curl -sfL https://get.k3s.io | K3S_URL={K3S_URL} K3S_TOKEN={K3S_TOKEN} sh -"'
        )
        run_cmd(f'ssh {SSH_OPTS} andy@{new_ip} "sudo systemctl restart k3s-agent"')

        self.call_from_thread(log.write_line, f"[green]OK {new_name} added![/green]")
        self.call_from_thread(self.refresh_status)

    @work(thread=True)
    def _run_downscale(self):
        log = self.query_one("#log-panel", Log)
        current = get_vm_count()
        if current <= 0:
            self.call_from_thread(log.write_line, "[red]No Terraform workers to remove[/red]")
            return

        wnum  = current + 1
        wname = f"k3s-worker-{wnum}"
        self.call_from_thread(log.write_line, f"\n[bold yellow]Downscale Removing {wname}...[/bold yellow]")

        # Drain
        run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl drain {wname} --ignore-daemonsets --delete-emptydir-data --force 2>/dev/null || true"')
        run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s kubectl delete node {wname} 2>/dev/null || true"')

        # Terraform
        tfvars = os.path.join(TERRAFORM_DIR, "terraform.tfvars")
        try:
            content = open(tfvars).read()
            content = re.sub(r'vm_count\s*=\s*\d+', f'vm_count       = {current - 1}', content)
            open(tfvars, 'w').write(content)
        except Exception as e:
            self.call_from_thread(log.write_line, f"[red]FAIL {e}[/red]"); return

        run_cmd(f"cd {TERRAFORM_DIR} && terraform apply -auto-approve")
        self.call_from_thread(log.write_line, f"[green]OK {wname} removed![/green]")
        self.call_from_thread(self.refresh_status)

    @work(thread=True)
    def _run_rejoin(self):
        log = self.query_one("#log-panel", Log)
        self.call_from_thread(log.write_line, "\n[bold yellow] Rejoining workers...[/bold yellow]")
        vm_count = get_vm_count()
        for i in range(vm_count):
            wnum = i + 2
            wname = f"k3s-worker-{wnum}"
            wip   = get_worker_ip(wnum)
            self.call_from_thread(log.write_line, f"  Processing [bold]{wname}[/bold] ({wip})...")
            run_cmd(f'ssh {SSH_OPTS} andy@{wip} "sudo rm -f /etc/rancher/node/password"')
            run_cmd(f'ssh {SSH_OPTS} andy@{wip} "sudo systemctl stop k3s-agent 2>/dev/null || true"')
            rc, _, _ = run_cmd(
                f'ssh {SSH_OPTS} andy@{wip} '
                f'"curl -sfL https://get.k3s.io | K3S_URL={K3S_URL} K3S_TOKEN={K3S_TOKEN} sh -"'
            )
            run_cmd(f'ssh {SSH_OPTS} andy@{wip} "sudo systemctl restart k3s-agent"')
            icon = "OK" if rc == 0 else "FAIL"
            color = "green" if rc == 0 else "red"
            self.call_from_thread(log.write_line, f"  [{color}]{icon} {wname}[/{color}]")
        self.call_from_thread(log.write_line, "[green]OK Rejoin complete![/green]")
        self.call_from_thread(self.refresh_status)

    @work(thread=True)
    def _run_sync(self):
        log = self.query_one("#log-panel", Log)
        self.call_from_thread(log.write_line, "\n[bold yellow] Syncing images to all workers...[/bold yellow]")
        workers = {"k3s-worker-1": "192.168.122.219"}
        for i in range(get_vm_count()):
            wnum = i + 2
            workers[f"k3s-worker-{wnum}"] = get_worker_ip(wnum)
        for name, ip in sorted(workers.items()):
            self.call_from_thread(log.write_line, f"  Syncing to [bold]{name}[/bold]...")
            _, img, _ = run_cmd(
                f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} '
                f'"sudo k3s ctr images list | grep trengo-search | head -1 | awk \'{{print $1}}\'"'
            )
            if img:
                run_cmd(f'ssh {SSH_OPTS} andy@{K3S_CONTROL_IP} "sudo k3s ctr images export /tmp/sync.tar {img}"')
                run_cmd(f'scp {SSH_OPTS} andy@{K3S_CONTROL_IP}:/tmp/sync.tar /tmp/')
                run_cmd(f'scp {SSH_OPTS} /tmp/sync.tar andy@{ip}:/tmp/')
                run_cmd(f'ssh {SSH_OPTS} andy@{ip} "sudo k3s ctr images import /tmp/sync.tar"')
                self.call_from_thread(log.write_line, f"  [green]OK {name} synced[/green]")
            else:
                self.call_from_thread(log.write_line, f"  [yellow][WARN] No image found for {name}[/yellow]")
        self.call_from_thread(log.write_line, "[green]OK Sync complete![/green]")

if __name__ == "__main__":
    app = LabTUI()
    app.run()
