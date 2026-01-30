#cloud-config
hostname: ${hostname}
fqdn: ${hostname}.homelab.local
manage_etc_hosts: true

users:
  - name: andy
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: users, admin, sudo
    shell: /bin/bash
    ssh_authorized_keys:
      - ${ssh_public_key}

package_update: true
package_upgrade: false

packages:
  - qemu-guest-agent
  - curl

runcmd:
  - systemctl enable qemu-guest-agent
  - systemctl start qemu-guest-agent
