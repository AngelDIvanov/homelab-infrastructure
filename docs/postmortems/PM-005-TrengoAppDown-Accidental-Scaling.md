# PM-005 — TrengoAppDown — Accidental Replica Scale to Zero

**Date:** March 2026  
**Severity:** CRITICAL  
**Duration:** ~2 minutes (fast detection and recovery)  
**Status:** Resolved  

---

## Summary
The Trengo Search application went completely down after the deployment was accidentally scaled to 0 replicas during a lab operation. The alerting pipeline detected the outage within 90 seconds, fired a CRITICAL alert to Slack with a direct runbook link and auto-created a GitLab incident. The application was restored within 2 minutes.

---

## Timeline

| Time | Event |
|------|-------|
| T+0 | `kubectl scale deployment trengo-search --replicas=0` run accidentally during lab maintenance |
| T+1m | Prometheus detects `kube_deployment_status_replicas_available == 0` for 1 minute |
| T+1m30s | `TrengoAppDown` CRITICAL alert fires |
| T+1m30s | Slack #incidents receives alert with runbook link and incident URL |
| T+2m | Engineer scales deployment back to 1 replica |
| T+3m | `TrengoAppDown` resolves — RESOLVED sent to Slack, GitLab issue auto-closed |

---

## Root Cause
Human error — `kubectl scale deployment trengo-search --replicas=0` run on production namespace. No confirmation prompt, takes effect immediately.

---

## Impact
- Application unavailable for ~2 minutes
- Alert and recovery pipeline worked correctly end-to-end

---

## Resolution
1. Confirmed `replicas=0` in deployment spec
2. Scaled back: `kubectl scale deployment trengo-search --replicas=1 -n default`
3. Verified pod healthy

---

## What We Did to Prevent Recurrence
- Runbook updated — explicit step for `replicas=0` scenario
- lab-control.py Nuke Test is now a dedicated menu option to avoid accidental scaling
- Alert pipeline validated end-to-end

---

## Lessons Learned
- `kubectl scale --replicas=0` has no confirmation prompt
- Runbooks must cover ambiguous recovery states
- Alerting pipeline worked perfectly — detection in 90 seconds
