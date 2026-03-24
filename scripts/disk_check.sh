#!/bin/bash
# ============================================
#   DevOps Home Lab - Disk Pruning Script
# ============================================
#
# Author: Angel
# Purpose: Prunes unused container images to free disk space
#
# This script is called remotely by check-lab.sh when disk usage exceeds threshold.
# It is piped via SSH and executed with sudo on the target node.
#
# Environment variables:
#   DISK_THRESHOLD - Percentage threshold (default: 75)
# ============================================

THRESHOLD=${DISK_THRESHOLD:-75}

echo "Starting image prune on $(hostname)..."

# Get current disk usage
CURRENT_PCT=$(df / | awk 'NR==2{gsub(/%/,"");print $5}')
echo "Current disk usage: ${CURRENT_PCT}%"

if [ "$CURRENT_PCT" -lt "$THRESHOLD" ]; then
    echo "Disk usage below threshold ($THRESHOLD%), no pruning needed"
    exit 0
fi

# Get list of images currently in use by running containers
IMAGES_IN_USE=$(k3s ctr containers list -q 2>/dev/null | xargs -I{} k3s ctr containers info {} 2>/dev/null | grep -oP '"image":\s*"\K[^"]+' | sort -u)

# Get all images
ALL_IMAGES=$(k3s ctr images list -q 2>/dev/null)

# Prune unused images (excluding trengo-search to preserve rollback capability)
PRUNED=0
while IFS= read -r img; do
    # Skip if empty
    [ -z "$img" ] && continue
    
    # Skip pause images (needed by k8s)
    [[ "$img" == *"pause"* ]] && continue
    
    # Skip if image is in use
    echo "$IMAGES_IN_USE" | grep -q "^${img}$" && continue
    
    # Skip the latest trengo-search image (preserve for recovery)
    if [[ "$img" == *"trengo-search"* ]]; then
        # Keep at most 2 trengo images
        TRENGO_COUNT=$(echo "$ALL_IMAGES" | grep -c "trengo-search" || echo 0)
        if [ "$TRENGO_COUNT" -le 2 ]; then
            echo "Keeping trengo image: $img"
            continue
        fi
    fi
    
    # Try to remove the image
    if k3s ctr images rm "$img" 2>/dev/null; then
        echo "Removed: $img"
        ((PRUNED++))
    fi
done <<< "$ALL_IMAGES"

# Also prune any dangling/unused data
k3s ctr content prune 2>/dev/null || true

# Report results
AFTER_PCT=$(df / | awk 'NR==2{gsub(/%/,"");print $5}')
echo "Pruned $PRUNED images"
echo "Disk usage: ${CURRENT_PCT}% -> ${AFTER_PCT}%"

if [ "$AFTER_PCT" -lt "$THRESHOLD" ]; then
    echo "SUCCESS: Disk usage now below threshold"
    exit 0
else
    echo "WARNING: Still above threshold after pruning"
    exit 1
fi
