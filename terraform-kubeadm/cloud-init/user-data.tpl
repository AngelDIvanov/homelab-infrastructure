#cloud-config

hostname: ${hostname}

fqdn: ${hostname}.homelab.local

manage_etc_hosts: true

users:

  - name: labadmin

    sudo: ALL=(ALL) NOPASSWD:ALL  # Ansible requires passwordless sudo; the account is key-only

    groups: users, admin, sudo

    shell: /bin/bash

    lock_passwd: true

    ssh_authorized_keys:

      - ${ssh_public_key}
%{ for key in extra_ssh_public_keys ~}

      - ${key}
%{ endfor ~}

package_update: true

package_upgrade: false

packages:

  - qemu-guest-agent

  - curl

runcmd:

  - systemctl enable qemu-guest-agent

  - systemctl start qemu-guest-agent
