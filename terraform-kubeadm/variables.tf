variable "libvirt_uri" {
  description = "Libvirt connection URI"
  type        = string
  default     = "qemu:///system"
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
}

variable "control_ip" {
  description = "Fixed IP for kubeadm control plane"
  type        = string
  default     = "192.168.122.240"
}

variable "worker_ip" {
  description = "Fixed IP for kubeadm worker-1"
  type        = string
  default     = "192.168.122.241"
}

variable "control_memory" {
  description = "Memory in MB for control plane"
  type        = number
  default     = 2048
}

variable "control_vcpu" {
  description = "vCPUs for control plane"
  type        = number
  default     = 2
}

variable "worker_memory" {
  description = "Memory in MB for worker"
  type        = number
  default     = 2048
}

variable "worker_vcpu" {
  description = "vCPUs for worker"
  type        = number
  default     = 2
}
