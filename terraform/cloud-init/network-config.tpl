version: 2
ethernets:
  ens3:
    dhcp4: false
    addresses:
      - ${ip_address}/24
    routes:
      - to: default
        via: 192.168.122.1
    nameservers:
      addresses:
        - 192.168.122.1
        - 8.8.8.8
