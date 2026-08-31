# KCD SF 2026 — Making Agent Work Human-Reviewable and Multiplayer with GitOps

Demo for Leigh and Tamao's talk. The thesis: an agent that can `kubectl patch`
can also `kubectl delete`, with no commit to roll back to. So we run the agent
as a **declarative workload**, confine it at three independent layers, and give
it exactly one write path: **a pull request a human reviews, and Flux applies**.

| Layer | Confines | Enforced by |
|---|---|---|
| Process | the agent binary | [nono](https://nono.sh) — kernel sandbox (Landlock / Seatbelt) with **L7 egress rules** and **phantom-token credential injection** |
| Workload | the pod | restricted Pod Security, read-only rootfs, no capabilities, default-deny NetworkPolicy |
| Change | the cluster | read-only RBAC; writes leave only as Git PRs that **Flux** reconciles after human review |

nono confines the process, the pod confines the workload, **Flux confines the
change** — and the rollback is `git revert`.

## The story

1. `checkout` is a deliberately CPU-hungry web service: **150 millicores per
   replica**, HPA `minReplicas: 2, maxReplicas: 4`, target 60% CPU.
2. A load-test Job saturates it predictably. The HPA climbs to **4/4 replicas,
   pinned near 100%**, and reports `ScalingLimited: TooManyReplicas`.
3. The **capacity agent** — a [Google ADK](https://adk.dev) agent running
   in-cluster under nono — notices. It verifies the saturation, measures the
   **schedulable CPU headroom on the nodes**, checks no capacity PR is already
   open, and decides the cluster can absorb more replicas.
4. It opens a **one-line pull request** raising `maxReplicas`, with the full
   capacity analysis in the PR body. It *cannot* patch the HPA: its RBAC is
   read-only, and its sandbox only lets it reach its own account's repos.
5. A human merges. Flux reconciles. The HPA scales out. The incident ends —
   and the whole remediation is a reviewable, revertible commit.

## Quickstart

Requirements: `kind`, `docker`, `kubectl`, `helm`, `gh` (or a `GITHUB_TOKEN`),
a Linux host/VM with kernel ≥ 6.7 for nono's Landlock TCP filtering (kind
nodes share the host kernel).

```bash
# a fine-grained PAT for the AGENT's GitHub account, on this repo:
#   Read:  Contents, Metadata    Write: Contents, Pull requests, Issues
export GITHUB_TOKEN=github_pat_...
# Gemini API key (https://aistudio.google.com — free tier, no card needed).
# Omit it and the agent runs in deterministic "heuristic" mode instead.
export GOOGLE_API_KEY=...

./infra/bootstrap.sh
```

This creates the 3-node kind cluster, installs metrics-server and
[flux-operator](https://fluxoperator.dev) (whose embedded **Flux Web UI**
lands on <http://localhost:8080>), applies the `FluxInstance` that syncs this
repo, builds and side-loads the two demo images, and creates the secrets.

Surfaces during the demo:

- Flux Web UI: <http://localhost:8080> (Kustomizations, sources, workloads, logs)
- checkout app: <http://localhost:9898/checkout>
- agent logs: `kubectl -n agents logs deploy/capacity-agent -f`

## Demo beats

```bash
# 1. Everything arrived via Git: the app, the HPA, and the agent itself
flux get kustomizations

# 2. Start the incident
kubectl apply -f loadtest/loadtest-job.yaml
kubectl -n checkout get hpa checkout -w        # 2 -> 4/4, ScalingLimited

# 3. Watch the agent engage (saturation check -> capacity math -> PR)
kubectl -n agents logs deploy/capacity-agent -f

# 4. Prove the agent CANNOT write to the cluster
kubectl auth can-i patch hpa -n checkout \
  --as=system:serviceaccount:agents:capacity-agent   # no

# 5. Prove the sandbox: same profile, live L7 denials
cd agents/capacity-agent
nono run --allow-cwd --profile manifests/nono-profile.json -- /bin/sh -c \
  '/usr/bin/curl -s https://api.github.com/repos/torvalds/linux'
#   -> {"error":"Forbidden"}  (the proxy, not GitHub — the request never left)

# 6. Review the PR on GitHub, merge it, and watch Flux close the loop
kubectl -n checkout get hpa checkout -w        # maxReplicas rises, load absorbed

# 7. End the incident
kubectl delete -f loadtest/loadtest-job.yaml
```

## Why the sandbox is the interesting part

The nono profile ([agents/capacity-agent/manifests/nono-profile.json](agents/capacity-agent/manifests/nono-profile.json))
is shipped to the pod as a ConfigMap **via Flux** and used verbatim by
`run-local.sh` on a laptop (Landlock on Linux, Seatbelt on macOS — same JSON):

- **Filesystem**: default-deny; writable paths are `/tmp` and the workdir.
- **Egress at L7**: `api.github.com` is allowed *per method and path* —
  `GET /repos/pepushi-evans/**`, `POST .../git/refs`, `PUT .../contents/**`,
  `POST .../pulls` — plus Gemini and the kube API. Everything else, including
  every other GitHub repo and all of `github.com`, is refused by the proxy.
  A prompt-injected agent cannot read public repos, exfiltrate to a gist, or
  push anywhere else.
- **Phantom tokens**: the nono supervisor reads `GITHUB_TOKEN` /
  `GOOGLE_API_KEY` *before* the sandbox is applied. The agent process receives
  decoy values; the proxy swaps in the real credentials upstream. The agent
  can *use* the PAT but never *hold* it.
- **In a pod**: runs under `RuntimeDefault` seccomp, non-root, no added
  capabilities, with `--sandbox-policy landlock` (pure-Landlock TCP
  enforcement, kernel ≥ 6.7; the default seccomp-notify baseline needs
  `pidfd_getfd`, which restricted pods deny). UDP stays unfiltered under pure
  Landlock — the default-deny NetworkPolicy is the second ring that covers it.

## The agent

`agents/capacity-agent/` is a Google ADK `LlmAgent` with six tools: three
read-only Kubernetes facts (HPA status, workload resources, node headroom) and
three GitHub verbs (list open capacity PRs, read the HPA manifest, open the
PR). The instruction walks a small model through the workflow step by step —
verify, dedupe, measure, bound the increase by node capacity, show the math in
the PR body.

Model backends:

- **Default**: Gemini via `GOOGLE_API_KEY` — the AI Studio free tier (no
  credit card) is enough, since the LLM is only invoked when the HPA is
  actually saturated. Model: `gemini-flash-latest`, override with `MODEL`.
- **Any OpenAI-compatible endpoint** via LiteLLM env vars, e.g. Groq
  (`LLM_MODEL=openai/llama-3.3-70b-versatile LLM_API_BASE=https://api.groq.com/openai/v1 LLM_API_KEY=...`),
  OpenRouter, or a local Ollama (`LLM_MODEL=ollama_chat/qwen3`).
- **No key at all**: `AGENT_MODE=heuristic` runs the same workflow as plain
  arithmetic — great for CI and offline runs. (`DRY_RUN=1` logs the PR
  instead of opening it.)
- GitHub Models was retired (July 2026) and Copilot subscriptions expose no
  supported model endpoint — older tutorials pointing there won't work.

Run it on your laptop against any cluster — same sandbox, no admin kubeconfig
exposure (it mints a short-lived token for the read-only ServiceAccount):

```bash
cd agents/capacity-agent && ./run-local.sh --once
```

## The Flux project's agent tooling in this demo

- **Schema gate** ([.github/workflows/validate.yaml](.github/workflows/validate.yaml)):
  every commit and PR — human- or agent-authored — must pass
  `flux schema validate` (the [fluxcd/flux-schema](https://github.com/fluxcd/flux-schema)
  CLI plugin) against the default Kubernetes+Flux catalog and the
  **ecosystem catalog** (schemas.fluxoperator.dev, ~9k CNCF CRD schemas,
  CEL rules included). Config lives in [.fluxschema.yml](.fluxschema.yml).
- **Agent self-check**: `open_capacity_pr` runs the same `flux-schema`
  validation on its proposed manifest *before* opening the PR — inside the
  sandbox, whose only new egress is `GET raw.githubusercontent.com/fluxcd/flux-schema/**`.
- **Flux MCP server** ([flux-operator-mcp](https://fluxoperator.dev/mcp-server/),
  installed read-only by bootstrap): the ADK agent mounts it as an
  `McpToolset` (`FLUX_MCP_URL`), filtered to `get_flux_instance`,
  `get_kubernetes_resources`, `search_flux_docs`, so the LLM can enrich its
  PR analysis with GitOps pipeline state. Remove the env var to run without it.
- **[fluxcd/agent-skills](https://github.com/fluxcd/agent-skills)**: the
  Flux project's SKILL.md packs for coding-agent harnesses (Claude Code,
  Copilot, Codex, `flux operator skills install`). This demo's agent bakes
  the same ideas into its ADK instruction; the skills' repo-audit phase uses
  the identical `flux-schema` validation this repo enforces in CI.

## SPIFFE workload identity (nono ≥ 0.70)

The sandbox can also source credentials from a **SPIRE Workload API** instead
of static secrets: the supervisor fetches short-lived JWT-SVIDs and rotates
them in the background, so the agent's identity is attested and expiring —
never a value in its environment. [agents/capacity-agent/spiffe/](agents/capacity-agent/spiffe/)
has a two-script local demo (throwaway SPIRE + a sandbox route that injects
`sub: spiffe://kcd-sf-26.demo/capacity-agent`) and the in-cluster CSI notes.

## Repo layout

```
infra/                      kind cluster config + bootstrap + FluxInstance
clusters/kind/              Flux Kustomizations (what the cluster follows)
apps/checkout/              the demo workload: app source, Deployment, HPA
loadtest/                   the incident, applied by hand
agents/capacity-agent/      ADK agent, Dockerfile, nono profile, manifests
```

The agent's PRs target exactly one line of `apps/checkout/hpa.yaml`.
