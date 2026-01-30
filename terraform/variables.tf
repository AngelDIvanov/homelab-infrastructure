variable "libvirt_uri" {
  description = "Libvirt connection URI"
  type        = string
  default     = "qemu:///system"
}

variable "vm_count" {
  description = "Number of worker VMs to create"
  type        = number
  default     = 1
}

variable "vm_memory" {
  description = "Memory in MB for each VM"
  type        = number
  default     = 2048
}

variable "vm_vcpu" {
  description = "Number of vCPUs per VM"
  type        = number
  default     = 2
}

variable "vm_prefix" {
  description = "Prefix for VM names"
  type        = string
  default     = "k3s-worker"
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
}

variable "base_ip_octet" {
  description = "Starting IP last octet"
  type        = number
  default     = 221
}
