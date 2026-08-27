# KCD SF 2026 Demo: A Human-Reviewable, Multiplayer Agent via GitOps

Demo manifests for the talk *Making Agent Work Human-Reviewable and Multiplayer
with GitOps*.

The thesis: an agent that can `kubectl patch` can also `kubectl delete`, with
no commit to roll back to. So we run the agent centrally and confine it at three
layers, and we let it change infrastructure ONLY by opening a pull request that
Flux reconciles after a human approves.

## The three layers (and where each file lives)

| Layer | Confines | How | File |
|-------|----------|-----|------|
| Process | the agent binary | `nono` kernel-enforced Landlock sandbox (fs scope + egress allowlist) | `agent-deployment.yaml` (command + ConfigMap profile) |
| Workload | the pod | RuntimeDefault seccomp, non-root, no caps, read-only rootfs, NetworkPolicy egress | `agent-deployment.yaml`, `networkpolicy.yaml` |
| Change | the agent's effect on the cluster | read-only RBAC + Flux MCP read-only; writes leave only as Git PRs | `rbac.yaml` |

`nono` confines the process, the pod confines the workload, **Flux confines the
change**. The sandbox vendors solve the first two; GitOps is what gives you the
third (and the rollback).

## Why it runs under RuntimeDefault (no privileged pod)

Verified against `moby/profiles` `seccomp/default.json` @ `main`:
`landlock_add_rule`, `landlock_create_ruleset`, and `landlock_restrict_self`
are `SCMP_ACT_ALLOW` with **no** capability gate and **no** `minKernel` floor
(lines 198-200). RuntimeDefault is a seccomp profile, so the runtime sets
`no_new_privs`, which is what `landlock_restrict_self` needs in lieu of
CAP_SYS_ADMIN. Result: nono enforces Landlock in a stock pod, no extra privilege.

Node requirements:
- kernel >= 5.13 for Landlock filesystem rules
- kernel >= 6.7 for Landlock ABI v4 (nono's per-process TCP port egress filtering)

If a node's profile is too old, see `seccomp-fallback.yaml`.

## Apply

```bash
kubectl apply -k demo/
```

Pre-reqs you provide:
- an agent image with `nono`, `gh`/`git`, and your agent CLI on PATH (set in `agent-deployment.yaml`)
- a GitHub token Secret scoped to "open PR", NOT push-to-main:
  `kubectl -n agents create secret generic review-agent-git --from-literal=token=ghp_...`
- a model API key Secret:
  `kubectl -n agents create secret generic review-agent-model --from-literal=anthropic-api-key=sk-ant-...`
- a CNI that enforces NetworkPolicy (Cilium/Calico)
- the kube-apiserver CIDR filled into `networkpolicy.yaml`

## Credential flow: the agent never sees the GitHub token

The GitHub token lives in a Kubernetes Secret, but the agent **process** never
receives it. We use nono's built-in `github` credential route with **proxy
(phantom-token) injection**:

1. The Secret is mounted as env `GITHUB_TOKEN` on the container.
2. The nono **supervisor (parent)** reads `env://GITHUB_TOKEN` *before* applying
   the sandbox, then zeroizes it from memory after exec.
3. The agent **child** gets only a phantom token (`NONO_PROXY_TOKEN`), never the
   real one. It isn't in the child's env or memory.
4. When `gh`/`git` call `api.github.com`, nono's proxy validates the phantom
   token and swaps in the real `Authorization: token <real>` upstream.
   `--trust-proxy-ca` lets the Go-based `gh` CLI trust the proxy cert on Linux.

Result: a prompt-injected or compromised agent **cannot exfiltrate the GitHub
token**. For an even stronger variant (token absent from *every* process env),
mount the Secret as a file and point a `custom_credentials.github` route at
`file:///etc/nono/secrets/github-token` (see the note in
`agent-deployment.yaml`).

## Demo beats (live)

1. Show the agent pod running under `nono` (`kubectl -n agents get po`,
   then `kubectl -n agents exec ... -- nono status` / the strace check).
2. Ask the agent to "fix the failing podinfo deployment". It reads cluster
   state via the Flux MCP server (read-only) and finds the issue.
3. Show it CANNOT mutate: `kubectl auth can-i patch deploy --as=system:serviceaccount:agents:review-agent` -> **no**.
4. The agent opens a PR with the fix. Show the diff. A human (and a second
   agent) review and approve in the PR.
5. Merge. Flux reconciles. The fix lands.
6. Break something on purpose -> `git revert` -> Flux rolls it back. That's the
   whole point: the rollback is a commit, not a prayer.

## Files
- `networkpolicy.yaml` - namespace `agents` + default-deny egress (workload layer)
- `rbac.yaml` - read-only ServiceAccount/ClusterRole/binding (change layer)
- `agent-deployment.yaml` - the nono-wrapped agent + nono profile (process + workload)
- `seccomp-fallback.yaml` - custom seccomp profile for older nodes (docs only)
- `kustomization.yaml` - apply order

> Note: image refs, the API server CIDR, node labels, and the nono profile
> schema are placeholders. Fill them in for your cluster before the dry run.
