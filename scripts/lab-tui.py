#!/usr/bin/env python3
"""
DevOps Home Lab — TUI Control Panel
=====================================
A terminal UI with live VM status, keyboard navigation,
and integrated controls for K3s, Ansible, and Terraform.

Requirements: pip install textual
Usage:        python3 lab-tui.py
"""

import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Label, Log, Static

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPTS_DIR.parent
TERRAFORM_DIR = REPO_DIR / "terraform"

K3S_CONTROL_IP = "192.168.122.218"
CI_RUNNER_IP = "192.168.122.220"
K3S_INFRA_IP = "192.168.122.230"
K3S_URL = f"https://{K3S_CONTROL_IP}:6443"

def _get_secret(env_var, vault_item):
    """Read a secret only when an operation needs it."""
    value = os.environ.get(env_var, "")
    if value:
        return value
    try:
        result = subprocess.run(
            ["bw", "get", "password", vault_item],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        result = None
    if result and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    raise RuntimeError(
        f"{env_var} is unavailable; run: source {SCRIPTS_DIR}/load-secrets.sh"
    )

BASE_IP_OCTET = 221
SSH_ARGS = [
    "-o", "ConnectTimeout=5",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
]
SSH_OPTS = " ".join(shlex.quote(arg) for arg in SSH_ARGS)

K3S_INSTALL_TIMEOUT = 600

# Services to check
SERVICES = [
    ("Grafana",      f"http://{K3S_CONTROL_IP}:30080"),
    ("GitLab",       f"http://{K3S_INFRA_IP}:8929"),
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
    _, defined_output, _ = run_cmd("virsh list --all --name 2>/dev/null")
    _, running_output, _ = run_cmd("virsh list --state-running --name 2>/dev/null")
    defined = {name for name in defined_output.splitlines() if name}
    running = {name for name in running_output.splitlines() if name}
    workers = sorted(
        (vm for vm in defined if re.fullmatch(r"k3s-worker-\d+", vm)),
        key=lambda vm: int(vm.rsplit("-", 1)[1]),
    )
    vms = ["k3s-control", *workers, "k3s-infra", "ci-runner"]
    return {vm: vm in running for vm in vms if vm in defined}

def service_states():
    """Return whether each configured service is reachable."""
    states = {}
    for name, url in SERVICES:
        try:
            result = subprocess.run(
                ["curl", "-sfk", "--max-time", "3", "-o", "/dev/null", url],
                capture_output=True,
            )
            states[name] = result.returncode == 0
        except FileNotFoundError:
            states[name] = False
    return states

def k3s_nodes():
    """Return list of (name, status) from kubectl."""
    _, out, _ = run_cmd(
        f"ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} "
        f"'sudo k3s kubectl get nodes --no-headers 2>/dev/null'"
    )
    nodes = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            nodes.append((parts[0], parts[1]))
    return nodes

def get_vm_count():
    tfvars = TERRAFORM_DIR / "terraform.tfvars"
    try:
        content = tfvars.read_text()
    except OSError:
        return 0
    match = re.search(r"vm_count\s*=\s*(\d+)", content)
    return int(match.group(1)) if match else 0

def get_worker_ip(worker_num):
    return f"192.168.122.{BASE_IP_OCTET + worker_num - 2}"

def run_script(name, emit, *args):
    """Run a repository script without invoking an extra shell parser."""
    path = SCRIPTS_DIR / name
    if not path.is_file():
        emit(f"[red]FAIL Script not found: {path}[/red]")
        return
    command = ["bash", str(path), *args]
    emit(f"[cyan]$ {' '.join(command)}[/cyan]")
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.stdout:
        for line in proc.stdout:
            emit(line.rstrip())
    rc = proc.wait()
    if rc == 0:
        emit("[green]OK Done[/green]")
    else:
        emit(f"[red]FAIL Exited with code {rc}[/red]")


def install_k3s_agent(ip):
    """Install an agent without placing its join token in process arguments."""
    try:
        token = _get_secret("K3S_TOKEN", "homelab-k3s-token")
    except RuntimeError as exc:
        return 1, "", str(exc)

    remote_script = "\n".join([
        "set -euo pipefail",
        (
            "curl -sfL https://get.k3s.io | "
            f"K3S_URL={shlex.quote(K3S_URL)} "
            f"K3S_TOKEN={shlex.quote(token)} sh -s - agent"
        ),
        "tmp=$(mktemp)",
        "chmod 0600 \"$tmp\"",
        (
            "printf '%s\\n' "
            f"{shlex.quote('K3S_TOKEN=' + token)} "
            f"{shlex.quote('K3S_URL=' + K3S_URL)} > \"$tmp\""
        ),
        "sudo install -m 0600 -o root -g root \"$tmp\" /etc/systemd/system/k3s-agent.service.env",
        "rm -f \"$tmp\"",
        "sudo systemctl daemon-reload",
        "sudo systemctl restart k3s-agent",
    ])
    try:
        result = subprocess.run(
            ["ssh", *SSH_ARGS, f"labadmin@{ip}", "bash -s"],
            input=remote_script,
            capture_output=True,
            text=True,
            timeout=K3S_INSTALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 1, "", f"k3s install on {ip} timed out after {K3S_INSTALL_TIMEOUT}s"
    return result.returncode, result.stdout.strip(), result.stderr.strip()

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
                yield Label("  CI RUNNER")
                yield Button(" Start CI Runner", id="ci-start", classes="section-btn")
                yield Button(" Stop CI Runner", id="ci-stop", classes="section-btn")
                yield Label("  LAB")
                yield Button(" Stop ALL",      id="stop-all",    classes="danger-btn")
                yield Button(" Health Check",  id="health",      classes="section-btn")
                yield Button(" Refresh Status",id="refresh-btn", classes="section-btn")
                yield Label("  SCALE")
                yield Button(" Upscale",       id="upscale",     classes="section-btn")
                yield Button(" Downscale",     id="downscale",   classes="section-btn")
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
        if any(states.get(vm) for vm in ("k3s-control", "k3s-infra", "ci-runner")):
            svcs = service_states()
            self.call_from_thread(setattr, panel, "service_data", svcs)
        else:
            self.call_from_thread(setattr, panel, "service_data", {})

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

        elif btn == "ci-start":
            self._run_script_async(
                "ci-runner.sh", " Starting CI runner...", "start"
            )

        elif btn == "ci-stop":
            self.push_screen(
                ConfirmScreen("Stop the CI runner?"),
                lambda ok: self._run_script_async(
                    "ci-runner.sh", " Stopping CI runner...", "stop"
                ) if ok else None,
            )

        elif btn == "stop-all":
            self.push_screen(
                ConfirmScreen("[WARN] Stop k3s and CI runner?\n(k3s-infra remains running)"),
                lambda ok: self._run_script_async("lab-stop-all.sh", " Stopping all environments...") if ok else None
            )

        elif btn == "health":
            self._run_script_async("check-lab.sh", " Running health check...")

        elif btn == "upscale":
            self._run_upscale()

        elif btn == "downscale":
            self.push_screen(
                ConfirmScreen("Drain and remove the highest-numbered Terraform worker?"),
                lambda ok: self._run_downscale() if ok else None,
            )

        elif btn == "rejoin":
            self.push_screen(
                ConfirmScreen("Rejoin all Terraform workers to k3s?"),
                lambda ok: self._run_rejoin() if ok else None
            )

        elif btn == "sync":
            self._run_sync()

    # ── Async script runner ───────────────────────────────────
    @work(thread=True)
    def _run_script_async(self, script_name, header, *args):
        log = self.call_from_thread(self.query_one, "#log-panel", Log)
        self.call_from_thread(log.write_line, f"\n[bold cyan]{'─'*50}[/bold cyan]")
        self.call_from_thread(log.write_line, f"[bold yellow]{header}[/bold yellow]")
        self.call_from_thread(log.write_line, f"[bold cyan]{'─'*50}[/bold cyan]")
        run_script(script_name, lambda line: self.call_from_thread(log.write_line, line), *args)
        self.call_from_thread(self.refresh_status)

    # ── Scale operations ──────────────────────────────────────
    @work(thread=True)
    def _run_upscale(self):
        log = self.call_from_thread(self.query_one, "#log-panel", Log)
        self.call_from_thread(log.write_line, "\n[bold yellow]Upscale Starting upscale...[/bold yellow]")

        current   = get_vm_count()
        new_count = current + 1
        new_wnum  = new_count + 1
        new_name  = f"k3s-worker-{new_wnum}"
        new_ip    = get_worker_ip(new_wnum)

        self.call_from_thread(log.write_line, f"  Adding: [bold]{new_name}[/bold] ({new_ip})")

        # Terraform
        self.call_from_thread(log.write_line, "[cyan]→ Running terraform apply...[/cyan]")
        tfvars = TERRAFORM_DIR / "terraform.tfvars"
        try:
            original_content = tfvars.read_text()
            updated_content = re.sub(
                r'vm_count\s*=\s*\d+', f'vm_count       = {new_count}', original_content
            )
            if updated_content == original_content:
                self.call_from_thread(
                    log.write_line,
                    "[red]FAIL tfvars vm_count was not updated, aborting before terraform apply[/red]",
                )
                return
            tfvars.write_text(updated_content)
        except OSError as e:
            self.call_from_thread(log.write_line, f"[red]FAIL Could not update tfvars: {e}[/red]")
            return

        rc, out, err = run_cmd(
            f"terraform -chdir={shlex.quote(str(TERRAFORM_DIR))} apply -auto-approve"
        )
        for line in out.splitlines(): self.call_from_thread(log.write_line, line)
        if rc != 0:
            tfvars.write_text(original_content)
            self.call_from_thread(
                log.write_line,
                f"[red]FAIL Terraform failed; restored terraform.tfvars: {err}[/red]",
            )
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
            rc, _, _ = run_cmd(f"ssh {SSH_OPTS} labadmin@{new_ip} 'echo ok' 2>/dev/null")
            if rc == 0:
                ready = True; break
            time.sleep(10)
        if not ready:
            self.call_from_thread(log.write_line, "[red]FAIL SSH timeout[/red]"); return

        # Join k3s
        self.call_from_thread(log.write_line, "[cyan]→ Joining k3s cluster...[/cyan]")
        rc, _, err = install_k3s_agent(new_ip)
        if rc != 0:
            self.call_from_thread(
                log.write_line,
                f"[red]FAIL Could not join {new_name}: {err or 'unknown error'}[/red]",
            )
            self.call_from_thread(
                log.write_line,
                f"[red] The VM {new_name} exists outside the cluster and tfvars vm_count is already committed;"
                " the operator can complete the join later via Rejoin Workers.[/red]",
            )
            return

        self.call_from_thread(log.write_line, f"[green]OK {new_name} added![/green]")
        self.call_from_thread(self.refresh_status)

    @work(thread=True)
    def _run_downscale(self):
        log = self.call_from_thread(self.query_one, "#log-panel", Log)
        current = get_vm_count()
        if current <= 0:
            self.call_from_thread(log.write_line, "[red]No Terraform workers to remove[/red]")
            return

        wnum  = current + 1
        wname = f"k3s-worker-{wnum}"
        self.call_from_thread(log.write_line, f"\n[bold yellow]Downscale Removing {wname}...[/bold yellow]")

        # Drain
        drain_rc, _, drain_err = run_cmd(
            f'ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} '
            f'"sudo k3s kubectl drain {wname} '
            '--ignore-daemonsets --delete-emptydir-data --force"'
        )
        if drain_rc != 0:
            self.call_from_thread(
                log.write_line, f"[red]FAIL Could not drain {wname}: {drain_err}[/red]"
            )
            return

        # Terraform
        tfvars = TERRAFORM_DIR / "terraform.tfvars"
        try:
            content = tfvars.read_text()
            updated = re.sub(
                r'vm_count\s*=\s*\d+', f'vm_count       = {current - 1}', content
            )
            if updated == content:
                uc_rc, _, uc_err = run_cmd(
                    f'ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} '
                    f'"sudo k3s kubectl uncordon {wname}"'
                )
                if uc_rc == 0:
                    self.call_from_thread(
                        log.write_line,
                        f"[red]FAIL tfvars vm_count was not updated, aborting before terraform apply; uncordoned {wname}[/red]",
                    )
                else:
                    self.call_from_thread(
                        log.write_line,
                        f"[red]FAIL tfvars vm_count was not updated, aborting before terraform apply; uncordon of {wname} failed, node still cordoned: {uc_err}[/red]",
                    )
                return
            tfvars.write_text(updated)
        except OSError as e:
            uc_rc, _, uc_err = run_cmd(
                f'ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} '
                f'"sudo k3s kubectl uncordon {wname}"'
            )
            if uc_rc == 0:
                self.call_from_thread(
                    log.write_line,
                    f"[red]FAIL tfvars I/O failed ({e}), aborting before terraform apply; uncordoned {wname}[/red]",
                )
            else:
                self.call_from_thread(
                    log.write_line,
                    f"[red]FAIL tfvars I/O failed ({e}), aborting before terraform apply; uncordon of {wname} failed, node still cordoned: {uc_err}[/red]",
                )
            return

        rc, _, err = run_cmd(
            f"terraform -chdir={shlex.quote(str(TERRAFORM_DIR))} apply -auto-approve"
        )
        if rc != 0:
            tfvars.write_text(content)
            uc_rc, _, uc_err = run_cmd(
                f'ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} '
                f'"sudo k3s kubectl uncordon {wname}"'
            )
            if uc_rc == 0:
                self.call_from_thread(
                    log.write_line,
                    f"[red]FAIL Terraform failed; restored terraform.tfvars and uncordoned {wname}: {err}[/red]",
                )
            else:
                self.call_from_thread(
                    log.write_line,
                    f"[red]FAIL Terraform failed; restored terraform.tfvars; uncordon of {wname} failed, node still cordoned: {uc_err}[/red]",
                )
            return
        rc, _, err = run_cmd(
            f'ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} '
            f'"sudo k3s kubectl delete node {wname} --ignore-not-found"'
        )
        if rc != 0:
            self.call_from_thread(
                log.write_line,
                f"[red]FAIL Could not delete node {wname}: {err or 'unknown error'}[/red]",
            )
            self.call_from_thread(
                log.write_line,
                f"[red] Retry on the control node: sudo k3s kubectl delete node {wname} --ignore-not-found[/red]",
            )
            return
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
            run_cmd(f'ssh {SSH_OPTS} labadmin@{wip} "sudo rm -f /etc/rancher/node/password"')
            run_cmd(f'ssh {SSH_OPTS} labadmin@{wip} "sudo systemctl stop k3s-agent 2>/dev/null || true"')
            rc, _, _ = install_k3s_agent(wip)
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
                f'ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} '
                f'"sudo k3s ctr images list | grep trengo-search | head -1 | awk \'{{print $1}}\'"'
            )
            if img:
                run_cmd(f'ssh {SSH_OPTS} labadmin@{K3S_CONTROL_IP} "sudo k3s ctr images export /tmp/sync.tar {img}"')
                run_cmd(f'scp {SSH_OPTS} labadmin@{K3S_CONTROL_IP}:/tmp/sync.tar /tmp/')
                run_cmd(f'scp {SSH_OPTS} /tmp/sync.tar labadmin@{ip}:/tmp/')
                run_cmd(f'ssh {SSH_OPTS} labadmin@{ip} "sudo k3s ctr images import /tmp/sync.tar"')
                self.call_from_thread(log.write_line, f"  [green]OK {name} synced[/green]")
            else:
                self.call_from_thread(log.write_line, f"  [yellow][WARN] No image found for {name}[/yellow]")
        self.call_from_thread(log.write_line, "[green]OK Sync complete![/green]")

if __name__ == "__main__":
    app = LabTUI()
    app.run()
