variable "libvirt_uri" {
  description = "Libvirt connection URI"
  type        = string
  default     = "qemu:///system"
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
}

variable "extra_ssh_public_keys" {
  description = "Additional SSH public keys injected into labadmin (e.g. CI runner keys)"
  type        = list(string)
  default     = []
}

variable "control_ip" {
  description = "Fixed IP for kubeadm control plane"
  type        = string
  default     = "192.168.122.240"
}

variable "worker_count" {
  description = "Number of kubeadm worker nodes"
  type        = number
  default     = 2

  validation {
    condition     = var.worker_count >= 1 && var.worker_count <= 5
    error_message = "worker_count must be between 1 and 5."
  }
}

variable "node_cidr" {
  description = "CIDR of the libvirt default network; nodes get fixed IPs from it"
  type        = string
  default     = "192.168.122.0/24"
}

variable "worker_ip_offset" {
  description = "Last octet of worker-1; worker-N gets worker_ip_offset + N - 1"
  type        = number
  default     = 241
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
