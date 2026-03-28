#!/bin/bash
# Sync Grafana dashboard JSON files to k3s ConfigMaps

K3S_CONTROL="192.168.122.218"
SSH_OPTS="-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no"
DASHBOARD_DIR="$(cd "$(dirname "$0")/../monitoring/grafana" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
BOLD='\033[1m'

echo -e "\n${BOLD}Syncing Grafana dashboards to cluster...${NC}\n"

declare -A DASHBOARD_MAP=(
    ["grafana-dashboards-configmap.yaml"]="grafana-dashboards"
    ["grafana-dashboard-trengo.yaml"]="grafana-dashboard-trengo"
)

# Sync YAML configmaps
for file in "${!DASHBOARD_MAP[@]}"; do
    filepath="$DASHBOARD_DIR/$file"
    if [ -f "$filepath" ]; then
        printf "  %-45s" "$file"
        result=$(ssh $SSH_OPTS andy@$K3S_CONTROL "sudo k3s kubectl apply -f -" < "$filepath" 2>&1)
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ applied${NC}"
        else
            echo -e "${RED}✗ failed${NC}"
            echo "    $result"
        fi
    fi
done

# Sync raw JSON files as configmaps
for json in "$DASHBOARD_DIR"/*.json; do
    [ -f "$json" ] || continue
    name=$(basename "$json" .json)
    cm_name="grafana-dashboard-$(echo $name | tr '_' '-')"
    printf "  %-45s" "$name.json"
    result=$(ssh $SSH_OPTS andy@$K3S_CONTROL \
        "sudo k3s kubectl create configmap $cm_name \
        --from-file=$name.json=/dev/stdin \
        -n monitoring \
        --dry-run=client -o yaml | \
        sudo k3s kubectl apply -f -" < "$json" 2>&1)
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ synced${NC}"
    else
        echo -e "${RED}✗ failed${NC}"
        echo "    $result"
    fi
done

echo -e "\n${GREEN}✓ Done — restart Grafana pod to reload dashboards${NC}"
ssh $SSH_OPTS andy@$K3S_CONTROL "sudo k3s kubectl rollout restart deployment monitoring-grafana -n monitoring" 2>/dev/null
echo -e "${GREEN}✓ Grafana restarting${NC}\n"
