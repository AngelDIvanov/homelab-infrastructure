#!/usr/bin/env python3
# Alertmanager webhook → GitLab issues + Slack /lab AI diagnosis command

from http.server import HTTPServer, BaseHTTPRequestHandler
import json, urllib.request, urllib.error, urllib.parse
import os, hashlib, hmac, logging, subprocess, threading, time, uuid, re

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GITLAB_URL        = os.environ.get('GITLAB_URL',        'http://192.168.122.230:8929')
GITLAB_TOKEN      = os.environ.get('GITLAB_TOKEN',      '')
GITLAB_PROJECT_ID = os.environ.get('GITLAB_PROJECT_ID', '1')
SSH_KEY           = os.environ.get('SSH_KEY',           '/root/.ssh/id_rsa')
SSH_USER          = os.environ.get('SSH_USER',          'andy')
K3S_CONTROL_IP    = os.environ.get('K3S_CONTROL_IP',    '192.168.122.218')

ANTHROPIC_API_KEY    = os.environ.get('ANTHROPIC_API_KEY',    '')
ANTHROPIC_MODEL      = os.environ.get('ANTHROPIC_MODEL',      'claude-sonnet-4-6')
SLACK_BOT_TOKEN      = os.environ.get('SLACK_BOT_TOKEN',      '')
SLACK_SIGNING_SECRET = os.environ.get('SLACK_SIGNING_SECRET', '')
SLACK_INCIDENTS      = os.environ.get('SLACK_INCIDENTS',      '#incidents')

SLACK_CRITICAL_URL = os.environ.get('SLACK_CRITICAL_URL', '')
SLACK_WARNING_URL  = os.environ.get('SLACK_WARNING_URL',  '')
SLACK_INFO_URL     = os.environ.get('SLACK_INFO_URL',     '')

SLACK_CRITICAL_CHANNEL = '#incidents'
SLACK_WARNING_CHANNEL  = '#incidents'
SLACK_INFO_CHANNEL     = '#all-homelab-alerts'
ONCALL = '<!here>'

SEVERITY_LABELS = {
    'critical': ['incident', 'critical', 'bug'],
    'warning':  ['incident', 'warning'],
    'info':     ['incident', 'info'],
}

WIKI_BASE = 'http://192.168.122.230:8929/root/homelab-infrastructure/-/wikis'

RUNBOOK_URLS = {
    'TrengoAppDown':     f'{WIKI_BASE}/runbooks/TrengoAppDown',
    'TrengoAppDegraded': f'{WIKI_BASE}/runbooks/TrengoAppDegraded',
    'NodeMemoryHigh':    f'{WIKI_BASE}/runbooks/NodeMemoryHigh',
    'NodeMemoryWarning': f'{WIKI_BASE}/runbooks/NodeMemoryWarning',
    'NodeDiskHigh':      f'{WIKI_BASE}/runbooks/NodeDiskHigh',
    'PodCrashLooping':   f'{WIKI_BASE}/runbooks/PodCrashLooping',
    'PodImagePullError': f'{WIKI_BASE}/runbooks/PodImagePullError',
}

POSTMORTEM_URLS = {
    'TrengoAppDown':      f'{WIKI_BASE}/post-mortems/PM-005-TrengoAppDown-Accidental-Scaling',
    'NodeMemoryHigh':     f'{WIKI_BASE}/post-mortems/PM-003-CI-Runner-OOM',
    'NodeMemoryCritical': f'{WIKI_BASE}/post-mortems/PM-003-CI-Runner-OOM',
    'PodImagePullError':  f'{WIKI_BASE}/post-mortems/PM-002-Alertmanager-Trunc-Function',
    'PodCrashLooping':    f'{WIKI_BASE}/post-mortems/PM-004-k3s-False-Positive-Storm',
}

