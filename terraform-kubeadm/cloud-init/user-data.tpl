#cloud-config

hostname: ${hostname}

fqdn: ${hostname}.homelab.local

manage_etc_hosts: true

users:

  - name: labadmin

    sudo: ALL=(ALL) NOPASSWD:ALL

    groups: users, admin, sudo

    shell: /bin/bash

    lock_passwd: false

    ssh_authorized_keys:

      - ${ssh_public_key}

      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILgMILWmBoGWja3vsjUJ1tcRPJg05IFWVuLdOLM77Dda gitlab-runner@ci-runner

package_update: true

package_upgrade: false

packages:

  - qemu-guest-agent

  - curl

runcmd:

  - echo 'labadmin:changeme' | chpasswd   # lab default; rotate on first login or switch to ssh keys only

  - sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config

  - systemctl restart sshd

  - systemctl enable qemu-guest-agent

  - systemctl start qemu-guest-agent
