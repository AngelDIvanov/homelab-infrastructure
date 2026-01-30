output "worker_ips" {
  description = "IP addresses of worker VMs"
  value = {
    for idx, vm in libvirt_domain.worker :
    vm.name => length(vm.network_interface) > 0 && length(vm.network_interface[0].addresses) > 0 ? vm.network_interface[0].addresses[0] : "pending"
  }
}

output "worker_names" {
  description = "Names of worker VMs"
  value       = [for vm in libvirt_domain.worker : vm.name]
}

output "worker_count" {
  description = "Number of worker VMs created"
  value       = var.vm_count
}
