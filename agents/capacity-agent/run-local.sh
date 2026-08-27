#!/usr/bin/env bash
# Run the capacity agent on a developer machine (Linux or macOS), inside the
# SAME nono sandbox profile the in-cluster Deployment uses.
#
# The agent never reads your admin kubeconfig: this script mints a
# short-lived token for the read-only `capacity-agent` ServiceAccount and
# writes a dedicated kubeconfig into ./.work/, which is the only credential
# the sandbox can see. GITHUB_TOKEN / GOOGLE_API_KEY are read by the nono
# supervisor and phantom-token swapped at the proxy — the agent process
# never holds them.
#
# Usage:
#   GITHUB_TOKEN=github_pat_... GOOGLE_API_KEY=... ./run-local.sh [--once]
#   (GITHUB_TOKEN falls back to `gh auth token`; without GOOGLE_API_KEY the
#    agent runs in heuristic mode. DRY_RUN=1 to log the PR instead of
#    opening it.)
set -euo pipefail
cd "$(dirname "$0")"

# A polluted PYTHONPATH (flox/nix dev shells) makes pip skip "already
# satisfied" deps that then don't exist inside the sandbox — the venv must
# be self-contained.
unset PYTHONPATH

: "${GITHUB_TOKEN:=$(gh auth token)}"
export GITHUB_TOKEN

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

mkdir -p .work
SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA_DATA=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
SA_TOKEN=$(kubectl -n agents create token capacity-agent --duration=2h)
cat > .work/kubeconfig <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: demo
    cluster:
      server: ${SERVER}
      certificate-authority-data: ${CA_DATA}
users:
  - name: capacity-agent
    user:
      token: ${SA_TOKEN}
contexts:
  - name: demo
    context: {cluster: demo, user: capacity-agent}
current-context: demo
EOF

export KUBECONFIG=$PWD/.work/kubeconfig
exec nono run --allow-cwd --profile manifests/nono-profile.json -- \
  .venv/bin/python app/main.py "$@"
