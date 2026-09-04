output "control_ip" {
  description = "Fixed IP of the kubeadm control plane"
  value       = var.control_ip
}

output "worker_ips" {
  description = "Map of worker node name to fixed IP"
  value       = { for name, node in local.workers : name => node.ip }
}

output "nodes" {
  description = "Cluster nodes with their fixed IP and role"
  value       = { for name, node in local.nodes : name => { ip = node.ip, role = node.role } }
}
