#!/usr/bin/env python3
"""Fail CI when the ConfigMap copy of webhook.py drifts from scripts/webhook.py.

kubernetes/deployments/alertmanager-webhook.yaml embeds the webhook script in
a ConfigMap (block scalar) so the pod needs no volume mounts. That copy must
stay byte-identical to scripts/webhook.py — a stale embedded copy silently
deploys old (possibly unhardened) code while the repo looks fixed.

Extraction is textual (find the `webhook.py: |` block, dedent 4 spaces), so
this script needs no third-party dependencies.

Exit codes: 0 in sync, 1 drifted or unreadable.
"""
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "kubernetes" / "deployments" / "alertmanager-webhook.yaml"
SCRIPT = ROOT / "scripts" / "webhook.py"


def extract_embedded() -> str | None:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == "  webhook.py: |":
            start = i + 1
            break
    if start is None:
        return None
    out = []
    for line in lines[start:]:
        if line.strip() == "":
            out.append("")
            continue
        if not line.startswith("    "):
            break
        out.append(line[4:])
    return "\n".join(out).rstrip("\n")


def main() -> int:
    embedded = extract_embedded()
    if not embedded:
        print(f"ERROR: no `webhook.py: |` block found in {MANIFEST}")
        return 1

    source = SCRIPT.read_text(encoding="utf-8").rstrip("\n")
    if embedded == source:
        print("OK: ConfigMap webhook.py is in sync with scripts/webhook.py")
        return 0

    diff = list(
        difflib.unified_diff(
            embedded.splitlines(),
            source.splitlines(),
            "configmap/webhook.py",
            "scripts/webhook.py",
            lineterm="",
        )
    )[:20]
    print("ERROR: ConfigMap webhook.py differs from scripts/webhook.py — regenerate it.")
    print("First differing lines:")
    print("\n".join(diff))
    return 1


if __name__ == "__main__":
    sys.exit(main())
