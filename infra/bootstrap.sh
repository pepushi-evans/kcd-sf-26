#!/usr/bin/env bash
# Bootstrap the KCD SF 2026 demo cluster from zero.
#
# Requires: kind, docker, kubectl, helm. Idempotent: safe to re-run.
#
# Env:
#   GITHUB_TOKEN     fine-grained PAT for the AGENT GitHub account with
#                    pepushi-evans/kcd-sf-26 access. Read: Contents, Metadata.
#                    Write: Contents, Pull requests, Issues.
#                    (Falls back to `gh auth token` if unset.)
#   GOOGLE_API_KEY   Gemini API key for the capacity agent (AI Studio free
#                    tier works). Optional at bootstrap; the agent Deployment
#                    stays Pending on the secret until it exists.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=kcd-sf-26
CTX=kind-${CLUSTER}

# Multi-node kind on a shared VM commonly exhausts the default inotify
# instance limit (128) and workers fail to join with an unhealthy kubelet.
if [ "$(sysctl -n fs.inotify.max_user_instances)" -lt 512 ]; then
  echo ">> raising fs.inotify.max_user_instances to 512 (needs sudo)"
  sudo sysctl -w fs.inotify.max_user_instances=512
fi

if ! kind get clusters | grep -qx "${CLUSTER}"; then
  kind create cluster --config infra/kind-cluster.yaml --wait 180s
fi
kubectl config use-context "${CTX}"

echo ">> metrics-server (HPA needs it; kind kubelets need --kubelet-insecure-tls)"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' \
  2>/dev/null || true

echo ">> flux-operator (embedded Flux Web UI on 9080; anonymous admin for the local demo)"
# Anonymous web auth: every visitor is user `flux` in group `flux-admin`,
# which flux-ui-admin-rbac.yaml binds to the chart's flux-web-admin role —
# unlocking the UI's GitOps actions and log viewer. Local kind demo ONLY.
helm upgrade --install flux-operator \
  oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --namespace flux-system --create-namespace \
  --set-json 'web.config={"authentication":{"type":"Anonymous","anonymous":{"username":"flux","groups":["flux-admin"]}}}' \
  --wait
kubectl apply -f infra/flux-ui-admin-rbac.yaml

echo ">> flux-operator-mcp (read-only Flux context tools for the agent)"
helm upgrade --install flux-operator-mcp \
  oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator-mcp \
  --namespace flux-system --set readonly=true --wait

echo ">> git auth secret for FluxInstance sync"
TOKEN="${GITHUB_TOKEN:-$(gh auth token)}"
kubectl -n flux-system create secret generic flux-system \
  --from-literal=username=git \
  --from-literal="password=${TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo ">> FluxInstance + Web UI NodePort (localhost:8080)"
kubectl apply -f infra/flux-instance.yaml
kubectl apply -f infra/flux-ui-nodeport.yaml
kubectl apply -f infra/flux-ui-networkpolicy.yaml

echo ">> demo app image"
docker build -t checkout:demo apps/checkout/app
kind load docker-image checkout:demo --name "${CLUSTER}"

echo ">> capacity agent image"
docker build -t capacity-agent:demo agents/capacity-agent
kind load docker-image capacity-agent:demo --name "${CLUSTER}"

echo ">> agent secrets (namespace comes from Git, so create it here too)"
kubectl create namespace agents --dry-run=client -o yaml | kubectl apply -f -
kubectl -n agents create secret generic capacity-agent-github \
  --from-literal="token=${TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -
if [ -n "${GOOGLE_API_KEY:-}" ]; then
  kubectl -n agents create secret generic capacity-agent-model \
    --from-literal="google-api-key=${GOOGLE_API_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "!! GOOGLE_API_KEY unset — create secret agents/capacity-agent-model before the agent can talk to Gemini"
fi

echo ">> waiting for Flux to install and sync clusters/kind"
kubectl -n flux-system wait fluxinstance/flux --for=condition=Ready --timeout=5m || true
echo
echo "Done. Surfaces:"
echo "  Flux Web UI:  http://localhost:8080"
echo "  checkout app: http://localhost:9898/checkout"
echo "  Start the incident: kubectl apply -f loadtest/loadtest-job.yaml"
