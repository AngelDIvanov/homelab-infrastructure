#!/bin/bash
echo "Starting CRC..."
virsh start crc
echo "Waiting for CRC to boot (this takes a while)..."
sleep 60
virsh list --all
