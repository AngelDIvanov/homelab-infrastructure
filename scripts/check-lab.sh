#!/usr/bin/env bash
# Read-only health check by default; remediation requires an explicit flag.
# Usage: ./check-lab.sh [--fix|--restart|--reboot]

set -uo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Suppress ANSI when not running in a terminal (CronJob / log capture)
[ ! -t 1 ] && { GREEN=''; RED=''; YELLOW=''; CYAN=''; BLUE=''; BOLD=''; DIM=''; NC=''; }

SECTION="general"

# Structured logfmt output — parseable by Loki with | logfmt
# Usage: slog <level> <check_desc> <status> <node> [msg]
slog() {
    local level="$1" desc="$2" status="$3" node="${4:-homelab}"
    shift 4
    local msg="${*:-}"
    local key
    key=$(printf '%s' "$desc" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/--*/-/g; s/^-//; s/-$//')
    printf 'level=%s check=%s section=%s status=%s node=%s msg="%s"\n' \
        "$level" "$key" "$SECTION" "$status" "$node" "${msg//\"/\'}"
}

PASS=0
FAIL=0
WARN=0

# Static infrastructure (these don't scale)
K3S_CONTROL_IP="192.168.122.218"
CI_RUNNER_IP="192.168.122.220"
K3S_INFRA_IP="192.168.122.230"
GITLAB_IP="$K3S_INFRA_IP"
GITLAB_PORT="8929"

# SSH options
SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no"

# Static VM definitions (control + ci-runner)
declare -A VMS=(
    ["ci-runner"]="$CI_RUNNER_IP"
    ["k3s-control"]="$K3S_CONTROL_IP"
)

# SSH helper for control plane
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
K3S_CMD="ssh $SSH_OPTS labadmin@$K3S_CONTROL_IP"

# Dynamic worker discovery from k3s
declare -A WORKERS=()
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
        if [[ "$name" =~ ^k3s-worker-[0-9]+$ ]] && [ -n "$ip" ]; then
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
    echo "ssh $SSH_OPTS labadmin@$ip"
}

# Command-line flags
AUTO_FIX=false
FORCE_RESTART=false
FORCE_REBOOT=false
case "${1:-}" in
    "") ;;
    --fix) AUTO_FIX=true ;;
    --restart) AUTO_FIX=true; FORCE_RESTART=true ;;
    --reboot) AUTO_FIX=true; FORCE_RESTART=true; FORCE_REBOOT=true ;;
    -h|--help)
        echo "Usage: $0 [--fix|--restart|--reboot]"
        echo "  no flag    report health without changing the lab"
        echo "  --fix      apply in-place remediations"
        echo "  --restart  gracefully restart VMs, then remediate"
        echo "  --reboot   force-stop VMs, restart them, then remediate"
        exit 0
        ;;
    *)
        echo "Unknown option: $1" >&2
        echo "Usage: $0 [--fix|--restart|--reboot]" >&2
        exit 2
        ;;
esac

