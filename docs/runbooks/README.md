# Runbooks Index

Recovery procedures for homelab-infrastructure alerts and failure modes.

## Control Plane
| Runbook | Alert |
|---------|-------|
| [API Server Down](control-plane/api-server-down.md) | `K3sControlPlaneUnhealthy` |

## Nodes
| Runbook | Alert |
|---------|-------|
| [Worker Node Not Ready](nodes/worker-node-not-ready.md) | `K3sWorkerNodeDown`, `KubeNodeNotReady` |
| [Node Memory Pressure](nodes/node-memory-pressure.md) | `NodeMemoryWarning`, `NodeMemoryHigh`, `NodeMemoryCritical`, `ContainerOOMKilled` |
| [Node Disk Full](nodes/node-disk-full.md) | `NodeDiskHigh`, `NodeDiskCritical` |

## Storage
| Runbook | Alert |
|---------|-------|
| [NFS Server Down](storage/nfs-server-down.md) | Manual |
| [PVC Pending](storage/pvc-pending.md) | `PVCPending` |

## Applications
| Runbook | Alert |
|---------|-------|
| [Pod CrashLoop](applications/pod-crashloop.md) | `PodCrashLooping`, `CronJobFailed` |
| [Pod Image Pull Error](applications/pod-image-pull-error.md) | `PodImagePullError` |
| [Trengo App Down](applications/trengo-app-down.md) | `TrengoAppDown`, `TrengoAppDegraded` |

## Monitoring Stack
| Runbook | Alert |
|---------|-------|
| [Monitoring Stack Down](monitoring/monitoring-stack-down.md) | Manual |

## Disaster Recovery
| Runbook | Scenario |
|---------|----------|
| [Full Cluster Recovery](disaster-recovery/full-cluster-recovery.md) | Host hypervisor failure |

---

## Runbook Template

Each runbook follows this structure:

```
# Alert Name

## Alert
`AlertName` (severity: critical/warning/info)

## Symptoms
## Root Cause Checklist
## Recovery Steps
## Verify Recovery
## Escalation
## Post-Incident
## Related Alerts
```