RUNBOOKS = {
    'NodeMemoryCritical': [
        {
            'instance_contains': '192.168.122.230',
            'host': '192.168.122.230',
            'cmd': 'sudo pkill -f stress-ng 2>/dev/null; sudo gitlab-ctl restart',
            'description': 'Killed stress-ng and restarted GitLab on k3s-infra.',
        },
        {
            'instance_contains': '192.168.122.220',
            'host': '192.168.122.220',
            'cmd': 'sudo pkill -f stress-ng 2>/dev/null; sudo gitlab-runner restart',
            'description': 'Killed stress-ng and restarted GitLab runner on ci-runner.',
        },
    ],
    'NodeDiskHigh': [
        {
            'instance_contains': '192.168.122.218',
            'host': '192.168.122.218',
            'cmd': 'sudo k3s crictl rmi --prune',
            'description': 'Pruned unused container images on k3s-control.',
        },
        {
            'instance_contains': '192.168.122.219',
            'host': '192.168.122.219',
            'cmd': 'sudo k3s crictl rmi --prune',
            'description': 'Pruned unused container images on k3s-worker-1.',
        },
        {
            'instance_contains': '192.168.122.230',
            'host': '192.168.122.230',
            'cmd': 'sudo k3s crictl rmi --prune',
            'description': 'Pruned unused container images on k3s-infra.',
        },
    ],
}

# ── Pending /lab approvals (in-memory) ───────────────────────────────────────
pending = {}   # token → {commands, response_url}

