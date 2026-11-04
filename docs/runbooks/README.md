# Runbooks Index

Recovery procedures for homelab-infrastructure alerts and failure modes.

## Control Plane
| Runbook | Alert |
|---------|-------|
| [API Server Down](control-plane/api-server-down.md) | `ControlPlaneUnhealthy`, `ControlPlaneNodeNotReady` |

## Nodes
| Runbook | Alert |
|---------|-------|
| [Worker Node Not Ready](nodes/worker-node-not-ready.md) | `WorkerNodeDown` |
| [Cluster Drift](nodes/cluster-drift.md) | `ClusterDrift` |
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
| [Registry Down](applications/registry-down.md) | `LocalRegistryDown`, `PodImagePullError` |
| [Secret Rotation](applications/secret-rotation.md) | Planned maintenance / compromise response |

## Monitoring Stack
| Runbook | Alert |
|---------|-------|
| [Monitoring Stack Down](monitoring/monitoring-stack-down.md) | Manual |
| [Webhook Down](monitoring/webhook-down.md) | `AlertmanagerFailedToSendAlerts` |

## Disaster Recovery
| Runbook | Scenario |
|---------|----------|
| [Full Cluster Recovery](disaster-recovery/full-cluster-recovery.md) | Host hypervisor failure |

---

## Lab addresses

| Host | IP | Role |
|---|---|---|
| kubeadm-control | 192.168.122.240 | control plane (API server, etcd, scheduler, controller-manager) |
| kubeadm-worker-1 | 192.168.122.241 | worker |
| kubeadm-worker-2 | 192.168.122.242 | worker |
| infra VM | 192.168.122.230 | NFS server (`/data`) + GitLab CE, outside the cluster |
| ci-runner | 192.168.122.220 | GitLab shell runner, outside the cluster |
| Traefik | 192.168.122.200 | MetalLB address for the `*.homelab.local` ingresses |

`kubectl` runs from the operator machine with the kubeadm admin kubeconfig, or
on `kubeadm-control` as `labadmin`.

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
