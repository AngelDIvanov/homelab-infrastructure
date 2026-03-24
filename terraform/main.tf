terraform {
  required_version = ">= 1.0"
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "0.7.6"  # Use stable 0.7.x instead
    }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
}

# This should work with 0.7.x
resource "libvirt_volume" "worker" {
  count            = var.vm_count
  name             = "${var.vm_prefix}-${count.index + 2}.qcow2"
  pool             = "default"
  base_volume_pool = "default"
  base_volume_name = "ubuntu-cloud-base.qcow2"
  size             = 21474836480
}

resource "libvirt_cloudinit_disk" "commoninit" {
  count = var.vm_count
  name  = "${var.vm_prefix}-${count.index + 2}-cloudinit.iso"
  pool  = "default"

  user_data = templatefile("${path.module}/cloud-init/user-data.tpl", {
    hostname       = "${var.vm_prefix}-${count.index + 2}"
    ssh_public_key = var.ssh_public_key
  })

  network_config = templatefile("${path.module}/cloud-init/network-config.tpl", {
    ip_address = "192.168.122.${var.base_ip_octet + count.index}"
  })
}

resource "libvirt_domain" "worker" {
  count  = var.vm_count
  name   = "${var.vm_prefix}-${count.index + 2}"
  memory = var.vm_memory
  vcpu   = var.vm_vcpu

  cloudinit = libvirt_cloudinit_disk.commoninit[count.index].id

  network_interface {
    network_name   = "default"
    wait_for_lease = false
  }

  disk {
    volume_id = libvirt_volume.worker[count.index].id
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
