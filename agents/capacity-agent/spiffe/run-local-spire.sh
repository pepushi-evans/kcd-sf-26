#!/usr/bin/env bash
# Stand up a throwaway local SPIRE (server + agent) and register the current
# user, so nono's SPIFFE credential route (v0.70+) can fetch JWT-SVIDs from
# the Workload API. Everything lives under ./.work/spire and dies with the
# script (ctrl-c) — this is a demo rig, not an installation.
#
# Usage:  ./run-local-spire.sh          # sets up and stays running
# Then in another terminal:  ./demo-svid.sh
set -euo pipefail
cd "$(dirname "$0")"

SPIRE_VERSION=1.15.3
ARCH=$(uname -m); case "$ARCH" in aarch64|arm64) ARCH=arm64;; x86_64) ARCH=amd64;; esac
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
WORK=$PWD/.work/spire
BIN=$WORK/spire-${SPIRE_VERSION}/bin
TD=kcd-sf-26.demo
# unix socket paths have a ~104-char limit; keep them short and stable
SOCK_DIR=/tmp/kcd-spire
mkdir -p "$SOCK_DIR"

mkdir -p "$WORK"
if [ ! -x "$BIN/spire-server" ]; then
  echo ">> fetching spire ${SPIRE_VERSION}"
  curl -fsSL -o "$WORK/spire.tgz" \
    "https://github.com/spiffe/spire/releases/download/v${SPIRE_VERSION}/spire-${SPIRE_VERSION}-${OS}-${ARCH}-musl.tar.gz"
  tar xzf "$WORK/spire.tgz" -C "$WORK"
fi

cat > "$WORK/server.conf" <<EOF
server {
  bind_address = "127.0.0.1"
  bind_port = "8581"
  trust_domain = "$TD"
  data_dir = "$WORK/server-data"
  ca_ttl = "24h"
  default_x509_svid_ttl = "1h"
  default_jwt_svid_ttl = "5m"
}
plugins {
  DataStore "sql" { plugin_data {
    database_type = "sqlite3"
    connection_string = "$WORK/server-data/datastore.sqlite3"
  } }
  KeyManager "memory" { plugin_data {} }
  NodeAttestor "join_token" { plugin_data {} }
}
EOF

cat > "$WORK/agent.conf" <<EOF
agent {
  data_dir = "$WORK/agent-data"
  server_address = "127.0.0.1"
  server_port = "8581"
  trust_domain = "$TD"
  insecure_bootstrap = true  # demo rig; production uses a real trust bundle
}
plugins {
  KeyManager "memory" { plugin_data {} }
  NodeAttestor "join_token" { plugin_data {} }
  WorkloadAttestor "unix" { plugin_data {} }
}
EOF

echo ">> starting spire-server"
"$BIN/spire-server" run -config "$WORK/server.conf" -socketPath "$SOCK_DIR/server.sock" &
SERVER_PID=$!
trap 'kill $SERVER_PID ${AGENT_PID:-} 2>/dev/null || true' EXIT
for i in $(seq 1 30); do
  "$BIN/spire-server" healthcheck -socketPath "$SOCK_DIR/server.sock" >/dev/null 2>&1 && break
  sleep 1
done

TOKEN=$("$BIN/spire-server" token generate -socketPath "$SOCK_DIR/server.sock" \
  -spiffeID "spiffe://$TD/local-node" | awk '{print $2}')

echo ">> starting spire-agent"
"$BIN/spire-agent" run -config "$WORK/agent.conf" -socketPath "$SOCK_DIR/agent.sock" -joinToken "$TOKEN" &
AGENT_PID=$!
for i in $(seq 1 30); do
  "$BIN/spire-agent" healthcheck -socketPath "$SOCK_DIR/agent.sock" >/dev/null 2>&1 && break
  sleep 1
done

echo ">> registering this user (unix:uid:$(id -u)) as spiffe://$TD/capacity-agent"
"$BIN/spire-server" entry create -socketPath "$SOCK_DIR/server.sock" \
  -parentID "spiffe://$TD/local-node" \
  -spiffeID "spiffe://$TD/capacity-agent" \
  -selector "unix:uid:$(id -u)"

echo
echo "SPIRE is up. Workload API socket: $SOCK_DIR/agent.sock"
echo "Run ./demo-svid.sh in another terminal. Ctrl-C here to tear down."
wait
