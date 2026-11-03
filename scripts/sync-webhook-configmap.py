#!/usr/bin/env python3
"""Keep the alertmanager-webhook ConfigMap in sync with scripts/webhook.py.

The Deployment runs /app/webhook.py from the ConfigMap, not the file in
scripts/. Editing one without the other silently deploys stale code, so the
ConfigMap block is generated from the script:

    python3 scripts/sync-webhook-configmap.py          # rewrite the ConfigMap
    python3 scripts/sync-webhook-configmap.py --check  # exit 1 if it is stale (CI)
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "webhook.py"
MANIFEST = ROOT / "kubernetes" / "deployments" / "alertmanager-webhook.yaml"
KEY = "  webhook.py: |\n"
INDENT = "    "


def render(source: str) -> str:
    return "".join(INDENT + line if line.strip() else "\n" for line in source.splitlines(keepends=True))


def main() -> int:
    check = "--check" in sys.argv[1:]
    manifest = MANIFEST.read_text()
    start = manifest.index(KEY) + len(KEY)
    end = manifest.index("\n---\n", start) + 1
    wanted = render(SCRIPT.read_text())
    if manifest[start:end] == wanted:
        print("ConfigMap is in sync with scripts/webhook.py")
        return 0
    if check:
        print("ConfigMap is stale: run python3 scripts/sync-webhook-configmap.py", file=sys.stderr)
        return 1
    MANIFEST.write_text(manifest[:start] + wanted + manifest[end:])
    print(f"updated {MANIFEST.relative_to(ROOT)} from {SCRIPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
