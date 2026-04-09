#!/bin/bash
set -e

K3S_HOST="andy@192.168.122.218"
VERSION="${1:-v1}"

echo "================================================"
echo "Deploying Trengo Search Tool to K3s"
echo "================================================"

echo ""
echo "Step 1: Building Docker image..."
docker build -t trengo-search:${VERSION} .

echo ""
echo "Step 2: Testing image locally..."
docker run -d -p 8080:80 --name trengo-test trengo-search:${VERSION}
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
echo "Step 3: Exporting image..."
docker save trengo-search:${VERSION} -o /tmp/trengo-search-${VERSION}.tar

echo ""
echo "Step 4: Copying to K3s control node..."
scp /tmp/trengo-search-${VERSION}.tar ${K3S_HOST}:/tmp/

echo ""
echo "Step 5: Importing to K3s..."
ssh ${K3S_HOST} "sudo k3s ctr images import /tmp/trengo-search-${VERSION}.tar"

echo ""
echo "Step 6: Deploying to Kubernetes..."
kubectl apply -f k8s/deployment.yaml

echo ""
echo "Step 7: Waiting for pods to be ready..."
kubectl wait --for=condition=ready pod -l app=trengo-search --timeout=60s

echo ""
echo "Step 8: Checking deployment status..."
kubectl get deployments
kubectl get pods
kubectl get services

echo ""
echo "================================================"
echo "Deployment complete!"
echo "================================================"
echo ""
echo "Access the application:"
SERVICE_IP=$(kubectl get svc trengo-search-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "  Direct: http://${SERVICE_IP}"
echo "  Port-forward: kubectl port-forward svc/trengo-search-service 8080:80"
echo ""
