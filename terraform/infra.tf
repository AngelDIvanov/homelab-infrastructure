# ─────────────────────────────────────────────────────────────
#  k3s-infra — dedicated node for stateful monitoring workloads
#  Fixed IP: 192.168.122.230
#  Taint: node-role=infra:NoSchedule
# ─────────────────────────────────────────────────────────────

variable "infra_enabled" {
  description = "Whether to create the infra node"
  type        = bool
  default     = false
}

variable "infra_memory" {
  description = "Memory in MB for infra node"
  type        = number
  default     = 3072
}

variable "infra_vcpu" {
  description = "vCPUs for infra node"
  type        = number
  default     = 2
}

variable "infra_ip" {
  description = "Fixed IP for infra node"
  type        = string
  default     = "192.168.122.230"
}

resource "libvirt_volume" "infra" {
  count            = var.infra_enabled ? 1 : 0
  name             = "k3s-infra.qcow2"
  pool             = "default"
  base_volume_pool = "default"
  base_volume_name = "ubuntu-cloud-base.qcow2"
  size             = 32212254720  # 30GB — more space for logs/metrics
}

resource "libvirt_cloudinit_disk" "infra" {
  count = var.infra_enabled ? 1 : 0
  name  = "k3s-infra-cloudinit.iso"
  pool  = "default"
  user_data = templatefile("${path.module}/cloud-init/user-data.tpl", {
    hostname       = "k3s-infra"
    ssh_public_key = var.ssh_public_key
  })
  network_config = templatefile("${path.module}/cloud-init/network-config.tpl", {
    ip_address = var.infra_ip
  })
}

resource "libvirt_domain" "infra" {
  count     = var.infra_enabled ? 1 : 0
  name      = "k3s-infra"
  memory    = var.infra_memory
  vcpu      = var.infra_vcpu
  cloudinit = libvirt_cloudinit_disk.infra[0].id

  network_interface {
    network_name   = "default"
    wait_for_lease = false
  }

  disk {
    volume_id = libvirt_volume.infra[0].id
  }

  console {
    type        = "pty"
    target_type = "serial"
    target_port = "0"
  }

  graphics {
    type        = "spice"
    listen_type = "address"
    autoport    = true
  }
}

output "infra_ip" {
  value = var.infra_enabled ? var.infra_ip : "not created"
}
