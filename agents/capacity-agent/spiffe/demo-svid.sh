#!/usr/bin/env bash
# The SPIFFE demo beat. With run-local-spire.sh running:
#
#   1. generates a SPIFFE-enabled variant of the agent's nono profile whose
#      `svid_demo` route fetches a JWT-SVID from the SPIRE Workload API and
#      injects it as a Bearer header (nono >= 0.70),
#   2. starts a loopback echo upstream,
#   3. curls the route from INSIDE the sandbox.
#
# The echo shows the decoded JWT-SVID claims (sub = spiffe://.../capacity-agent).
# The sandboxed process never touched the Workload API or held the SVID —
# the nono supervisor fetched, injected, and rotates it.
set -euo pipefail
cd "$(dirname "$0")"

SOCK=/tmp/kcd-spire/agent.sock
[ -S "$SOCK" ] || { echo "run ./run-local-spire.sh first (no socket at $SOCK)"; exit 1; }

python3 - "$SOCK" > .work/nono-profile-spiffe.json <<'EOF'
import json, sys
p = json.load(open("../manifests/nono-profile.json"))
p["meta"]["name"] = "capacity-agent-spiffe"
net = p["network"]
net["credentials"] = net.get("credentials", []) + ["svid_demo"]
net["custom_credentials"]["svid_demo"] = {
    "upstream": "http://127.0.0.1:8099",
    "spiffe": {
        "type": "jwt",
        "workload_api_socket": sys.argv[1],
        "audience": ["svid_demo"],
        "inject_header": "Authorization",
    },
}
json.dump(p, sys.stdout, indent=2)
EOF
nono profile validate .work/nono-profile-spiffe.json

python3 echo.py & ECHO_PID=$!
trap 'kill $ECHO_PID 2>/dev/null || true' EXIT
sleep 1

nono run --allow-cwd --profile .work/nono-profile-spiffe.json -- /bin/sh -c '
  echo "=== calling the svid_demo route from inside the sandbox ==="
  /usr/bin/curl -s -H "Authorization: Bearer $SVID_DEMO_API_KEY" "$SVID_DEMO_BASE_URL/"
'
