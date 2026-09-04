terraform {
  required_version = ">= 1.0"
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "0.7.6"
    }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
}

resource "libvirt_volume" "node" {
  for_each = local.nodes

  name             = "kubeadm-${each.key}.qcow2"
  pool             = "default"
  base_volume_pool = "default"
  base_volume_name = "ubuntu-cloud-base.qcow2"
  size             = 21474836480 # 20GB
}

resource "libvirt_cloudinit_disk" "node" {
  for_each = local.nodes

  name = "kubeadm-${each.key}-cloudinit.iso"
  pool = "default"
  user_data = templatefile("${path.module}/cloud-init/user-data.tpl", {
    hostname              = "kubeadm-${each.key}"
    ssh_public_key        = var.ssh_public_key
    extra_ssh_public_keys = var.extra_ssh_public_keys
  })
  network_config = templatefile("${path.module}/cloud-init/network-config.tpl", {
    ip_address = each.value.ip
  })
}

resource "libvirt_domain" "node" {
  for_each = local.nodes

  name      = "kubeadm-${each.key}"
  memory    = each.value.memory
  vcpu      = each.value.vcpu
  cloudinit = libvirt_cloudinit_disk.node[each.key].id

  network_interface {
    network_name   = "default"
    wait_for_lease = false
  }

  disk {
    volume_id = libvirt_volume.node[each.key].id
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
