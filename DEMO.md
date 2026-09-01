# Demo runbook — KCD SF 2026 (25 min, ~9 min live demo)

Backup assets for every beat: [demo-backup/](demo-backup/) (screenshots,
terminal captures, agent PR bodies). If anything misbehaves live, switch to
those and keep talking.

## Preflight (10 minutes before)

```bash
kubectl config use-context kind-kcd-sf-26
flux get kustomizations                      # all Ready
kubectl -n agents get pods                   # 1/1 Running
kubectl -n agents logs deploy/capacity-agent --tail=2   # mode=llm, quiet ticks
gh pr list -R pepushi-evans/kcd-sf-26        # no open PRs
curl -s localhost:9898/checkout              # app answers
curl -s -o /dev/null -w '%{http_code}\n' localhost:8080  # UI answers: 200
```

- Laptop must reach the VM's ports: forward **8080** (Flux UI) and **9898**
  (app), e.g. `ssh -L 8080:localhost:8080 -L 9898:localhost:9898 <vm>`.
- Browser must be logged into GitHub as **pepushi-evans** (the repo is
  private to that account).
- Pre-open the browser tabs from the Links table below, in order.
- Terminal: big font, two panes (one for `watch`/logs, one for commands).

## Links (pre-open as tabs, in this order)

| # | What | URL |
|---|------|-----|
| 1 | Flux UI dashboard | <http://localhost:8080/> |
| 2 | Flux UI: checkout workload (graph + CPU + pods) | <http://localhost:8080/workload/Deployment/checkout/checkout> |
| 3 | Flux UI: agent workload (log viewer) | <http://localhost:8080/workload/Deployment/agents/capacity-agent> |
| 4 | Flux UI: apps Kustomization | <http://localhost:8080/resource/Kustomization/flux-system/apps> |
| 5 | **GitHub: pull requests** | <https://github.com/pepushi-evans/kcd-sf-26/pulls> |
| 6 | Source: nono profile (L7 rules) | <https://github.com/pepushi-evans/kcd-sf-26/blob/main/agents/capacity-agent/manifests/nono-profile.json> |
| 7 | Source: agent Deployment (3 layers, phantom tokens) | <https://github.com/pepushi-evans/kcd-sf-26/blob/main/agents/capacity-agent/manifests/deployment.yaml> |
| 8 | Source: the HPA the PR edits | <https://github.com/pepushi-evans/kcd-sf-26/blob/main/apps/checkout/hpa.yaml> |
| 9 | Source: load test Job | <https://github.com/pepushi-evans/kcd-sf-26/blob/main/loadtest/loadtest-job.yaml> |
| 10 | Source: agent instruction + tools | <https://github.com/pepushi-evans/kcd-sf-26/blob/main/agents/capacity-agent/app/agent.py> |
| 11 | Live app | <http://localhost:9898/checkout> |
| 12 | (if CI pushed) Actions | <https://github.com/pepushi-evans/kcd-sf-26/actions> |

## The beats

**Timing anchor: start the load test EARLY.** Apply → saturation ≈ 2.5 min,
agent tick ≤ 1 min, LLM run ≈ 1 min. **Load-test apply → PR on GitHub ≈ 4-5
minutes**, so start it, then narrate the architecture while it cooks.

```bash
# 1. (30s) Baseline — everything here arrived via Git
flux get kustomizations                       # or tab 1 / tab 4
curl -s localhost:9898/checkout               # or tab 11

# 2. (30s) START THE INCIDENT NOW — it needs ~4 min to reach the PR
kubectl apply -f loadtest/loadtest-job.yaml
kubectl -n checkout get hpa checkout -w       # leave running in pane 2

# 3. (2 min, while it saturates) The three layers, from source:
#    tab 6 (nono profile: L7 endpoints, phantom creds)
#    tab 7 (Deployment: nono wrap, read-only SA, restricted pod)
#    Then prove them live:
kubectl auth can-i patch hpa -n checkout \
  --as=system:serviceaccount:agents:capacity-agent          # -> no
cd agents/capacity-agent
GITHUB_TOKEN=$(gh auth token) nono run --silent --allow-cwd \
  --profile manifests/nono-profile.json -- /bin/sh -c \
  '/usr/bin/curl -s https://api.github.com/repos/torvalds/linux'
#   -> {"error":"Forbidden"}   (the sandbox proxy; it never reached GitHub)
cd ../..

# 4. (1-2 min) HPA hits 4/4 ScalingLimited (pane 2) — switch to agent logs:
kubectl -n agents logs deploy/capacity-agent -f    # or tab 3 (UI log viewer)
#    Narrate the tool calls as they print: hpa status -> PR dedupe ->
#    resources -> node headroom -> manifest -> flux MCP context ->
#    open_capacity_pr (flux-schema self-check runs inside it)

# 5. (2 min) Review the PR: tab 5 -> the new PR
#    Show: one-line diff, the analysis table, node-headroom math,
#    GitOps Context (from the Flux MCP server), rollback plan.
#    MERGE IT (squash). Back to pane 2:
flux reconcile kustomization apps 2>/dev/null   # skip the 1-min wait
kubectl -n checkout get hpa checkout -w         # maxReplicas rises, scales out,
                                                # utilization falls toward 60%

# 6. (30s) The kicker: the remediation is a commit.
#    "Rollback is `git revert`, not an incident bridge."

# 7. End the incident (also right after the talk):
kubectl delete -f loadtest/loadtest-job.yaml
```

## Reset between rehearsals

```bash
kubectl delete -f loadtest/loadtest-job.yaml --ignore-not-found
gh pr list -R pepushi-evans/kcd-sf-26                    # close unmerged PRs:
gh pr close <n> -R pepushi-evans/kcd-sf-26 --delete-branch
# if you merged during rehearsal, revert so the demo starts at maxReplicas 4:
git pull stealthybox main && git revert --no-edit <merge-sha> && git push stealthybox main
flux reconcile kustomization apps
# HPA scales back to 2 within ~2 min of load removal
```

## If it breaks

| Symptom | Move |
|---|---|
| Gemini quota/5xx mid-demo | agent logs show the error honestly; show `demo-backup/agent-engagement-log.txt` + `pr-2-agent-body.md` and narrate |
| Agent pod not Running | `flux reconcile kustomization agents`; else `kubectl -n agents rollout restart deploy/capacity-agent` |
| HPA stuck `<unknown>` | metrics-server hiccup: `kubectl -n kube-system rollout restart deploy/metrics-server` (recovers in ~30s) |
| UI tab dead | `kubectl -n flux-system get pods`; fall back to `demo-backup/ui-*.png` |
| GitHub/network down | full backup path: screenshots + text captures in `demo-backup/`, PR bodies included |
