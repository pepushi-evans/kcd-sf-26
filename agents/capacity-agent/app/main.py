"""Watch loop for the capacity agent.

Cheap deterministic checks run every AGENT_INTERVAL seconds against the HPA;
the LLM is only invoked when the HPA is actually saturated (free-tier rate
limits appreciate this). AGENT_MODE=heuristic runs the same remediation with
plain arithmetic and no model at all — useful for CI and keyless dry runs.
"""
import asyncio
import math
import os
import sys
import time

import github_tools
import k8s_tools

INTERVAL = int(os.environ.get("AGENT_INTERVAL", "60"))
# llm unless neither a Gemini key nor a LiteLLM endpoint is configured —
# keyless environments still get the deterministic remediation path.
_default_mode = "llm" if (
    os.environ.get("GOOGLE_API_KEY") or os.environ.get("LLM_MODEL")
) else "heuristic"
MODE = os.environ.get("AGENT_MODE", _default_mode)


def log(msg: str) -> None:
    print(f"[capacity-agent] {msg}", flush=True)


def is_saturated(hpa: dict) -> bool:
    if hpa["current_replicas"] != hpa["max_replicas"]:
        return False
    cur, target = hpa["current_cpu_utilization_pct"], hpa["target_cpu_utilization_pct"]
    if cur is None or target is None or cur < target:
        return False
    return any(
        c["type"] == "ScalingLimited" and c["status"] == "True"
        and c["reason"] == "TooManyReplicas"
        for c in hpa["conditions"]
    )


async def remediate_llm(hpa: dict) -> None:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from agent import build_agent

    app, user, session = "capacity-agent", "operator", f"incident-{int(time.time())}"
    sessions = InMemorySessionService()
    await sessions.create_session(app_name=app, user_id=user, session_id=session)
    runner = Runner(app_name=app, agent=build_agent(), session_service=sessions)

    trigger = (
        "The checkout HPA looks saturated. Investigate and, if the analysis "
        f"supports it, propose a capacity PR. Trigger snapshot: {hpa}"
    )
    msg = types.Content(role="user", parts=[types.Part(text=trigger)])
    async for event in runner.run_async(user_id=user, session_id=session, new_message=msg):
        calls = event.get_function_calls() if hasattr(event, "get_function_calls") else []
        for c in calls or []:
            log(f"tool call: {c.name}({c.args})")
        if event.is_final_response() and event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts)
            log(f"final: {text}")


def remediate_heuristic(hpa: dict) -> None:
    """The same workflow the LLM follows, as arithmetic. No model required."""
    prs = github_tools.list_open_capacity_prs()["open_capacity_prs"]
    if prs:
        log(f"capacity PR already open: {prs[0]['url']} — standing by")
        return
    workload = k8s_tools.get_workload_resources()
    capacity = k8s_tools.get_cluster_capacity()
    manifest = github_tools.get_hpa_manifest()
    if manifest["max_replicas_in_git"] != hpa["max_replicas"]:
        log("git already ahead of cluster; waiting for Flux to reconcile")
        return
    demand = math.ceil(
        hpa["current_replicas"]
        * hpa["current_cpu_utilization_pct"]
        / hpa["target_cpu_utilization_pct"]
    ) + 1
    per_replica = max(1, workload["cpu_request_millicores"])
    cap_fit = hpa["max_replicas"] + (capacity["free_cpu_millicores"] // 2) // per_replica
    new_max = min(demand, cap_fit, hpa["max_replicas"] * 2)
    if new_max <= hpa["max_replicas"]:
        log(f"no safe increase available (demand={demand}, fit={cap_fit})")
        return
    title = f"checkout: raise HPA maxReplicas {hpa['max_replicas']} -> {new_max}"
    body = f"""## Capacity analysis (heuristic mode)

| observation | value |
|---|---|
| replicas | {hpa['current_replicas']}/{hpa['max_replicas']} (max) |
| CPU utilization | {hpa['current_cpu_utilization_pct']}% (target {hpa['target_cpu_utilization_pct']}%) |
| per-replica CPU request | {per_replica}m |
| schedulable free CPU | {capacity['free_cpu_millicores']}m across {capacity['schedulable_nodes']} nodes |

Estimated demand is ~{demand} replicas. The additional
{new_max - hpa['max_replicas']} replicas need {(new_max - hpa['max_replicas']) * per_replica}m,
well under 50% of free capacity. After merge, Flux reconciles the HPA and the
autoscaler absorbs the load. Roll back with `git revert`.
"""
    result = github_tools.open_capacity_pr(new_max, title, body)
    log(f"opened: {result}")


async def tick() -> bool:
    hpa = k8s_tools.get_hpa_status()
    log(
        f"hpa {hpa['namespace']}/{hpa['name']}: "
        f"{hpa['current_replicas']}/{hpa['max_replicas']} replicas, "
        f"cpu {hpa['current_cpu_utilization_pct']}% (target {hpa['target_cpu_utilization_pct']}%)"
    )
    if not is_saturated(hpa):
        return False
    log("HPA saturated at maxReplicas — engaging")
    if MODE == "heuristic":
        remediate_heuristic(hpa)
    else:
        await remediate_llm(hpa)
    return True


async def main() -> None:
    once = "--once" in sys.argv
    log(f"mode={MODE} interval={INTERVAL}s repo={github_tools.REPO} dry_run={github_tools.DRY_RUN}")
    while True:
        try:
            engaged = await tick()
        except Exception as e:  # keep the watch loop alive through API blips
            log(f"tick error: {type(e).__name__}: {e}")
            engaged = False
        if once:
            sys.exit(0 if engaged else 3)
        # After engaging, wait longer so an open PR gets reviewed before we
        # re-examine the same incident.
        await asyncio.sleep(INTERVAL * 5 if engaged else INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
