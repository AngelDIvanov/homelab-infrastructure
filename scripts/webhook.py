#!/usr/bin/env python3
# Alertmanager webhook → GitLab issues
# Fires when Alertmanager POSTs; opens/closes issues based on alert state.

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
import os
import hashlib
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

GITLAB_URL   = os.environ.get('GITLAB_URL',   'http://192.168.122.230:8929')
GITLAB_TOKEN = os.environ.get('GITLAB_TOKEN', '')
GITLAB_PROJECT_ID = os.environ.get('GITLAB_PROJECT_ID', '1')

SEVERITY_LABELS = {
    'critical': ['incident', 'critical', 'bug'],
    'warning':  ['incident', 'warning'],
    'info':     ['incident', 'info'],
}

SEVERITY_PREFIX = {
    'critical': '[CRITICAL]',
    'warning':  '[WARNING]',
    'info':     '[INFO]',
}

def inc_number(alert):
    fp = alert.get('fingerprint', alert['labels'].get('alertname', 'unknown'))
    return int(hashlib.md5(fp.encode()).hexdigest()[:5], 16) % 90000 + 10000

def gitlab_request(method, path, data=None):
    url = f"{GITLAB_URL}/api/v4/projects/{GITLAB_PROJECT_ID}{path}"
    headers = {
        'PRIVATE-TOKEN': GITLAB_TOKEN,
        'Content-Type': 'application/json',
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
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

def create_issue(alert, inc_num):
    severity  = alert['labels'].get('severity', 'info')
    alertname = alert['labels'].get('alertname', 'Unknown')
    namespace = alert['labels'].get('namespace', 'N/A')
    summary   = alert['annotations'].get('summary', alertname)
    desc      = alert['annotations'].get('description', '')
    prefix    = SEVERITY_PREFIX.get(severity, '[INFO]')
    labels    = SEVERITY_LABELS.get(severity, ['incident'])

    title = f"{prefix} INC-{inc_num} | {alertname}"

    body = f"""## {prefix} Incident INC-{inc_num}

**Severity:** {severity.upper()}
**Alert:** {alertname}
**Namespace:** {namespace}
**Summary:** {summary}

### Description
{desc}

### Alert Labels
```
{json.dumps(alert['labels'], indent=2)}
```

### Timeline
- **Fired:** {alert.get('startsAt', 'N/A')}

---
*Auto-created by Alertmanager*
"""

    issue = gitlab_request('POST', '/issues', {
        'title': title,
        'description': body,
        'labels': ','.join(labels),
    })

    if issue:
        log.info(f"Created issue #{issue['iid']} — {title}")
    return issue

def close_issue(issue, alert, inc_num):
    ends_at = alert.get('endsAt', 'N/A')
    
    # Add resolve comment
    gitlab_request('POST', f"/issues/{issue['iid']}/notes", {
        'body': f"**RESOLVED** — Alert cleared at {ends_at}\n\n*Auto-resolved by Alertmanager*"
    })

    # Close the issue
    gitlab_request('PUT', f"/issues/{issue['iid']}", {'state_event': 'close'})
    log.info(f"Closed issue #{issue['iid']} — INC-{inc_num}")

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        status  = payload.get('status', '')
        alerts  = payload.get('alerts', [])

        log.info(f"Received {status} with {len(alerts)} alerts")

        for alert in alerts:
            inc_num = inc_number(alert)
            alertname = alert['labels'].get('alertname', 'Unknown')

            if status == 'firing':
                existing = find_open_issue(inc_num)
                if not existing:
                    create_issue(alert, inc_num)
                else:
                    log.info(f"Issue already exists for INC-{inc_num} — skipping")

            elif status == 'resolved':
                existing = find_open_issue(inc_num)
                if existing:
                    close_issue(existing, alert, inc_num)
                else:
                    log.info(f"No open issue found for INC-{inc_num} — nothing to close")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, format, *args):
        pass  # Suppress default HTTP logs

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    log.info(f"Starting webhook receiver on port {port}")
    log.info(f"GitLab: {GITLAB_URL} | Project: {GITLAB_PROJECT_ID}")
    HTTPServer(('0.0.0.0', port), WebhookHandler).serve_forever()