# ── SSH helpers ───────────────────────────────────────────────────────────────
def _ssh(host, cmd, timeout=30):
    try:
        r = subprocess.run(
            ['ssh', '-i', SSH_KEY, '-o', 'StrictHostKeyChecking=no',
             '-o', 'ConnectTimeout=10', f'{SSH_USER}@{host}', cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return (r.stdout + r.stderr).strip() or '(no output)'
    except subprocess.TimeoutExpired:
        return '(timeout)'
    except Exception as e:
        return f'(error: {e})'

def ssh_kube(args, timeout=20):
    return _ssh(K3S_CONTROL_IP, f'sudo k3s kubectl {args}', timeout)

# ── Cluster state gathering ───────────────────────────────────────────────────
def gather_state():
    out = []

    nodes = ssh_kube('get nodes -o wide')
    out.append(f"=== NODES ===\n{nodes}")

    unhealthy = ssh_kube(
        "get pods -A --no-headers 2>/dev/null | grep -vE '(Running|Completed|Succeeded)'"
    )
    if unhealthy and '(no output)' not in unhealthy:
        out.append(f"=== UNHEALTHY PODS ===\n{unhealthy}")

    warnings = ssh_kube(
        "get events -A --field-selector=type=Warning "
        "--sort-by='.lastTimestamp' 2>/dev/null | tail -15"
    )
    if warnings and '(no output)' not in warnings:
        out.append(f"=== RECENT WARNINGS ===\n{warnings}")

    jobs = ssh_kube(
        "get jobs -A --no-headers 2>/dev/null | grep -v ' 1/1 '"
    )
    if jobs and '(no output)' not in jobs:
        out.append(f"=== FAILED/INCOMPLETE JOBS ===\n{jobs}")

    first_bad = ssh_kube(
        "get pods -A --no-headers 2>/dev/null "
        "| grep -vE '(Running|Completed|Succeeded)' | head -1"
    )
    if first_bad and '(no output)' not in first_bad and '(error' not in first_bad:
        parts = first_bad.split()
        if len(parts) >= 2:
            ns, pod = parts[0], parts[1]
            logs = ssh_kube(
                f"logs {pod} -n {ns} --tail=40 --previous 2>/dev/null "
                f"|| sudo k3s kubectl logs {pod} -n {ns} --tail=40 2>/dev/null"
            )
            if logs and '(no output)' not in logs:
                out.append(f"=== LOGS ({ns}/{pod}) ===\n{logs}")

    return '\n\n'.join(out)

# ── Claude API ────────────────────────────────────────────────────────────────
def call_claude(user_msg, state):
    if not ANTHROPIC_API_KEY:
        return 'ANTHROPIC_API_KEY not set — cannot call Claude.'

    system = (
        "You are an SRE assistant for a homelab k3s cluster. "
        "Nodes: k3s-control (.218), k3s-worker-1 (.219), k3s-worker-2 (.221), "
        "k3s-infra (.230 — GitLab CE + Prometheus/Grafana/Loki/Alertmanager). "
        "Analyze the cluster state, then respond with: "
        "1) Brief diagnosis (2-3 sentences). "
        "2) Root cause. "
        "3) Fix commands in a single ```bash code block — kubectl/k3s/ssh only. "
        "Prefix any destructive command with a WARNING comment."
    )

    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": f"{user_msg}\n\nCluster state:\n{state}"}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read())['content'][0]['text']
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return f"Claude API error: {e}"

# ── Parse safe commands from Claude response ──────────────────────────────────
_SAFE_CMD = re.compile(r'^(sudo\s+)?(kubectl|k3s|ssh)\b')

def parse_commands(text):
    commands = []
    for block in re.findall(r'```(?:bash|sh|shell)?\n(.*?)```', text, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith('#') and _SAFE_CMD.match(line):
                commands.append(line)
    return commands

# ── Slack Bot API helpers ─────────────────────────────────────────────────────
def slack_post(channel, blocks, text=''):
    if not SLACK_BOT_TOKEN:
        log.warning("SLACK_BOT_TOKEN not set — skipping interactive post")
        return
    body = json.dumps({"channel": channel, "text": text, "blocks": blocks}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=body,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            r = json.loads(resp.read())
            if not r.get('ok'):
                log.error(f"Slack API error: {r.get('error')}")
    except Exception as e:
        log.error(f"Slack post failed: {e}")

def slack_respond(url, text, replace=True):
    body = json.dumps({"text": text, "replace_original": replace}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error(f"Slack response_url failed: {e}")

def slack_send(webhook_url, payload):
    if not webhook_url:
        log.warning("Slack webhook URL not configured — skipping")
        return
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(webhook_url, data=body,
                                   headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            log.info(f"Slack notification sent: {resp.status}")
    except Exception as e:
        log.error(f"Slack error: {e}")

# ── Slack signature verification ──────────────────────────────────────────────
def verify_slack(headers, body_bytes):
    if not SLACK_SIGNING_SECRET:
        return True
    ts  = headers.get('X-Slack-Request-Timestamp', '')
    sig = headers.get('X-Slack-Signature', '')
    if not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > 300:
            return False
    except ValueError:
        return False
    base     = f"v0:{ts}:{body_bytes.decode()}"
    expected = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)

# ── /lab slash command handler ────────────────────────────────────────────────
def handle_lab(body_bytes):
    params   = dict(urllib.parse.parse_qsl(body_bytes.decode()))
    user_msg = params.get('text', '').strip() or 'diagnose the cluster'
    resp_url = params.get('response_url', '')

    def run():
        state     = gather_state()
        diagnosis = call_claude(user_msg, state)
        commands  = parse_commands(diagnosis)

        token = str(uuid.uuid4())
        pending[token] = {"commands": commands, "response_url": resp_url}

        short  = diagnosis[:2800] + '…' if len(diagnosis) > 2800 else diagnosis
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": ":robot_face:  Claude Diagnosis"}},
            {"type": "section",
             "text": {"type": "mrkdwn", "text": short}},
        ]

        if commands:
            cmd_text = '\n'.join(f'`{c}`' for c in commands)
            blocks += [
                {"type": "divider"},
                {"type": "section",
                 "text": {"type": "mrkdwn", "text": f"*Suggested commands:*\n{cmd_text}"}},
                {"type": "actions",
                 "elements": [
                     {"type": "button",
                      "text": {"type": "plain_text", "text": ":white_check_mark:  Approve & Run"},
                      "style": "primary",
                      "value": token,
                      "action_id": "lab_approve"},
                     {"type": "button",
                      "text": {"type": "plain_text", "text": ":x:  Dismiss"},
                      "style": "danger",
                      "value": token,
                      "action_id": "lab_dismiss"},
                 ]},
            ]
        else:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_No executable commands identified._"},
            })

        slack_post(SLACK_INCIDENTS, blocks, text="Claude cluster diagnosis")

    threading.Thread(target=run, daemon=True).start()
    return json.dumps({
        "response_type": "ephemeral",
        "text": ":mag: Gathering cluster state — results in #incidents shortly…",
    }).encode()

# ── Slack interactivity handler ───────────────────────────────────────────────
def handle_action(body_bytes):
    params   = dict(urllib.parse.parse_qsl(body_bytes.decode()))
    payload  = json.loads(params.get('payload', '{}'))
    resp_url = payload.get('response_url', '')
    user     = payload.get('user', {}).get('name', 'unknown')

    for action in payload.get('actions', []):
        aid   = action.get('action_id')
        token = action.get('value')

        if aid == 'lab_dismiss':
            pending.pop(token, None)
            slack_respond(resp_url, f":x: Dismissed by *{user}*.")
            return b'ok'

        if aid == 'lab_approve':
            entry = pending.pop(token, None)
            if not entry:
                slack_respond(resp_url, ":warning: Token expired or already used.")
                return b'ok'

            commands = entry['commands']
            slack_respond(
                resp_url,
                f":hourglass_flowing_sand: Approved by *{user}* — running {len(commands)} command(s)…"
            )

            def run_cmds(cmds=commands, u=user):
                results = []
                for cmd in cmds:
                    out = _ssh(K3S_CONTROL_IP, cmd, timeout=60)
                    results.append(f"$ {cmd}\n{out}")

                full      = '\n\n'.join(results)
                truncated = full[:2700] + '\n…(truncated)' if len(full) > 2700 else full
                slack_post(
                    SLACK_INCIDENTS,
                    blocks=[
                        {"type": "header",
                         "text": {"type": "plain_text",
                                  "text": f":terminal:  Commands executed by {u}"}},
                        {"type": "section",
                         "text": {"type": "mrkdwn", "text": f"```{truncated}```"}},
                    ],
                    text=f"Command results ({u})",
                )

            threading.Thread(target=run_cmds, daemon=True).start()
            return b'ok'

    return b'ok'

# ── Alertmanager helpers ──────────────────────────────────────────────────────
def run_remediation(alert):
    alertname = alert['labels'].get('alertname', '')
    instance  = alert['labels'].get('instance', '')
    if alertname not in RUNBOOKS:
        return None
    for rb in RUNBOOKS[alertname]:
        if rb['instance_contains'] not in instance:
            continue
        log.info(f"Auto-remediation: {alertname} on {rb['host']}")
        out = _ssh(rb['host'], rb['cmd'], timeout=120)
        success = 'error' not in out.lower()
        log.info(f"Remediation {'ok' if success else 'FAILED'} on {rb['host']}")
        return {
            'success': success, 'host': rb['host'],
            'cmd': rb['cmd'], 'description': rb['description'], 'output': out[:500],
        }
    return None

def inc_number(alert):
    fp = alert.get('fingerprint', alert['labels'].get('alertname', 'unknown'))
    return int(hashlib.md5(fp.encode()).hexdigest()[:5], 16) % 90000 + 10000

def gitlab_request(method, path, data=None):
    url     = f"{GITLAB_URL}/api/v4/projects/{GITLAB_PROJECT_ID}{path}"
    headers = {'PRIVATE-TOKEN': GITLAB_TOKEN, 'Content-Type': 'application/json'}
    body    = json.dumps(data).encode() if data else None
    req     = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log.error(f"GitLab API error {e.code}: {e.read()}")
        return None

def find_open_issue(inc_num):
    issues = gitlab_request('GET', f'/issues?state=opened&search=INC-{inc_num}&labels=incident')
    if issues:
        for issue in issues:
            if f'INC-{inc_num}' in issue['title']:
                return issue
    return None

def create_issue(alert, inc_num, remediation=None):
    severity  = alert['labels'].get('severity', 'info')
    alertname = alert['labels'].get('alertname', 'Unknown')
    namespace = alert['labels'].get('namespace', 'N/A')
    instance  = alert['labels'].get('instance', 'N/A')
    summary   = alert['annotations'].get('summary', alertname)
    desc      = alert['annotations'].get('description', '')
    labels    = SEVERITY_LABELS.get(severity, ['incident'])
    title     = f"INC-{inc_num} | {severity.upper()} | {alertname}"

    runbook_url    = RUNBOOK_URLS.get(alertname, '')
    postmortem_url = POSTMORTEM_URLS.get(alertname, '')
    rb_section     = f"\n### Runbook\n{runbook_url}\n" if runbook_url else ""
    pm_section     = f"\n### Related Post-Mortem\n{postmortem_url}\n" if postmortem_url else ""

    rem_section = ""
    if remediation:
        status = "SUCCESS" if remediation['success'] else "FAILED"
        rem_section = (
            f"\n### Auto-remediation\n"
            f"- **Status:** {status}\n"
            f"- **Host:** {remediation['host']}\n"
            f"- **Command:** `{remediation['cmd']}`\n"
            f"- **Action:** {remediation['description']}\n"
            f"- **Output:**\n```\n{remediation['output']}\n```\n"
        )

    body = (
        f"## Incident INC-{inc_num}\n\n"
        f"**Severity:** {severity.upper()}\n"
        f"**Alert:** {alertname}\n"
        f"**Namespace:** {namespace}\n"
        f"**Instance:** {instance}\n"
        f"**Summary:** {summary}\n\n"
        f"### Description\n{desc}\n"
        f"{rb_section}{pm_section}{rem_section}"
        f"### Timeline\n- Fired: {alert.get('startsAt', 'N/A')}\n\n"
        f"---\nAuto-created by Alertmanager webhook"
    )
    issue = gitlab_request('POST', '/issues', {
        'title': title, 'description': body, 'labels': ','.join(labels),
    })
    if issue:
        log.info(f"Created issue #{issue['iid']} — {title}")
    return issue

def close_issue(issue, alert, inc_num):
    gitlab_request('POST', f"/issues/{issue['iid']}/notes", {
        'body': (f"RESOLVED — Alert cleared at {alert.get('endsAt', 'N/A')}\n\n"
                 "Auto-resolved by Alertmanager webhook"),
    })
    gitlab_request('PUT', f"/issues/{issue['iid']}", {'state_event': 'close'})
    log.info(f"Closed issue #{issue['iid']} — INC-{inc_num}")

def notify_firing(alert, inc_num, issue=None, remediation=None):
    severity   = alert['labels'].get('severity', 'info')
    alertname  = alert['labels'].get('alertname', 'Unknown')
    namespace  = alert['labels'].get('namespace', 'N/A')
    summary    = alert['annotations'].get('summary', alertname)
    desc       = alert['annotations'].get('description', '')
    started    = alert.get('startsAt', 'N/A')
    runbook    = RUNBOOK_URLS.get(alertname, '')
    postmortem = POSTMORTEM_URLS.get(alertname, '')

    if severity == 'critical':
        webhook_url = SLACK_CRITICAL_URL
        channel, color = SLACK_CRITICAL_CHANNEL, 'danger'
        header     = f"CRITICAL | {alertname}"
        oncall     = f"*On-call:* {ONCALL} — please investigate"
    elif severity == 'warning':
        webhook_url = SLACK_WARNING_URL or SLACK_CRITICAL_URL
        channel, color = SLACK_WARNING_CHANNEL, 'warning'
        header     = f"WARNING | {alertname}"
        oncall     = f"*FYI:* {ONCALL}"
    else:
        webhook_url = SLACK_INFO_URL or SLACK_CRITICAL_URL
        channel, color = SLACK_INFO_CHANNEL, 'good'
        header     = f"INFO | {alertname}"
        oncall     = ''

    lines = [
        f"*Summary:* {summary}",
        f"*Description:* {desc}",
        f"*Namespace:* `{namespace}`",
        f"*Started:* {started}",
    ]
    if oncall:       lines.append(oncall)
    if runbook:      lines.append(f"*Runbook:* {runbook}")
    if postmortem:   lines.append(f"*Post-mortem:* {postmortem}")
    if issue:        lines.append(f"*Incident:* {issue.get('web_url', '')}")
    if remediation:
        s = "SUCCESS" if remediation['success'] else "FAILED"
        lines.append(f"*Auto-remediation:* {s} — {remediation['description']}")

    slack_send(webhook_url, {
        'channel': channel,
        'attachments': [{
            'color': color, 'title': header,
            'title_link': 'http://192.168.122.218:30090/alerts',
            'text': '\n'.join(lines),
            'footer': 'Homelab Alertmanager | http://192.168.122.218:30093',
        }],
    })

def notify_resolved(alert, inc_num, issue=None):
    severity  = alert['labels'].get('severity', 'info')
    alertname = alert['labels'].get('alertname', 'Unknown')
    namespace = alert['labels'].get('namespace', 'N/A')
    summary   = alert['annotations'].get('summary', alertname)
    started   = alert.get('startsAt', 'N/A')
    ended     = alert.get('endsAt',   'N/A')

    webhook_url = SLACK_CRITICAL_URL if severity == 'critical' else (SLACK_WARNING_URL or SLACK_CRITICAL_URL)
    channel     = SLACK_CRITICAL_CHANNEL if severity == 'critical' else SLACK_WARNING_CHANNEL

    lines = [
        f"*Summary:* {summary}",
        f"*Namespace:* `{namespace}`",
        f"*Started:* {started}",
        f"*Resolved:* {ended}",
    ]
    if issue:
        lines.append(f"*Incident:* {issue.get('web_url', '')} (closed)")

    slack_send(webhook_url, {
        'channel': channel,
        'attachments': [{
            'color': 'good', 'title': f"RESOLVED | {alertname}",
            'title_link': 'http://192.168.122.218:30090/alerts',
            'text': '\n'.join(lines),
            'footer': 'Homelab Alertmanager | http://192.168.122.218:30093',
        }],
    })

# ── HTTP handler ──────────────────────────────────────────────────────────────
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)

        # ── Slack slash command: POST /slack/lab ──────────────────────────────
        if self.path == '/slack/lab':
            if not verify_slack(dict(self.headers), body):
                self.send_response(401); self.end_headers(); return
            resp = handle_lab(body)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(resp)
            return

        # ── Slack interactivity: POST /slack/actions ──────────────────────────
        if self.path == '/slack/actions':
            if not verify_slack(dict(self.headers), body):
                self.send_response(401); self.end_headers(); return
            handle_action(body)
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
            return

        # ── Alertmanager webhook: POST / ──────────────────────────────────────
        try:
            payload = json.loads(body)
        except Exception:
            self.send_response(400); self.end_headers(); return

        status = payload.get('status', '')
        alerts = payload.get('alerts', [])
        log.info(f"Received {status} with {len(alerts)} alerts")

        for alert in alerts:
            inc_num   = inc_number(alert)
            severity  = alert['labels'].get('severity', 'info')
            alertname = alert['labels'].get('alertname', 'Unknown')

            if status == 'firing':
                existing = find_open_issue(inc_num)
                if not existing:
                    remediation = run_remediation(alert)
                    if severity == 'critical':
                        issue = create_issue(alert, inc_num, remediation)
                        notify_firing(alert, inc_num, issue, remediation)
                    else:
                        notify_firing(alert, inc_num, None, remediation)
                else:
                    log.info(f"Issue already exists for INC-{inc_num} — skipping")

            elif status == 'resolved':
                issue = find_open_issue(inc_num) if severity == 'critical' else None
                if issue:
                    close_issue(issue, alert, inc_num)
                notify_resolved(alert, inc_num, issue)

        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')

    def log_message(self, *args):
        pass

if __name__ == '__main__':
    log.info(f"Starting webhook on :8080 | GitLab: {GITLAB_URL} | Project: {GITLAB_PROJECT_ID}")
    log.info(f"Slack /lab endpoint: POST /slack/lab | Actions: POST /slack/actions")
    HTTPServer(('0.0.0.0', 8080), WebhookHandler).serve_forever()
