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

# ── Control plane ──────────────────────────────────────────────
resource "libvirt_volume" "control" {
  name             = "kubeadm-control.qcow2"
  pool             = "default"
  base_volume_pool = "default"
  base_volume_name = "ubuntu-cloud-base.qcow2"
  size             = 21474836480  # 20GB
}

resource "libvirt_cloudinit_disk" "control" {
  name  = "kubeadm-control-cloudinit.iso"
  pool  = "default"
  user_data = templatefile("${path.module}/cloud-init/user-data.tpl", {
    hostname       = "kubeadm-control"
    ssh_public_key = var.ssh_public_key
  })
  network_config = templatefile("${path.module}/cloud-init/network-config.tpl", {
    ip_address = var.control_ip
  })
}

resource "libvirt_domain" "control" {
  name      = "kubeadm-control"
  memory    = var.control_memory
  vcpu      = var.control_vcpu
  cloudinit = libvirt_cloudinit_disk.control.id

  network_interface {
    network_name   = "default"
    wait_for_lease = false
  }

  disk {
    volume_id = libvirt_volume.control.id
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

# ── Worker node ────────────────────────────────────────────────
resource "libvirt_volume" "worker" {
  name             = "kubeadm-worker-1.qcow2"
  pool             = "default"
  base_volume_pool = "default"
  base_volume_name = "ubuntu-cloud-base.qcow2"
  size             = 21474836480  # 20GB
}

resource "libvirt_cloudinit_disk" "worker" {
  name  = "kubeadm-worker-1-cloudinit.iso"
  pool  = "default"
  user_data = templatefile("${path.module}/cloud-init/user-data.tpl", {
    hostname       = "kubeadm-worker-1"
    ssh_public_key = var.ssh_public_key
  })
  network_config = templatefile("${path.module}/cloud-init/network-config.tpl", {
    ip_address = var.worker_ip
  })
}

resource "libvirt_domain" "worker" {
  name      = "kubeadm-worker-1"
  memory    = var.worker_memory
  vcpu      = var.worker_vcpu
  cloudinit = libvirt_cloudinit_disk.worker.id

  network_interface {
    network_name   = "default"
    wait_for_lease = false
  }

  disk {
    volume_id = libvirt_volume.worker.id
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
