#!/bin/bash
# ============================================
#   DevOps Home Lab - Health Check & Auto-Fix
# ============================================
#
# Author: Angel
# Purpose: Validates and auto-repairs home lab infrastructure
#
# Checks performed:
#   - VM power state and connectivity
#   - Service availability (curl)
#   - Kubernetes cluster health
#   - Monitoring stack status
#   - Worker node agents
#   - Disk health + auto image pruning
#   - K8s Dashboard & Portainer
#
# Auto-fixes:
#   - Starts powered-off VMs
#   - Restarts inactive k3s-agent
#   - Deletes stuck pods
#   - Syncs container images across nodes
#   - Prunes unused images when disk is low
#   - Restarts k3s if NodePort unreachable
#
# Usage: 
#   ./check-lab.sh          - Normal checks with auto-start if needed
#   ./check-lab.sh --restart - Force restart all VMs before checks
#   ./check-lab.sh --reboot  - Force hard reboot (destroy + start) all VMs
# ============================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

# Static infrastructure (these don't scale)
K3S_CONTROL_IP="192.168.122.218"
CI_RUNNER_IP="192.168.122.220"

# SSH options
SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no"

# Static VM definitions (control + ci-runner)
declare -A VMS=(
    ["ci-runner"]="$CI_RUNNER_IP"
    ["k3s-control"]="$K3S_CONTROL_IP"
)

# SSH helper for control plane
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
K3S_CMD="ssh $SSH_OPTS andy@$K3S_CONTROL_IP"

# Dynamic worker discovery from k3s
declare -A WORKERS
discover_workers() {
    echo -e "${DIM}Discovering workers from k3s cluster...${NC}"
    
    # Get workers from k3s (name and IP)
    local worker_data
    worker_data=$($K3S_CMD "sudo k3s kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name} {.status.addresses[?(@.type==\"InternalIP\")].address}{\"\\n\"}{end}'" 2>/dev/null | grep -v control)
    
    if [ -z "$worker_data" ]; then
        echo -e "${YELLOW}Warning: Could not discover workers from k3s${NC}"
        return 1
    fi
    
    while IFS=' ' read -r name ip; do
        if [ -n "$name" ] && [ -n "$ip" ]; then
            WORKERS["$name"]="$ip"
            VMS["$name"]="$ip"
        fi
    done <<< "$worker_data"
    
    echo -e "${DIM}Found ${#WORKERS[@]} workers: ${!WORKERS[*]}${NC}\n"
    return 0
}

# Helper function to get SSH command for any node
ssh_cmd() {
    local ip=$1
    echo "ssh $SSH_OPTS andy@$ip"
}

# Command-line flags
FORCE_RESTART=false
FORCE_REBOOT=false
if [[ "$1" == "--restart" ]]; then
    FORCE_RESTART=true
elif [[ "$1" == "--reboot" ]]; then
    FORCE_REBOOT=true
fi

# Banner
clear
echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════════════════╗"
echo "║                                                                       ║"
echo "║           🏠  DevOps Home Lab - Health Check  🏠                      ║"
echo "║                                                                       ║"
echo "╠═══════════════════════════════════════════════════════════════════════╣"
echo "║  K3s Cluster │ GitLab CI/CD │ Prometheus │ Grafana │ Portainer       ║"
echo "╚═══════════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "${DIM}Started at: $(date '+%Y-%m-%d %H:%M:%S')${NC}\n"

# Discover workers early
discover_workers

