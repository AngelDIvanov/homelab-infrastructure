# PM-006 — ngrok CreateContainerError — containerd 2.2 Nix Symlink Rejection

**Date:** 2026-04-11  
**Severity:** WARNING  
**Duration:** ~2 hours (ngrok pod unable to restart; old pod remained live throughout)  
**Status:** Resolved  

---

## Summary

After upgrading the k3s cluster from v1.33.6/v1.34.5 to v1.34.6, the `ngrok-webhook` pod began failing to start on any new container creation attempt with the error `path escapes from parent`. The official `ngrok/ngrok:latest` image is built with Nix, which stores files in `/nix/store/` and uses absolute symlinks (e.g. `/etc/passwd → /nix/store/<hash>-passwd/etc/passwd`). containerd 2.2.2 — shipped with k3s v1.34.6 — introduced strict symlink path checking and rejects any symlink whose target escapes the container root.

The existing pod survived because it predated the upgrade and containerd does not re-check running containers. Any restart, rollout, or new scheduling would have failed. Alertmanager began recording ~9% notification failures to the webhook integration, triggering `AlertmanagerClusterFailedToSendAlertsAll`.

---

## Timeline

| Time | Event |
|------|-------|
| ~06:00 | k3s cluster upgraded to v1.34.6 across all 4 nodes; containerd bumped to 2.2.2 |
| ~06:15 | Alertmanager restarts during k3s-infra upgrade, fails to deliver queued alerts to webhook → failure rate crosses threshold |
| ~06:16 | `AlertmanagerClusterFailedToSendAlertsAll` fires; repeat notifications begin every 5 min |
| ~08:13 | Engineer approves Claude auto-diagnosis; Claude runs `kubectl rollout restart deployment/ngrok-webhook` — creates a new pod that immediately fails with `CreateContainerError` |
| ~08:13 | Old pod still Running; new pod crashes in a loop; Claude misdiagnoses as image corruption and suggests rebuilding trengo-search |
| ~08:18 | Second Claude diagnosis posted (alert resolved and refired); still incorrect |
| ~08:20 | Root cause identified: containerd 2.2.2 rejects Nix absolute symlinks in ngrok image |
| ~08:22 | Wrapper image built on hypervisor (Docker unaffected by containerd 2.2.2 restriction) |
| ~08:23 | `192.168.122.218:30500/ngrok:3.37.6` pushed to local registry |
| ~08:24 | Deployment updated to wrapper image; new pod starts successfully |
| ~08:24 | Rollout complete; `AlertmanagerClusterFailedToSendAlertsAll` resolves |

---

## Root Cause

**Primary:** `ngrok/ngrok:latest` uses Nix packaging. Nix stores all files under `/nix/store/<hash>-<pkg>/` and creates absolute symlinks in standard paths (e.g. `/etc/passwd → /nix/store/.../etc/passwd`). containerd 2.2.2 added strict symlink validation during container mount setup and rejects absolute symlinks whose targets resolve outside the container root filesystem.

**Contributing:** `imagePullPolicy: Always` on the deployment meant every pod creation pulled a fresh image — but even pinning to the original digest did not help since the symlink issue affects all versions of the ngrok Nix image.

**Compounding:** Claude's auto-diagnosis was incorrect. It identified the error as image layer corruption and suggested rebuilding trengo-search, which was unrelated. This caused unnecessary rollout restarts and prolonged the incident.

---

## Impact

- `ngrok-webhook` unable to be rescheduled; any node failure or rollout would have lost the tunnel permanently until manual intervention
- Alertmanager recorded ~9% notification failure rate to the webhook integration
- Slack was spammed with `:repeat: Still firing` every 5 minutes for ~2 hours
- No actual alert delivery loss — the existing ngrok pod remained up throughout

---

## Resolution

Built a multi-stage wrapper image that copies only the static ngrok binary from the Nix image into a clean Alpine 3.19 base, avoiding all Nix symlinks:

```dockerfile
FROM ngrok/ngrok:latest AS src
FROM alpine:3.19
COPY --from=src /nix/store/hylndpagbhv7gww08jnxdcb3k2kc945y-ngrok-3.37.6/bin/ngrok /usr/local/bin/ngrok
ENTRYPOINT ["/usr/local/bin/ngrok"]
```

Pushed to local registry as `192.168.122.218:30500/ngrok:3.37.6`. Deployment updated to use this image with `imagePullPolicy: IfNotPresent`.

---

## What We Did to Prevent Recurrence

- `Dockerfile.ngrok` and `ngrok-webhook.yaml` committed to repo under `kubernetes/deployments/`
- Deployment now uses local registry image pinned to version tag (not `:latest` from Docker Hub)
- `imagePullPolicy` changed from `Always` to `IfNotPresent`
- Dockerfile includes instructions for updating to a new ngrok version

---

## Lessons Learned

- **Test new containerd versions against all running images before upgrading k3s** — a quick `crictl pull && crictl run` smoke test on a non-critical node would have caught this before the rollout
- **Avoid `imagePullPolicy: Always` with `:latest` tags from Docker Hub** — pin versions and use the local registry for anything running in the cluster
- **Claude misdiagnosis:** the `path escapes from parent` error was novel enough that Claude incorrectly attributed it to image corruption. The correct diagnosis required reading the containerd snapshot filesystem directly (`readlink` on the overlay snapshot) to find the Nix absolute symlink. Consider adding a runbook entry for this class of error
- **Repeat notification spam:** the 5-minute drift-check cycle caused a Slack notification on every fire. Rate-limiting repeat notifications is a future improvement

---

## Related

- Runbook: `docs/runbooks/nodes/cluster-drift.md` (also updated 2026-04-11 for k3s token issues found same day)
- GitLab incident: INC-83
- Fix commit: `88706f9`
