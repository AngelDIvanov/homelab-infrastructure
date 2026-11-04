terraform {
  required_version = ">= 1.0"
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "0.7.6" # Use stable 0.7.x instead
    }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
}

# This root only manages the infra VM (NFS server + GitLab CE), see infra.tf.
# The Kubernetes nodes live in terraform-kubeadm/.