# Banner
[ -t 1 ] && clear
echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════════════════╗"
echo "║                                                                       ║"
echo "║             DevOps Home Lab - Health Check                            ║"
echo "║                                                                       ║"
echo "╠═══════════════════════════════════════════════════════════════════════╣"
echo "║  K3s │ GitLab CI/CD │ Prometheus │ Grafana │ Loki │ Security Tools    ║"
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
    while kill -0 "$pid" 2>/dev/null; do
        for (( i=0; i<${#frames}; i++ )); do
            printf "\r  ${CYAN}[${frames:$i:1}]${NC} "
            sleep $delay
        done
    done
    printf "\r      \r"
}

# Section header — also updates SECTION for structured logs
section() {
    SECTION=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/--*/-/g; s/^-//; s/-$//')
    echo -e "\n${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Live check function with proper line clearing
check() {
    local desc="$1"
    local cmd="$2"
    local node="${3:-homelab}"
    printf "  %-55s" "$desc"
    bash -o pipefail -c "$cmd" &>/dev/null &
    local pid=$!
    spinner "$pid"
    wait "$pid"
    local result=$?
    printf "\r  %-55s" "$desc"
    if [ $result -eq 0 ]; then
        echo -e "${GREEN}OK PASS${NC}"
        ((PASS++))
        slog INFO "$desc" PASS "$node" "ok"
        return 0
    else
        echo -e "${RED}FAIL FAIL${NC}"
        ((FAIL++))
        slog ERROR "$desc" FAIL "$node" "check failed"
        return 1
    fi
}

# Warning check (doesn't fail) with proper line clearing
check_warn() {
    local desc="$1"
    local cmd="$2"
    local node="${3:-homelab}"
    printf "  %-55s" "$desc"
    bash -o pipefail -c "$cmd" &>/dev/null &
    local pid=$!
    spinner $pid
    wait $pid
    local result=$?
    printf "\r  %-55s" "$desc"
    if [ $result -eq 0 ]; then
        echo -e "${GREEN}OK PASS${NC}"
        ((PASS++))
        slog INFO "$desc" PASS "$node" "ok"
        return 0
    else
        echo -e "${YELLOW}[WARN] WARN${NC}"
        ((WARN++))
        slog WARN "$desc" WARN "$node" "non-critical failure"
        return 1
    fi
}

# VM helper functions
check_vm_running() {
    local vm_name=$1
    [ "$(virsh domstate "$vm_name" 2>/dev/null)" = "running" ]
}

start_vm() {
    local vm_name=$1
    printf "  %-55s" "Starting $vm_name..."
    if virsh start "$vm_name" &>/dev/null; then
        echo -e "${GREEN}OK Started${NC}"
        return 0
    else
        echo -e "${RED}FAIL Failed to start${NC}"
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
    echo -e "${GREEN}OK Stopped${NC}"
}

destroy_vm() {
    local vm_name=$1
    printf "  %-55s" "Hard reboot (destroy) $vm_name..."
    if check_vm_running "$vm_name"; then
        virsh destroy "$vm_name" &>/dev/null
    fi
    echo -e "${GREEN}OK Destroyed${NC}"
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
            echo -e "${GREEN}OK Responsive${NC}"
            ((PASS++))
            return 0
        fi
        ((attempts++))
        sleep 2
    done
    
    printf "\r  %-55s" "Waiting for $vm_name ($ip)"
    echo -e "${RED}FAIL Timeout (60s)${NC}"
    ((FAIL++))
    return 1
}

# ── VM POWER ─────────────────────────────────

section " VIRTUAL MACHINE POWER STATE"

# Handle forced reboot (hard reset)
if [ "$FORCE_REBOOT" = true ]; then
    echo -e "${RED}[WARN]  Force REBOOT requested (--reboot flag) - Destroying all VMs${NC}\n"
    for vm_name in "${!VMS[@]}"; do
        if check_vm_running "$vm_name"; then
            destroy_vm "$vm_name"
        fi
    done
    echo -e "\n${CYAN} Waiting 5 seconds before restart...${NC}"
    sleep 5
    FORCE_RESTART=true
fi

# Handle forced restart (graceful)
if [ "$FORCE_RESTART" = true ]; then
    if [ "$FORCE_REBOOT" = false ]; then
        echo -e "${YELLOW}[WARN]  Force restart requested (--restart flag)${NC}\n"
    fi
    for vm_name in "${!VMS[@]}"; do
        if check_vm_running "$vm_name"; then
            if [ "$FORCE_REBOOT" = false ]; then
                shutdown_vm "$vm_name"
            fi
        fi
    done
    if [ "$FORCE_REBOOT" = false ]; then
        echo -e "\n${CYAN} Waiting 5 seconds before restart...${NC}"
        sleep 5
    fi
fi

# Check VM state. Starting stopped VMs requires an explicit remediation mode.
STARTUP_NEEDED=false
for vm_name in "${!VMS[@]}"; do
    if ! check_vm_running "$vm_name"; then
        if [ "$AUTO_FIX" = true ]; then
            echo -e "  ${YELLOW}[WARN]  $vm_name is not running; starting it${NC}"
            if start_vm "$vm_name"; then
                STARTUP_NEEDED=true
            else
                echo -e "  ${RED}FAIL Critical: Unable to start $vm_name${NC}"
                ((FAIL++))
            fi
        else
            echo -e "  ${RED}FAIL${NC} $vm_name is not running (use --fix to start it)"
            ((FAIL++))
            slog ERROR "vm-$vm_name-running" FAIL "$vm_name" "stopped; remediation disabled"
        fi
    else
        echo -e "  ${GREEN}OK${NC} $vm_name already running"
    fi
done

# k3s-infra is persistent: start it when explicitly fixing, but never include it
# in restart, reboot, or shutdown loops.
if ! check_vm_running k3s-infra; then
    if [ "$AUTO_FIX" = true ]; then
        echo -e "  ${YELLOW}[WARN]  k3s-infra is not running; starting it${NC}"
        if start_vm k3s-infra; then
            STARTUP_NEEDED=true
        else
            echo -e "  ${RED}FAIL Critical: Unable to start k3s-infra${NC}"
            ((FAIL++))
        fi
    else
        echo -e "  ${RED}FAIL${NC} k3s-infra is not running (use --fix to start it)"
        ((FAIL++))
        slog ERROR "vm-k3s-infra-running" FAIL k3s-infra "stopped; remediation disabled"
    fi
else
    echo -e "  ${GREEN}OK${NC} k3s-infra already running (persistent)"
fi

# Wait for VMs to boot and services to initialize
if [ "$STARTUP_NEEDED" = true ] || [ "$FORCE_RESTART" = true ]; then
    echo -e "\n${YELLOW} Waiting for VMs to boot and services to initialize...${NC}"
    
    for i in {1..6}; do
        printf "  ${CYAN}[]${NC} Boot sequence in progress... %d0/60 seconds\r" $i
        sleep 10
    done
    echo -e "  ${GREEN}OK${NC} 60 second boot delay complete                           "
    
    echo -e "\n${CYAN} Verifying network connectivity...${NC}"
    for vm_name in "${!VMS[@]}"; do
        vm_ip="${VMS[$vm_name]}"
        wait_for_vm "$vm_name" "$vm_ip"
    done
    
    echo -e "\n${CYAN} Waiting for k3s cluster to initialize...${NC}"
    for i in {1..3}; do
        printf "  ${CYAN}[]${NC} Cluster initialization... %d0/30 seconds\r" $i
        sleep 10
    done
    echo -e "  ${GREEN}OK${NC} Cluster initialization time complete           "
    
    # Re-discover workers after restart
    discover_workers
fi

section "  VIRTUAL MACHINE CONNECTIVITY"
check "ci-runner ($CI_RUNNER_IP)" "ping -c 1 -W 2 $CI_RUNNER_IP" "ci-runner"
check "k3s-control ($K3S_CONTROL_IP)" "ping -c 1 -W 2 $K3S_CONTROL_IP" "k3s-control"
check "k3s-infra ($K3S_INFRA_IP)" "ping -c 1 -W 2 $K3S_INFRA_IP" "k3s-infra"

# Dynamic worker connectivity checks
for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
    worker_ip="${WORKERS[$worker_name]}"
    check "$worker_name ($worker_ip)" "ping -c 1 -W 2 $worker_ip" "$worker_name"
done

section "  CORE SERVICES"
check "Trengo App prod (port 32504)" "curl -sf --max-time 5 http://$K3S_CONTROL_IP:32504"
check_warn "Trengo App staging (port 32505)" "curl -sf --max-time 5 http://$K3S_CONTROL_IP:32505"
check "Grafana (port 30080)" "curl -sf --max-time 5 -o /dev/null http://$K3S_CONTROL_IP:30080"
check "Alertmanager (port 30093)" "curl -sf --max-time 5 -o /dev/null http://$K3S_CONTROL_IP:30093"
check "GitLab (port 8929)" "curl -sf --max-time 5 -o /dev/null http://$GITLAB_IP:$GITLAB_PORT"
check_warn "K8s Dashboard (port 30443)" "curl -sfk --max-time 5 -o /dev/null https://$K3S_CONTROL_IP:30443"
check_warn "Portainer (port 30777)" "curl -sf --max-time 5 -o /dev/null http://$K3S_CONTROL_IP:30777"

section "K8s   KUBERNETES CLUSTER"
K3S_AVAILABLE=false
if check "K3s API responding" "$K3S_CMD 'sudo k3s kubectl cluster-info' 2>/dev/null | grep -q running"; then
    K3S_AVAILABLE=true
fi
check "Control plane Ready" "$K3S_CMD 'sudo k3s kubectl get nodes' 2>/dev/null | grep -q 'k3s-control.*Ready'"

# Dynamic worker Ready checks
for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
    check "$worker_name Ready" "$K3S_CMD 'sudo k3s kubectl get nodes' 2>/dev/null | grep -q '$worker_name.*Ready'"
done

check "Trengo pods running" "$K3S_CMD 'sudo k3s kubectl get pods -n default -l app=trengo-search' 2>/dev/null | grep -q 'Running'"
check_warn "Trengo staging pods running" "$K3S_CMD 'sudo k3s kubectl get pods -n default -l app=trengo-search-staging' 2>/dev/null | grep -q 'Running'"

section "  MONITORING STACK"

# Check for stuck monitoring pods
STUCK_PODS=$($K3S_CMD "sudo k3s kubectl get pods -n monitoring --field-selector=status.phase=Terminating -o custom-columns=NAME:.metadata.name --no-headers" 2>/dev/null)

if [ -n "$STUCK_PODS" ]; then
    if [ "$AUTO_FIX" = true ]; then
        echo -e "  ${YELLOW}[WARN]  Stuck pods detected, cleaning up...${NC}"
        while read -r pod; do
            $K3S_CMD "sudo k3s kubectl delete pod $pod -n monitoring --grace-period=30 --force" 2>/dev/null
        done <<< "$STUCK_PODS"
        sleep 10
    else
        echo -e "  ${YELLOW}[WARN]  Stuck monitoring pods detected (use --fix to remove them)${NC}"
        ((WARN++))
        slog WARN "monitoring-stuck-pods" WARN k3s-control "remediation disabled"
    fi
fi

check "Prometheus running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'prometheus.*Running'"
check "Grafana pod running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'monitoring-grafana.*Running'"
check "Alertmanager running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'alertmanager.*Running'"
check "Loki running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'loki.*Running'"
check_warn "Promtail DaemonSet ready" "$K3S_CMD 'sudo k3s kubectl get daemonset -n monitoring' 2>/dev/null | grep -q 'promtail'"
check_warn "Webhook receiver running" "$K3S_CMD 'sudo k3s kubectl get pods -n monitoring' 2>/dev/null | grep -q 'webhook.*Running'"

section "  WORKER AGENT STATUS"

# Dynamic worker agent checks
for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
    worker_ip="${WORKERS[$worker_name]}"
    WORKER_CMD=$(ssh_cmd "$worker_ip")
    
    printf "  %-55s" "k3s-agent on $worker_name"
    ACTIVE=$($WORKER_CMD "systemctl is-active k3s-agent" 2>/dev/null)
    
    if [ "$ACTIVE" == "active" ] || [ "$ACTIVE" == "activating" ]; then
        echo -e "${GREEN}OK PASS${NC}"
        ((PASS++))
    else
        if [ "$AUTO_FIX" = true ]; then
            echo -e "${YELLOW}[WARN] INACTIVE - restarting...${NC}"
            $WORKER_CMD "sudo systemctl start k3s-agent" 2>/dev/null
            sleep 5
            ACTIVE=$($WORKER_CMD "systemctl is-active k3s-agent" 2>/dev/null)
            if [ "$ACTIVE" == "active" ] || [ "$ACTIVE" == "activating" ]; then
                echo -e "  ${GREEN}OK Agent restarted successfully${NC}"
                ((PASS++))
            else
                echo -e "  ${RED}FAIL Agent failed to start${NC}"
                ((FAIL++))
            fi
        else
            echo -e "${RED}FAIL INACTIVE${NC} (use --fix to restart)"
            ((FAIL++))
            slog ERROR "k3s-agent-$worker_name" FAIL "$worker_name" "inactive; remediation disabled"
        fi
    fi
done

section "  POD HEALTH"

if [ "$K3S_AVAILABLE" = true ]; then
    STUCK_APP_PODS=$($K3S_CMD "sudo k3s kubectl get pods -n default -l app=trengo-search --no-headers 2>/dev/null | grep -v Running | awk '{print \$1}'" 2>/dev/null)
else
    STUCK_APP_PODS="__cluster_unreachable__"
fi

if [ "$STUCK_APP_PODS" = "__cluster_unreachable__" ]; then
    echo -e "  ${YELLOW}[WARN] Pod remediation checks skipped because the API is unavailable${NC}"
    ((WARN++))
elif [ -n "$STUCK_APP_PODS" ]; then
    if [ "$AUTO_FIX" != true ]; then
        echo -e "  ${RED}FAIL Stuck application pods detected${NC} (use --fix to remediate)"
        ((FAIL++))
        slog ERROR "application-pod-health" FAIL k3s-control "stuck pods; remediation disabled"
    else
        echo -e "  ${YELLOW}[WARN]  Found stuck pods, attempting rollback...${NC}"

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

        STILL_STUCK=$($K3S_CMD "sudo k3s kubectl get pods -n default -l app=trengo-search --no-headers 2>/dev/null | grep -v Running | wc -l" 2>/dev/null)

        if [ "$STILL_STUCK" -eq 0 ] || [ -z "$STILL_STUCK" ]; then
            echo -e "  ${GREEN}OK Pods recovered via rollback${NC}"
            ((PASS++))
        else
            echo -e "  ${RED}FAIL Rollback failed - manual intervention needed${NC}"
            ((FAIL++))
        fi
    else
        echo -e "  ${YELLOW}No rollback history - trying pod recreation...${NC}"
        
        STUCK_POD_NAMES=$($K3S_CMD "sudo k3s kubectl get pods -n default -l app=trengo-search --no-headers 2>/dev/null | grep -v Running | awk '{print \$1}'" 2>/dev/null)
        
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
            
            STILL_STUCK=$($K3S_CMD "sudo k3s kubectl get pods -n default -l app=trengo-search --no-headers 2>/dev/null | grep -v Running | wc -l" 2>/dev/null)
            
            if [ "$STILL_STUCK" -eq 0 ] || [ -z "$STILL_STUCK" ]; then
                echo -e "  ${GREEN}OK Pods recovered after recreation${NC}"
                ((PASS++))
            else
                echo -e "  ${RED}FAIL Pods still failing after recreation${NC}"
                echo -e "  ${YELLOW}  Hint: Check pod events with: kubectl describe pod <n>${NC}"
                ((FAIL++))
            fi
        else
            echo -e "  ${GREEN}OK All app pods healthy${NC}"
            ((PASS++))
        fi
    fi
    fi
else
    echo -e "  ${GREEN}OK All pods healthy${NC}"
    ((PASS++))
fi

section "  IMAGE SYNC"

if [ "$K3S_AVAILABLE" = true ]; then
    LATEST_IMAGE=$($K3S_CMD "sudo k3s ctr images list 2>/dev/null | grep trengo-search | head -1 | awk '{print \$1}'" 2>/dev/null)
else
    LATEST_IMAGE="__cluster_unreachable__"
fi

if [ "$LATEST_IMAGE" = "__cluster_unreachable__" ]; then
    echo -e "  ${YELLOW}[WARN] Image sync check skipped because the API is unavailable${NC}"
    ((WARN++))
elif [ -n "$LATEST_IMAGE" ]; then
    # Check every worker and record which ones lack the image
    NEEDS_SYNC=()
    if [ "${#WORKERS[@]}" -gt 0 ]; then
        for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
            worker_ip="${WORKERS[$worker_name]}"
            WORKER_CMD=$(ssh_cmd "$worker_ip")
            WORKER_HAS=$($WORKER_CMD "sudo k3s ctr images list 2>/dev/null | grep -q '$LATEST_IMAGE' && echo yes || echo no" 2>/dev/null)
            if [ "$WORKER_HAS" != "yes" ]; then
                NEEDS_SYNC+=("$worker_name")
            fi
        done
    fi

    if [ "${#NEEDS_SYNC[@]}" -gt 0 ]; then
        if [ "$AUTO_FIX" = true ]; then
            echo -e "  ${YELLOW}[WARN]  Syncing image to workers...${NC}"
            CONTROL_TAR=$(mktemp -t trengo-sync.XXXXXX.tar)
            REMOTE_TAR=$($K3S_CMD "mktemp -t trengo-sync.XXXXXX.tar" 2>/dev/null)
            if [ -z "$REMOTE_TAR" ]; then
                echo -e "  ${RED}FAIL Image sync aborted: control-plane mktemp failed${NC}"
                ((FAIL++))
                slog ERROR "worker-image-sync" FAIL k3s-control "control-plane mktemp returned no path"
                rm -f "$CONTROL_TAR"
            elif ! $K3S_CMD "sudo k3s ctr images export '$REMOTE_TAR' '$LATEST_IMAGE'" 2>/dev/null; then
                echo -e "  ${RED}FAIL Image sync aborted: ctr images export failed on k3s-control${NC}"
                ((FAIL++))
                slog ERROR "worker-image-sync" FAIL k3s-control "ctr images export of $LATEST_IMAGE failed"
                $K3S_CMD "rm -f '$REMOTE_TAR'" 2>/dev/null
                rm -f "$CONTROL_TAR"
            elif ! scp -q $SSH_OPTS "labadmin@$K3S_CONTROL_IP:$REMOTE_TAR" "$CONTROL_TAR" 2>/dev/null; then
                echo -e "  ${RED}FAIL Image sync aborted: copy of exported image from k3s-control failed${NC}"
                ((FAIL++))
                slog ERROR "worker-image-sync" FAIL k3s-control "scp of exported image tarball failed"
                $K3S_CMD "rm -f '$REMOTE_TAR'" 2>/dev/null
                rm -f "$CONTROL_TAR"
            else
                sync_failed=false
                for worker_name in $(echo "${!WORKERS[@]}" | tr ' ' '\n' | sort); do
                    # Skip workers that already have the image
                    if [[ " ${NEEDS_SYNC[*]} " != *" $worker_name "* ]]; then
                        continue
                    fi
                    worker_ip="${WORKERS[$worker_name]}"
                    WORKER_TAR=$(ssh $SSH_OPTS labadmin@$worker_ip "mktemp -t trengo-sync.XXXXXX.tar" 2>/dev/null)
                    if [ -z "$WORKER_TAR" ]; then
                        echo -e "  ${RED}FAIL Image sync to $worker_name failed: remote mktemp returned no path${NC}"
                        ((FAIL++))
                        slog ERROR "worker-image-sync" FAIL "$worker_name" "mktemp returned no path"
                        continue
                    fi
                    if scp -q $SSH_OPTS "$CONTROL_TAR" "labadmin@$worker_ip:'$WORKER_TAR'" 2>/dev/null \
                       && ssh $SSH_OPTS labadmin@$worker_ip "sudo k3s ctr images import '$WORKER_TAR'; rc=\$?; rm -f '$WORKER_TAR'; exit \$rc" 2>/dev/null; then
                        echo -e "  ${GREEN}OK Image synced to $worker_name${NC}"
                    else
                        echo -e "  ${RED}FAIL Image sync to $worker_name failed${NC}"
                        sync_failed=true
                    fi
                done
                rm -f "$CONTROL_TAR"
                $K3S_CMD "rm -f '$REMOTE_TAR'" 2>/dev/null
                if [ "$sync_failed" = true ]; then
                    echo -e "  ${RED}FAIL Image sync to one or more workers failed${NC}"
                    ((FAIL++))
                    slog ERROR "worker-image-sync" FAIL k3s-control "one or more worker imports failed"
                else
                    ((PASS++))
                fi
            fi
        else
            echo -e "  ${YELLOW}[WARN] Images are not synchronized on: ${NEEDS_SYNC[*]}${NC} (use --fix to sync)"
            ((WARN++))
            slog WARN "worker-image-sync" WARN k3s-control "out of sync on ${#NEEDS_SYNC[@]} worker(s); remediation disabled"
        fi
    elif [ "${#WORKERS[@]}" -eq 0 ]; then
        echo -e "  ${DIM}No workers to sync${NC}"
    else
        echo -e "  ${GREEN}OK Images in sync${NC}"
        ((PASS++))
    fi
else
    echo -e "  ${DIM}No trengo-search image found${NC}"
fi

section "  DISK HEALTH"

DISK_THRESHOLD=75
PRUNE_SCRIPT="${SCRIPT_DIR}/disk_check.sh"

# Check control plane
NODE="k3s-control"
CMD="$K3S_CMD"
DISK_PCT=$($CMD "df / | awk 'NR==2{gsub(/%/,\"\");print \$5}'" 2>/dev/null)
DISK_AVAIL=$($CMD "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null)

if [ -z "$DISK_PCT" ]; then
    printf "  %-45s" "$NODE"
    echo -e "${RED}FAIL FAIL${NC} (unreachable)"
    ((FAIL++))
    slog ERROR "disk-$NODE" FAIL "$NODE" "unreachable"
elif [ "$DISK_PCT" -lt "$DISK_THRESHOLD" ]; then
    printf "  %-45s" "$NODE"
    echo -e "${GREEN}OK PASS${NC}  ${DISK_PCT}% used (${DISK_AVAIL} free)"
    ((PASS++))
    slog INFO "disk-$NODE" PASS "$NODE" "${DISK_PCT}% used ${DISK_AVAIL} free"
else
    printf "  %-45s" "$NODE"
    if [ "$AUTO_FIX" != true ]; then
        echo -e "${YELLOW}[WARN] ${DISK_PCT}% used${NC} (use --fix to prune)"
        ((WARN++))
        slog WARN "disk-$NODE" WARN "$NODE" "${DISK_PCT}% used; remediation disabled"
    elif [ -f "$PRUNE_SCRIPT" ]; then
        echo -e "${YELLOW}[WARN] ${DISK_PCT}% used - pruning...${NC}"
        PRUNE_OUT=$(cat "$PRUNE_SCRIPT" | $CMD "sudo DISK_THRESHOLD=$DISK_THRESHOLD bash -s" 2>&1)
        echo "$PRUNE_OUT" | sed 's/^/    /'
        AFTER_PCT=$($CMD "df / | awk 'NR==2{gsub(/%/,\"\");print \$5}'" 2>/dev/null)
        AFTER_AVAIL=$($CMD "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null)
        if [ "$AFTER_PCT" -lt "$DISK_THRESHOLD" ]; then
            echo -e "    ${GREEN}OK Recovered: ${DISK_PCT}% → ${AFTER_PCT}% (${AFTER_AVAIL} free)${NC}"
            ((PASS++))
            slog INFO "disk-$NODE" PASS "$NODE" "pruned ${DISK_PCT}% to ${AFTER_PCT}% ${AFTER_AVAIL} free"
        else
            echo -e "    ${RED}FAIL Still at ${AFTER_PCT}% after pruning${NC}"
            ((FAIL++))
            slog ERROR "disk-$NODE" FAIL "$NODE" "still at ${AFTER_PCT}% after pruning"
        fi
    else
        echo -e "    ${RED}FAIL disk_check.sh not found${NC}"
        ((FAIL++))
        slog ERROR "disk-$NODE" FAIL "$NODE" "disk_check.sh not found"
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
        echo -e "${RED}FAIL FAIL${NC} (unreachable)"
        ((FAIL++))
    elif [ "$DISK_PCT" -lt "$DISK_THRESHOLD" ]; then
        printf "  %-45s" "$worker_name"
        echo -e "${GREEN}OK PASS${NC}  ${DISK_PCT}% used (${DISK_AVAIL} free)"
        ((PASS++))
    else
        printf "  %-45s" "$worker_name"
        if [ "$AUTO_FIX" != true ]; then
            echo -e "${YELLOW}[WARN] ${DISK_PCT}% used${NC} (use --fix to prune)"
            ((WARN++))
            slog WARN "disk-$worker_name" WARN "$worker_name" "${DISK_PCT}% used; remediation disabled"
        elif [ -f "$PRUNE_SCRIPT" ]; then
            echo -e "${YELLOW}[WARN] ${DISK_PCT}% used - pruning...${NC}"
            PRUNE_OUT=$(cat "$PRUNE_SCRIPT" | $WORKER_CMD "sudo DISK_THRESHOLD=$DISK_THRESHOLD bash -s" 2>&1)
            echo "$PRUNE_OUT" | sed 's/^/    /'
            AFTER_PCT=$($WORKER_CMD "df / | awk 'NR==2{gsub(/%/,\"\");print \$5}'" 2>/dev/null)
            AFTER_AVAIL=$($WORKER_CMD "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null)
            if [ "$AFTER_PCT" -lt "$DISK_THRESHOLD" ]; then
                echo -e "    ${GREEN}OK Recovered: ${DISK_PCT}% → ${AFTER_PCT}% (${AFTER_AVAIL} free)${NC}"
                ((PASS++))
            else
                echo -e "    ${RED}FAIL Still at ${AFTER_PCT}% after pruning${NC}"
                ((FAIL++))
            fi
        else
            echo -e "    ${RED}FAIL disk_check.sh not found${NC}"
            ((FAIL++))
        fi
    fi
done

# Clean up stuck svclb pods
STUCK_SVCLB=$($K3S_CMD "sudo k3s kubectl get pods -n kube-system --no-headers 2>/dev/null | grep 'svclb-trengo.*Pending' | wc -l" 2>/dev/null)
if [ "$STUCK_SVCLB" -gt 0 ]; then
    if [ "$AUTO_FIX" = true ]; then
        echo -e "  ${DIM}Cleaning ${STUCK_SVCLB} stuck svclb-trengo pods (port 80 conflict with traefik)...${NC}"
        $K3S_CMD "sudo k3s kubectl delete pods -n kube-system -l app=svclb-trengo-search-service-0af1958e --force --grace-period=0" 2>/dev/null
        echo -e "  ${DIM}Done - trengo accessible via NodePort 32504${NC}"
    else
        echo -e "  ${YELLOW}[WARN] ${STUCK_SVCLB} stuck svclb pods detected${NC} (use --fix to remove)"
        ((WARN++))
        slog WARN "stuck-svclb-pods" WARN k3s-control "remediation disabled"
    fi
fi

section "  SECURITY TOOLS"

printf "  %-55s" "Trivy installed on ci-runner"
TRIVY_CHECK=$(ssh $SSH_OPTS labadmin@$CI_RUNNER_IP "sudo test -f /home/gitlab-runner/bin/trivy && echo yes || echo no" 2>/dev/null)
if [ "$TRIVY_CHECK" == "yes" ]; then
    TRIVY_VER=$(ssh $SSH_OPTS labadmin@$CI_RUNNER_IP "sudo /home/gitlab-runner/bin/trivy --version 2>/dev/null | head -1" 2>/dev/null)
    echo -e "${GREEN}OK PASS${NC}  ${DIM}${TRIVY_VER}${NC}"
    ((PASS++))
else
    echo -e "${YELLOW}[WARN] WARN${NC}  ${DIM}Not installed -- will install on next pipeline run${NC}"
    ((WARN++))
fi

printf "  %-55s" "Gitleaks installed on ci-runner"
GITLEAKS_CHECK=$(ssh $SSH_OPTS labadmin@$CI_RUNNER_IP "sudo test -f /home/gitlab-runner/bin/gitleaks && echo yes || echo no" 2>/dev/null)
if [ "$GITLEAKS_CHECK" == "yes" ]; then
    GITLEAKS_VER=$(ssh $SSH_OPTS labadmin@$CI_RUNNER_IP "sudo /home/gitlab-runner/bin/gitleaks version 2>/dev/null" 2>/dev/null)
    echo -e "${GREEN}OK PASS${NC}  ${DIM}${GITLEAKS_VER}${NC}"
    ((PASS++))
else
    echo -e "${YELLOW}[WARN] WARN${NC}  ${DIM}Not installed -- will install on next pipeline run${NC}"
    ((WARN++))
fi

section "  GITLAB PIPELINE STATUS"

LAST_PIPELINE=$(curl -sf --max-time 5 \
    "http://$GITLAB_IP:$GITLAB_PORT/api/v4/projects/root%2Ftrengo-search/pipelines?per_page=1" \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN:-}" 2>/dev/null)

if [ -n "$LAST_PIPELINE" ] && echo "$LAST_PIPELINE" | grep -q '"status"'; then
    STATUS=$(echo "$LAST_PIPELINE" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)
    REF=$(echo "$LAST_PIPELINE" | grep -o '"ref":"[^"]*"' | head -1 | cut -d'"' -f4)
    PIPELINE_ID=$(echo "$LAST_PIPELINE" | grep -o '"id":[0-9]*' | head -1 | cut -d':' -f2)
    printf "  %-55s" "Last pipeline (#${PIPELINE_ID} on ${REF})"
    if [ "$STATUS" == "success" ]; then
        echo -e "${GREEN}OK ${STATUS}${NC}"
        ((PASS++))
    elif [ "$STATUS" == "running" ] || [ "$STATUS" == "pending" ]; then
        echo -e "${YELLOW}[WARN] ${STATUS}${NC}"
        ((WARN++))
    else
        echo -e "${RED}FAIL ${STATUS}${NC}"
        ((FAIL++))
    fi
else
    printf "  %-55s" "Last pipeline status"
    echo -e "${YELLOW}[WARN] WARN${NC}  ${DIM}Could not fetch -- set GITLAB_TOKEN env var for pipeline checks${NC}"
    ((WARN++))
fi

# ── SUMMARY ──────────────────────────────────

echo -e "\n${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}                            SUMMARY${NC}"
echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

echo -e "  ${GREEN}OK Passed:${NC}   $PASS"
echo -e "  ${YELLOW}[WARN] Warnings:${NC} $WARN"
echo -e "  ${RED}FAIL Failed:${NC}   $FAIL"

if [ $FAIL -eq 0 ]; then
    echo -e "\n${GREEN}${BOLD}    ╔═════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}    ║        ALL SYSTEMS OPERATIONAL                      ║${NC}"
    echo -e "${GREEN}${BOLD}    ╚═════════════════════════════════════════════════════╝${NC}"
    
    echo -e "\n${BOLD}   Quick Links:${NC}"
    echo -e "  ────────────────────────────────────────────────────────"
    echo -e "  ${CYAN} Trengo App (prod):${NC}   http://$K3S_CONTROL_IP:32504"
    echo -e "  ${CYAN} Trengo App (staging):${NC} http://$K3S_CONTROL_IP:32505"
    echo -e "  ${CYAN} Grafana:${NC}              http://$K3S_CONTROL_IP:30080"
    echo -e "  ${CYAN} Alertmanager:${NC}         http://$K3S_CONTROL_IP:30093"
    echo -e "  ${CYAN} GitLab:${NC}               http://$GITLAB_IP:$GITLAB_PORT"
    echo -e "  ${CYAN} Pipelines:${NC}            http://$GITLAB_IP:$GITLAB_PORT/root/trengo-search/-/pipelines"
    echo -e "  ${CYAN} Wiki:${NC}                 http://$GITLAB_IP:$GITLAB_PORT/root/trengo-search/-/wikis"
    echo -e "  ${CYAN} K8s Dashboard:${NC}         https://$K3S_CONTROL_IP:30443"
    echo -e "  ${CYAN} Portainer:${NC}             http://$K3S_CONTROL_IP:30777"

    echo -e "\n  ${DIM}Credentials are intentionally never printed by health checks.${NC}"
    echo -e "  ${DIM}Retrieve credentials directly from Vaultwarden when needed.${NC}"
else
    echo -e "\n${RED}${BOLD}╔═══════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}${BOLD}  ║       [WARN]  SOME CHECKS FAILED - REVIEW             ║${NC}"
    echo -e "${RED}${BOLD}  ╚═══════════════════════════════════════════════════════╝${NC}"
    echo -e "\n  ${YELLOW}Run the script again after fixing issues.${NC}"
    echo -e "  ${YELLOW}Or use './check-lab.sh --restart' for graceful restart.${NC}"
    echo -e "  ${YELLOW}Or use './check-lab.sh --reboot' for hard reboot (destroy).${NC}"
fi

echo -e "\n${DIM}Completed at: $(date '+%Y-%m-%d %H:%M:%S')${NC}\n"

SECTION="summary"
slog INFO summary DONE homelab "pass=$PASS fail=$FAIL warn=$WARN total=$((PASS+FAIL+WARN))"

[ "$FAIL" -gt 0 ] && exit 1
exit 0