# Spinner for long running tasks
spinner() {
    local pid=$1
    local delay=0.1
    local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    while [ "$(ps a | awk '{print $1}' | grep $pid)" ]; do
        for (( i=0; i<${#frames}; i++ )); do
            printf "\r  ${CYAN}[${frames:$i:1}]${NC} "
            sleep $delay
        done
    done
    printf "\r      \r"
}

# Section header
section() {
    echo -e "\n${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Live check function with proper line clearing
check() {
    local desc="$1"
    local cmd="$2"
    printf "  %-55s" "$desc"
    eval "$cmd" &>/dev/null &
    local pid=$!
    spinner $pid
    wait $pid
    local result=$?
    printf "\r  %-55s" "$desc"
    if [ $result -eq 0 ]; then
        echo -e "${GREEN}✓ PASS${NC}"
        ((PASS++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}"
        ((FAIL++))
        return 1
    fi
}

# Warning check (doesn't fail) with proper line clearing
check_warn() {
    local desc="$1"
    local cmd="$2"
    printf "  %-55s" "$desc"
    eval "$cmd" &>/dev/null &
    local pid=$!
    spinner $pid
    wait $pid
    local result=$?
    printf "\r  %-55s" "$desc"
    if [ $result -eq 0 ]; then
        echo -e "${GREEN}✓ PASS${NC}"
        ((PASS++))
        return 0
    else
        echo -e "${YELLOW}⚠ WARN${NC}"
        ((WARN++))
        return 1
    fi
}

# VM helper functions
check_vm_running() {
    local vm_name=$1
    virsh list --state-running 2>/dev/null | grep -q "$vm_name"
}

start_vm() {
    local vm_name=$1
    printf "  %-55s" "Starting $vm_name..."
    if virsh start "$vm_name" &>/dev/null; then
        echo -e "${GREEN}✓ Started${NC}"
        return 0
    else
        echo -e "${RED}✗ Failed to start${NC}"
        return 1
    fi
}

shutdown_vm() {
    local vm_name=$1
    printf "  %-55s" "Shutting down $vm_name..."
    virsh shutdown "$vm_name" &>/dev/null
    local attempts=0
    while [ $attempts -lt 15 ] && check_vm_running "$vm_name"; do
        sleep 2
        ((attempts++))
    done
    if check_vm_running "$vm_name"; then
        virsh destroy "$vm_name" &>/dev/null
    fi
    echo -e "${GREEN}✓ Stopped${NC}"
}

destroy_vm() {
    local vm_name=$1
    printf "  %-55s" "Hard reboot (destroy) $vm_name..."
    if check_vm_running "$vm_name"; then
        virsh destroy "$vm_name" &>/dev/null
    fi
    echo -e "${GREEN}✓ Destroyed${NC}"
}

# Wait for VM to be network-responsive
wait_for_vm() {
    local vm_name=$1
    local ip=$2
    printf "  %-55s" "Waiting for $vm_name ($ip)"
    
    local attempts=0
    local max_attempts=30
    while [ $attempts -lt $max_attempts ]; do
        if ping -c 1 -W 1 "$ip" &>/dev/null; then
            printf "\r  %-55s" "Waiting for $vm_name ($ip)"
            echo -e "${GREEN}✓ Responsive${NC}"
            ((PASS++))
            return 0
        fi
        ((attempts++))
        sleep 2
    done
    
    printf "\r  %-55s" "Waiting for $vm_name ($ip)"
    echo -e "${RED}✗ Timeout (60s)${NC}"
    ((FAIL++))
    return 1
}

# ============================================
#   CHECKS START
# ============================================

# ============================================
#   VM POWER MANAGEMENT
# ============================================

section "⚡ VIRTUAL MACHINE POWER STATE"

# Handle forced reboot (hard reset)
if [ "$FORCE_REBOOT" = true ]; then
    echo -e "${RED}⚠  Force REBOOT requested (--reboot flag) - Destroying all VMs${NC}\n"
    for vm_name in "${!VMS[@]}"; do
        if check_vm_running "$vm_name"; then
            destroy_vm "$vm_name"
        fi
    done
    echo -e "\n${CYAN}⏳ Waiting 5 seconds before restart...${NC}"
    sleep 5
    FORCE_RESTART=true
fi

# Handle forced restart (graceful)
if [ "$FORCE_RESTART" = true ]; then
    if [ "$FORCE_REBOOT" = false ]; then
        echo -e "${YELLOW}⚠  Force restart requested (--restart flag)${NC}\n"
    fi
    for vm_name in "${!VMS[@]}"; do
        if check_vm_running "$vm_name"; then
            if [ "$FORCE_REBOOT" = false ]; then
                shutdown_vm "$vm_name"
            fi
        fi
    done
    if [ "$FORCE_REBOOT" = false ]; then
        echo -e "\n${CYAN}⏳ Waiting 5 seconds before restart...${NC}"
        sleep 5
    fi
fi

# Check and start VMs if needed
STARTUP_NEEDED=false
for vm_name in "${!VMS[@]}"; do
    if ! check_vm_running "$vm_name"; then
        echo -e "  ${YELLOW}⚠  $vm_name is not running${NC}"
        if start_vm "$vm_name"; then
            STARTUP_NEEDED=true
        else
            echo -e "  ${RED}✗ Critical: Unable to start $vm_name${NC}"
            ((FAIL++))
        fi
    else
        echo -e "  ${GREEN}✓${NC} $vm_name already running"
    fi
done

# Wait for VMs to boot and services to initialize
if [ "$STARTUP_NEEDED" = true ] || [ "$FORCE_RESTART" = true ]; then
    echo -e "\n${YELLOW}⏳ Waiting for VMs to boot and services to initialize...${NC}"
    
    for i in {1..6}; do
        printf "  ${CYAN}[⏳]${NC} Boot sequence in progress... %d0/60 seconds\r" $i
        sleep 10
    done
    echo -e "  ${GREEN}✓${NC} 60 second boot delay complete                           "
    
    echo -e "\n${CYAN}🔍 Verifying network connectivity...${NC}"
    for vm_name in "${!VMS[@]}"; do
        vm_ip="${VMS[$vm_name]}"
        wait_for_vm "$vm_name" "$vm_ip"
    done
    
    echo -e "\n${CYAN}⏳ Waiting for k3s cluster to initialize...${NC}"
    for i in {1..3}; do
        printf "  ${CYAN}[⏳]${NC} Cluster initialization... %d0/30 seconds\r" $i
        sleep 10
    done
    echo -e "  ${GREEN}✓${NC} Cluster initialization time complete           "
    
    # Re-discover workers after restart
    discover_workers
fi

section "🖥️  VIRTUAL MACHINE CONNECTIVITY"
check "ci-runner ($CI_RUNNER_IP)" "ping -c 1 -W 2 $CI_RUNNER_IP"
check "k3s-control ($K3S_CONTROL_IP)" "ping -c 1 -W 2 $K3S_CONTROL_IP"

# Dynamic worker connectivity checks
for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
    worker_ip="${WORKERS[$worker_name]}"
    check "$worker_name ($worker_ip)" "ping -c 1 -W 2 $worker_ip"
done

section "🌐  CORE SERVICES"
check "Trengo App (port 32504)" "curl -sf --max-time 5 http://$K3S_CONTROL_IP:32504"
check "Grafana (port 30080)" "curl -sf --max-time 5 -o /dev/null http://$K3S_CONTROL_IP:30080"
check "GitLab (port 80)" "curl -sf --max-time 5 -o /dev/null http://$CI_RUNNER_IP"
check_warn "K8s Dashboard (port 30443)" "curl -sfk --max-time 5 -o /dev/null https://$K3S_CONTROL_IP:30443"
check_warn "Portainer (port 30777)" "curl -sf --max-time 5 -o /dev/null http://$K3S_CONTROL_IP:30777"

section "☸️   KUBERNETES CLUSTER"
check "K3s API responding" "$K3S_CMD 'sudo k3s kubectl cluster-info' 2>/dev/null | grep -q running"
check "Control plane Ready" "$K3S_CMD 'sudo k3s kubectl get nodes' 2>/dev/null | grep -q 'k3s-control.*Ready'"

# Dynamic worker Ready checks
for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
    check "$worker_name Ready" "$K3S_CMD 'sudo k3s kubectl get nodes' 2>/dev/null | grep -q '$worker_name.*Ready'"
done

check "Trengo pods running" "$K3S_CMD 'sudo k3s kubectl get pods -n default' 2>/dev/null | grep -q 'trengo-search.*Running'"

section "📊  MONITORING STACK"

# Check for stuck monitoring pods
STUCK_PODS=$($K3S_CMD "sudo k3s kubectl get pods -n monitoring --field-selector=status.phase=Terminating -o custom-columns=NAME:.metadata.name --no-headers" 2>/dev/null)

if [ -n "$STUCK_PODS" ]; then
    echo -e "  ${YELLOW}⚠️  Stuck pods detected, cleaning up...${NC}"
    while read -r pod; do
        $K3S_CMD "sudo k3s kubectl delete pod $pod -n monitoring --grace-period=30 --force" 2>/dev/null
    done <<< "$STUCK_PODS"
    sleep 10
fi

check "Prometheus running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'prometheus.*Running'"
check "Grafana pod running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'monitoring-grafana.*Running'"
check "Alertmanager running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'alertmanager.*Running'"

section "🔧  WORKER AGENT STATUS"

# Dynamic worker agent checks
for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
    worker_ip="${WORKERS[$worker_name]}"
    WORKER_CMD=$(ssh_cmd "$worker_ip")
    
    printf "  %-55s" "k3s-agent on $worker_name"
    ACTIVE=$($WORKER_CMD "systemctl is-active k3s-agent" 2>/dev/null)
    
    if [ "$ACTIVE" == "active" ] || [ "$ACTIVE" == "activating" ]; then
        echo -e "${GREEN}✓ PASS${NC}"
        ((PASS++))
    else
        echo -e "${YELLOW}⚠ INACTIVE - restarting...${NC}"
        $WORKER_CMD "sudo systemctl start k3s-agent" 2>/dev/null
        sleep 5
        ACTIVE=$($WORKER_CMD "systemctl is-active k3s-agent" 2>/dev/null)
        if [ "$ACTIVE" == "active" ] || [ "$ACTIVE" == "activating" ]; then
            echo -e "  ${GREEN}✓ Agent restarted successfully${NC}"
            ((PASS++))
        else
            echo -e "  ${RED}✗ Agent failed to start${NC}"
            ((FAIL++))
        fi
    fi
done

section "🔄  AUTO-FIX: POD HEALTH"

STUCK_APP_PODS=$($K3S_CMD "sudo k3s kubectl get pods -n default --no-headers 2>/dev/null | grep -v Running | awk '{print \$1}'" 2>/dev/null)

if [ -n "$STUCK_APP_PODS" ]; then
    echo -e "  ${YELLOW}⚠️  Found stuck pods, attempting rollback...${NC}"

    CURRENT_IMAGE=$($K3S_CMD "sudo k3s kubectl get deployment trengo-search -n default -o jsonpath='{.spec.template.spec.containers[0].image}'" 2>/dev/null)
    echo -e "  ${DIM}Current image: ${CURRENT_IMAGE}${NC}"
    
    CURRENT_TAG=$(echo "$CURRENT_IMAGE" | sed 's/.*://')
    echo -e "  ${DIM}Current tag: ${CURRENT_TAG}${NC}"

    REVISIONS=$($K3S_CMD "sudo k3s kubectl rollout history deployment/trengo-search -n default 2>/dev/null | grep -c REVISION")

    if [ "$REVISIONS" -gt 1 ]; then
        ROLLBACK=$($K3S_CMD "sudo k3s kubectl rollout undo deployment/trengo-search -n default" 2>&1)
        echo -e "  ${DIM}${ROLLBACK}${NC}"
        sleep 15

        NEW_IMAGE=$($K3S_CMD "sudo k3s kubectl get deployment trengo-search -n default -o jsonpath='{.spec.template.spec.containers[0].image}'" 2>/dev/null)
        echo -e "  ${DIM}Rolled back to: ${NEW_IMAGE}${NC}"

        STILL_STUCK=$($K3S_CMD "sudo k3s kubectl get pods -n default --no-headers 2>/dev/null | grep -v Running | wc -l" 2>/dev/null)

        if [ "$STILL_STUCK" -eq 0 ] || [ -z "$STILL_STUCK" ]; then
            echo -e "  ${GREEN}✓ Pods recovered via rollback${NC}"
            ((PASS++))
        else
            echo -e "  ${RED}✗ Rollback failed - manual intervention needed${NC}"
            ((FAIL++))
        fi
    else
        echo -e "  ${YELLOW}No rollback history - trying pod recreation...${NC}"
        
        STUCK_POD_NAMES=$($K3S_CMD "sudo k3s kubectl get pods -n default --no-headers 2>/dev/null | grep -v Running | awk '{print \$1}'" 2>/dev/null)
        
        if [ -n "$STUCK_POD_NAMES" ]; then
            echo -e "  ${DIM}Force-deleting stuck pods...${NC}"
            while IFS= read -r pod; do
                if [ -n "$pod" ]; then
                    $K3S_CMD "sudo k3s kubectl delete pod $pod -n default --force --grace-period=0" 2>/dev/null
                    echo -e "    ${DIM}Deleted: $pod${NC}"
                fi
            done <<< "$STUCK_POD_NAMES"
            
            echo -e "  ${DIM}Waiting for pods to be recreated...${NC}"
            sleep 20
            
            STILL_STUCK=$($K3S_CMD "sudo k3s kubectl get pods -n default --no-headers 2>/dev/null | grep -v Running | wc -l" 2>/dev/null)
            
            if [ "$STILL_STUCK" -eq 0 ] || [ -z "$STILL_STUCK" ]; then
                echo -e "  ${GREEN}✓ Pods recovered after recreation${NC}"
                ((PASS++))
            else
                echo -e "  ${RED}✗ Pods still failing after recreation${NC}"
                echo -e "  ${YELLOW}  Hint: Check pod events with: kubectl describe pod <n>${NC}"
                ((FAIL++))
            fi
        else
            echo -e "  ${RED}✗ Could not identify stuck pods${NC}"
            ((FAIL++))
        fi
    fi
else
    echo -e "  ${GREEN}✓ All pods healthy${NC}"
    ((PASS++))
fi

section "🔄  AUTO-FIX: IMAGE SYNC"

LATEST_IMAGE=$($K3S_CMD "sudo k3s ctr images list 2>/dev/null | grep trengo-search | head -1 | awk '{print \$1}'" 2>/dev/null)

if [ -n "$LATEST_IMAGE" ]; then
    # Check first worker only for sync status
    FIRST_WORKER_IP=$(echo "${WORKERS[@]}" | awk '{print $1}')
    if [ -n "$FIRST_WORKER_IP" ]; then
        WORKER_CMD=$(ssh_cmd "$FIRST_WORKER_IP")
        WORKER_HAS=$($WORKER_CMD "sudo k3s ctr images list 2>/dev/null | grep -q '$LATEST_IMAGE' && echo yes || echo no" 2>/dev/null)
        
        if [ "$WORKER_HAS" == "no" ]; then
            echo -e "  ${YELLOW}⚠️  Syncing image to workers...${NC}"
            $K3S_CMD "sudo k3s ctr images export /tmp/trengo-sync.tar $LATEST_IMAGE" 2>/dev/null
            scp -q $SSH_OPTS andy@$K3S_CONTROL_IP:/tmp/trengo-sync.tar /tmp/ 2>/dev/null
            
            for worker_name in "${!WORKERS[@]}"; do
                worker_ip="${WORKERS[$worker_name]}"
                scp -q $SSH_OPTS /tmp/trengo-sync.tar andy@$worker_ip:/tmp/ 2>/dev/null
                ssh $SSH_OPTS andy@$worker_ip "sudo k3s ctr images import /tmp/trengo-sync.tar" 2>/dev/null
                echo -e "  ${GREEN}✓ Image synced to $worker_name${NC}"
            done
            ((PASS++))
        else
            echo -e "  ${GREEN}✓ Images in sync${NC}"
            ((PASS++))
        fi
    else
        echo -e "  ${DIM}No workers to sync${NC}"
    fi
else
    echo -e "  ${DIM}No trengo-search image found${NC}"
fi

section "💾  DISK HEALTH"

DISK_THRESHOLD=75
PRUNE_SCRIPT="${SCRIPT_DIR}/disk_check.sh"

# Check control plane
NODE="k3s-control"
CMD="$K3S_CMD"
DISK_PCT=$($CMD "df / | awk 'NR==2{gsub(/%/,\"\");print \$5}'" 2>/dev/null)
DISK_AVAIL=$($CMD "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null)

if [ -z "$DISK_PCT" ]; then
    printf "  %-45s" "$NODE"
    echo -e "${RED}✗ FAIL${NC} (unreachable)"
    ((FAIL++))
elif [ "$DISK_PCT" -lt "$DISK_THRESHOLD" ]; then
    printf "  %-45s" "$NODE"
    echo -e "${GREEN}✓ PASS${NC}  ${DISK_PCT}% used (${DISK_AVAIL} free)"
    ((PASS++))
else
    printf "  %-45s" "$NODE"
    echo -e "${YELLOW}⚠ ${DISK_PCT}% used - pruning...${NC}"
    if [ -f "$PRUNE_SCRIPT" ]; then
        PRUNE_OUT=$(cat "$PRUNE_SCRIPT" | $CMD "sudo DISK_THRESHOLD=$DISK_THRESHOLD bash -s" 2>&1)
        echo "$PRUNE_OUT" | sed 's/^/    /'
        AFTER_PCT=$($CMD "df / | awk 'NR==2{gsub(/%/,\"\");print \$5}'" 2>/dev/null)
        AFTER_AVAIL=$($CMD "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null)
        if [ "$AFTER_PCT" -lt "$DISK_THRESHOLD" ]; then
            echo -e "    ${GREEN}✓ Recovered: ${DISK_PCT}% → ${AFTER_PCT}% (${AFTER_AVAIL} free)${NC}"
            ((PASS++))
        else
            echo -e "    ${RED}✗ Still at ${AFTER_PCT}% after pruning${NC}"
            ((FAIL++))
        fi
    else
        echo -e "    ${RED}✗ disk_check.sh not found${NC}"
        ((FAIL++))
    fi
fi

# Dynamic worker disk checks
for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
    worker_ip="${WORKERS[$worker_name]}"
    WORKER_CMD=$(ssh_cmd "$worker_ip")
    
    DISK_PCT=$($WORKER_CMD "df / | awk 'NR==2{gsub(/%/,\"\");print \$5}'" 2>/dev/null)
    DISK_AVAIL=$($WORKER_CMD "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null)
    
    if [ -z "$DISK_PCT" ]; then
        printf "  %-45s" "$worker_name"
        echo -e "${RED}✗ FAIL${NC} (unreachable)"
        ((FAIL++))
    elif [ "$DISK_PCT" -lt "$DISK_THRESHOLD" ]; then
        printf "  %-45s" "$worker_name"
        echo -e "${GREEN}✓ PASS${NC}  ${DISK_PCT}% used (${DISK_AVAIL} free)"
        ((PASS++))
    else
        printf "  %-45s" "$worker_name"
        echo -e "${YELLOW}⚠ ${DISK_PCT}% used - pruning...${NC}"
        if [ -f "$PRUNE_SCRIPT" ]; then
            PRUNE_OUT=$(cat "$PRUNE_SCRIPT" | $WORKER_CMD "sudo DISK_THRESHOLD=$DISK_THRESHOLD bash -s" 2>&1)
            echo "$PRUNE_OUT" | sed 's/^/    /'
            AFTER_PCT=$($WORKER_CMD "df / | awk 'NR==2{gsub(/%/,\"\");print \$5}'" 2>/dev/null)
            AFTER_AVAIL=$($WORKER_CMD "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null)
            if [ "$AFTER_PCT" -lt "$DISK_THRESHOLD" ]; then
                echo -e "    ${GREEN}✓ Recovered: ${DISK_PCT}% → ${AFTER_PCT}% (${AFTER_AVAIL} free)${NC}"
                ((PASS++))
            else
                echo -e "    ${RED}✗ Still at ${AFTER_PCT}% after pruning${NC}"
                ((FAIL++))
            fi
        else
            echo -e "    ${RED}✗ disk_check.sh not found${NC}"
            ((FAIL++))
        fi
    fi
done

# Clean up stuck svclb pods
STUCK_SVCLB=$($K3S_CMD "sudo k3s kubectl get pods -n kube-system --no-headers 2>/dev/null | grep 'svclb-trengo.*Pending' | wc -l" 2>/dev/null)
if [ "$STUCK_SVCLB" -gt 0 ]; then
    echo -e "  ${DIM}Cleaning ${STUCK_SVCLB} stuck svclb-trengo pods (port 80 conflict with traefik)...${NC}"
    $K3S_CMD "sudo k3s kubectl delete pods -n kube-system -l app=svclb-trengo-search-service-0af1958e --force --grace-period=0" 2>/dev/null
    echo -e "  ${DIM}Done - trengo accessible via NodePort 32504${NC}"
fi

# ============================================
#   RESULTS SUMMARY
# ============================================

echo -e "\n${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}                           📋 SUMMARY${NC}"
echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

echo -e "  ${GREEN}✓ Passed:${NC}   $PASS"
echo -e "  ${YELLOW}⚠ Warnings:${NC} $WARN"
echo -e "  ${RED}✗ Failed:${NC}   $FAIL"

if [ $FAIL -eq 0 ]; then
    echo -e "\n${GREEN}${BOLD}  ╔═════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}  ║       🎉 ALL SYSTEMS OPERATIONAL 🎉                 ║${NC}"
    echo -e "${GREEN}${BOLD}  ╚═════════════════════════════════════════════════════╝${NC}"
    
    echo -e "\n${BOLD}  🔗 Quick Links:${NC}"
    echo -e "  ────────────────────────────────────────────────────────"
    echo -e "  ${CYAN}📱 Trengo App:${NC}      http://$K3S_CONTROL_IP:32504"
    echo -e "  ${CYAN}📊 Grafana:${NC}         http://$K3S_CONTROL_IP:30080"
    echo -e "  ${CYAN}🦊 GitLab:${NC}          http://$CI_RUNNER_IP"
    echo -e "  ${CYAN}🚀 Pipelines:${NC}       http://$CI_RUNNER_IP/root/trengo-search/-/pipelines"
    echo -e "  ${CYAN}☸️  K8s Dashboard:${NC}   https://$K3S_CONTROL_IP:30443"
    echo -e "  ${CYAN}🐳 Portainer:${NC}       http://$K3S_CONTROL_IP:30777"
    
    echo -e "\n${BOLD}  🔑 K8s Dashboard Token:${NC}"
    echo -e "  ────────────────────────────────────────────────────────"
    DASH_TOKEN=$($K3S_CMD "sudo k3s kubectl create token dashboard-admin -n kubernetes-dashboard --duration=24h 2>/dev/null")
    if [ -n "$DASH_TOKEN" ]; then
        echo -e "  ${DIM}${DASH_TOKEN}${NC}"
        echo -e "  ${DIM}(Valid for 24 hours)${NC}"
    else
        echo -e "  ${YELLOW}Token not available - dashboard may not be installed${NC}"
    fi
    
    echo -e "\n${BOLD}  📊 Grafana Credentials:${NC}"
    echo -e "  ────────────────────────────────────────────────────────"
    echo -e "  ${DIM}Username: admin${NC}"
    GRAFANA_PASS=$($K3S_CMD "sudo k3s kubectl get secret -n monitoring monitoring-grafana -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d")
    if [ -n "$GRAFANA_PASS" ]; then
        echo -e "  ${DIM}Password: ${GRAFANA_PASS}${NC}"
    else
        echo -e "  ${DIM}Password: prom-operator (default)${NC}"
    fi
else
    echo -e "\n${RED}${BOLD}  ╔═════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}${BOLD}  ║       ⚠️  SOME CHECKS FAILED - REVIEW               ║${NC}"
    echo -e "${RED}${BOLD}  ╚═════════════════════════════════════════════════════╝${NC}"
    echo -e "\n  ${YELLOW}Run the script again after fixing issues.${NC}"
    echo -e "  ${YELLOW}Or use './check-lab.sh --restart' for graceful restart.${NC}"
    echo -e "  ${YELLOW}Or use './check-lab.sh --reboot' for hard reboot (destroy).${NC}"
fi

echo -e "\n${DIM}Completed at: $(date '+%Y-%m-%d %H:%M:%S')${NC}\n"
