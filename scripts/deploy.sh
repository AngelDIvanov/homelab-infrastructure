#!/bin/bash
# Build the trengo-search image, push it to the in-cluster registry and roll
# the deployment. Needs docker and a kubeconfig for the kubeadm cluster.
set -euo pipefail

REGISTRY="${REGISTRY:-192.168.122.240:30500}"   # NodePort on the control node
VERSION="${1:-v1}"
IMAGE="$REGISTRY/trengo-search:$VERSION"

echo "================================================"
echo "Deploying Trengo Search Tool ($IMAGE)"
echo "================================================"

echo ""
echo "Step 1: Building Docker image..."
docker build -t "$IMAGE" .

echo ""
echo "Step 2: Testing image locally..."
docker run -d -p 8080:80 --name trengo-test "$IMAGE"
sleep 3
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080)
docker stop trengo-test > /dev/null 2>&1
docker rm trengo-test > /dev/null 2>&1

if [ "$HTTP_CODE" != "200" ]; then
    echo "FAIL: Local test failed (HTTP $HTTP_CODE)"
    exit 1
fi
echo "OK: Local test passed"

echo ""
echo "Step 3: Pushing to the local registry..."
docker push "$IMAGE"

echo ""
echo "Step 4: Deploying to Kubernetes..."
kubectl apply -f kubernetes/deployments/trengo-search.yaml
kubectl set image deployment/trengo-search "trengo-search=$IMAGE"

echo ""
echo "Step 5: Waiting for the rollout..."
kubectl rollout status deployment/trengo-search --timeout=120s

echo ""
echo "Step 6: Checking deployment status..."
kubectl get deployments
kubectl get pods
kubectl get services

echo ""
echo "================================================"
echo "Deployment complete!"
echo "================================================"
echo ""
echo "Access the application:"
NODE_PORT=$(kubectl get svc trengo-search-service -o jsonpath='{.spec.ports[0].nodePort}')
echo "  NodePort: http://${REGISTRY%%:*}:${NODE_PORT}"
echo "  Port-forward: kubectl port-forward svc/trengo-search-service 8080:80"
echo ""
