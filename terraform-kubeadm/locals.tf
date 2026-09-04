locals {
  workers = {
    for i in range(var.worker_count) :
    "worker-${i + 1}" => {
      ip     = cidrhost(var.node_cidr, var.worker_ip_offset + i)
      memory = var.worker_memory
      vcpu   = var.worker_vcpu
      role   = "worker"
    }
  }
  nodes = merge(
    { control = {
      ip     = var.control_ip
      memory = var.control_memory
      vcpu   = var.control_vcpu
      role   = "control"
    } },
    local.workers,
  )
}
