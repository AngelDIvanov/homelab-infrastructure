#!/bin/bash
echo "Shutting down CRC..."
virsh shutdown crc
echo "Waiting for CRC to stop..."
sleep 15
virsh list --all
