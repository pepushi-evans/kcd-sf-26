# SPIFFE workload identity for the sandboxed agent

nono ≥ 0.70 can source per-route credentials from a **SPIRE Workload API**
instead of a static secret: the supervisor (outside the sandbox) fetches a
short-lived **JWT-SVID**, injects it into upstream requests, and rotates it in
the background. The agent process never holds the SVID and cannot reach the
Workload API socket — its identity is cryptographic, attested, and expiring,
not an env var.

## Local demo (two terminals)

```bash
./run-local-spire.sh   # throwaway SPIRE server+agent, registers unix:uid:$UID
./demo-svid.sh         # sandbox curls the route; the echo shows the SVID
```

Observed output — the sandboxed process sent only a phantom key, yet the
upstream received a fresh workload identity:

```json
{
  "authorization_header": "Bearer eyJhbGciOiJFUzI1NiIs...",
  "jwt_svid_claims": {
    "aud": ["svid_demo"],
    "exp": 1787864268,
    "sub": "spiffe://kcd-sf-26.demo/capacity-agent"
  }
}
```

The profile fragment (`demo-svid.sh` grafts it onto the shared
`manifests/nono-profile.json`):

```json
"svid_demo": {
  "upstream": "http://127.0.0.1:8099",
  "spiffe": {
    "type": "jwt",
    "workload_api_socket": "/tmp/kcd-spire/agent.sock",
    "audience": ["svid_demo"],
    "inject_header": "Authorization"
  }
}
```

Route names become child env vars (`SVID_DEMO_BASE_URL`,
`SVID_DEMO_API_KEY`) — use underscores, dashes make them unreferenceable in
a shell.

## In-cluster

The same profile block works in the pod once SPIRE runs in the cluster
(`helm install` the `spiffe` charts with the SPIFFE CSI driver):

1. mount the CSI driver's socket (`csi.spiffe.io`) into the agent pod and
   point `workload_api_socket` at it (conventionally
   `/run/spiffe/sockets/agent.sock`),
2. register the agent with pod-identity selectors via a `ClusterSPIFFEID`
   (e.g. `k8s:ns:agents`, `k8s:sa:capacity-agent`) so the SVID's `sub` is
   the pod's ServiceAccount identity,
3. point the route's `upstream` at any HTTPS service that validates
   JWT-SVIDs against the trust bundle.

nono validates the socket at startup and rotates SVIDs without restarts.
`spiffe` is mutually exclusive with `credential_key` on a route — identity
comes from attestation, not from a stored secret.
