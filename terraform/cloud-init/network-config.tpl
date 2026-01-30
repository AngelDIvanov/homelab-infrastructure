version: 2
ethernets:
  enp1s0:
    dhcp4: false
    addresses:
      - ${ip_address}/24
    gateway4: 192.168.122.1
    nameservers:
      addresses:
        - 8.8.8.8
