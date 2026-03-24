#!/bin/bash
set -e

WORKER_NUM=${1:-2}
VM_NAME="k3s-worker-${WORKER_NUM}"
IP_OCTET=$((220 + WORKER_NUM))
IP_ADDRESS="192.168.122.${IP_OCTET}"
MEMORY=2048
VCPUS=2
DISK_SIZE=20

SSH_PUBLIC_KEY="$(cat ~/.ssh/id_rsa.pub)"

echo "Creating worker VM: ${VM_NAME}"
echo "IP Address: ${IP_ADDRESS}"

cat > /tmp/${VM_NAME}-user-data << USERDATA
#cloud-config
hostname: ${VM_NAME}
fqdn: ${VM_NAME}.homelab.local
manage_etc_hosts: true

users:
  - name: andy
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: users, admin, sudo
    shell: /bin/bash
    ssh_authorized_keys:
      - ${SSH_PUBLIC_KEY}

package_update: true
package_upgrade: false

packages:
  - qemu-guest-agent
  - curl

runcmd:
  - systemctl enable qemu-guest-agent
  - systemctl start qemu-guest-agent
USERDATA

cat > /tmp/${VM_NAME}-network-config << NETCONFIG
version: 2
ethernets:
  enp1s0:
    dhcp4: false
    addresses:
      - ${IP_ADDRESS}/24
    gateway4: 192.168.122.1
    nameservers:
      addresses:
        - 8.8.8.8
        - 8.8.4.4
NETCONFIG

cat > /tmp/${VM_NAME}-meta-data << METADATA
instance-id: ${VM_NAME}
local-hostname: ${VM_NAME}
METADATA

genisoimage -output /tmp/${VM_NAME}-cidata.iso \
  -volid cidata -joliet -rock \
  /tmp/${VM_NAME}-user-data \
  /tmp/${VM_NAME}-meta-data \
  /tmp/${VM_NAME}-network-config

sudo cp /tmp/${VM_NAME}-cidata.iso /var/lib/libvirt/images/
sudo chown libvirt-qemu:kvm /var/lib/libvirt/images/${VM_NAME}-cidata.iso

sudo virt-install \
  --name ${VM_NAME} \
  --memory ${MEMORY} \
  --vcpus ${VCPUS} \
  --disk path=/var/lib/libvirt/images/${VM_NAME}.qcow2,size=${DISK_SIZE},backing_store=/var/lib/libvirt/images/ubuntu-22.04-base-fixed.qcow2,format=qcow2 \
  --disk path=/var/lib/libvirt/images/${VM_NAME}-cidata.iso,device=cdrom \
  --network network=default \
  --os-variant ubuntu22.04 \
  --graphics spice \
  --noautoconsole \
  --import

echo "✓ VM ${VM_NAME} created!"
echo "Waiting 60 seconds for boot..."
sleep 60

ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no andy@${IP_ADDRESS} "hostname" && echo "✓ SSH working!"
