moved {
  from = libvirt_volume.control
  to   = libvirt_volume.node["control"]
}

moved {
  from = libvirt_cloudinit_disk.control
  to   = libvirt_cloudinit_disk.node["control"]
}

moved {
  from = libvirt_domain.control
  to   = libvirt_domain.node["control"]
}

moved {
  from = libvirt_volume.worker
  to   = libvirt_volume.node["worker-1"]
}

moved {
  from = libvirt_cloudinit_disk.worker
  to   = libvirt_cloudinit_disk.node["worker-1"]
}

moved {
  from = libvirt_domain.worker
  to   = libvirt_domain.node["worker-1"]
}
