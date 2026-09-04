version: 2
ethernets:
  ens3:
    dhcp4: false
    addresses:
      - ${ip_address}/${prefix_length}
    routes:
      - to: default
        via: ${gateway}
    nameservers:
      addresses:
        - ${gateway}
