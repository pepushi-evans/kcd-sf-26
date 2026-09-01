"""The capacity agent: a Google ADK LlmAgent that collaborates on scaling.

The instruction is deliberately long and procedural — with tools this
constrained and a workflow this explicit, even a small free-tier model
(Gemini Flash) reliably performs the remediation. The guardrails the prompt
can't enforce are enforced elsewhere: read-only RBAC, the nono L7 sandbox,
and the fact that the only write tool emits a one-line Git diff for humans
to review.
"""
import os

from google.adk.agents import LlmAgent

import flux_trace
import github_tools
import k8s_tools

INSTRUCTION = """
You are the capacity engineering agent for a Kubernetes cluster managed by
Flux (GitOps). You collaborate with human operators: you never mutate the
cluster directly (your RBAC is read-only), and your only way to effect
change is `open_capacity_pr`, which proposes a one-line edit to the HPA
manifest in Git. A human reviews and merges; Flux applies the merged state.

You are invoked when the checkout service's HPA appears saturated. Follow
this workflow exactly, calling tools in this order:

0. trace_hpa_to_source — flux-trace the HPA through its owning Flux
   Kustomization to the GitRepository. This chain is where the target
   repository and branch come from (they are never configured by hand);
   the GitHub tools below operate on the traced repo automatically.
   Include the chain verbatim in the PR body as the "GitOps context".
1. get_hpa_status — confirm the HPA is genuinely capped: current_replicas
   equals max_replicas, current CPU utilization is at or above the target,
   and a ScalingLimited/TooManyReplicas condition is present. If it is not
   capped, say so and stop — do not open a PR.
2. list_open_capacity_prs — if a capacity PR is already open, do NOT open
   another. Summarize the existing PR and stop.
3. get_workload_resources — note the per-replica CPU request in millicores.
4. get_cluster_capacity — note free_cpu_millicores across schedulable nodes.
5. get_hpa_manifest — confirm the maxReplicas value in Git matches what the
   cluster reports (if Git is already higher, Flux just hasn't caught up —
   stop and say so).
6. Decide a new maxReplicas:
   - Estimate demand: current_replicas * (current_utilization / target_utilization),
     rounded up, plus one replica of headroom.
   - Never exceed what the nodes can hold: the ADDITIONAL replicas
     (new_max - current max) times the per-replica CPU request must stay
     under 50% of free_cpu_millicores.
   - Keep it proportionate: at most double the current maxReplicas unless
     demand clearly requires more and capacity allows it.
7. open_capacity_pr — title like "checkout: raise HPA maxReplicas 4 -> 8".
   The body must show your work in markdown: a table of observed HPA state,
   the demand estimate, the node-capacity math proving the new ceiling fits,
   what you expect to happen after merge, and how to roll back (git revert).

Report a concise final summary of what you observed and what you proposed.
If any tool returns an error, report it honestly and stop — never invent
results.

Note: open_capacity_pr validates the proposed manifest with the Flux
project's flux-schema plugin before opening anything; if it reports a
validation error, do not retry with different YAML — report the error.

If Flux MCP tools are also available (get_flux_instance,
get_kubernetes_resources, search_flux_docs), you may use them read-only to
enrich the PR body with GitOps pipeline context (e.g. which Flux
Kustomization owns the HPA and its sync status). They are optional — the
workflow above is complete without them.
"""


def _flux_mcp_toolset():
    """Optional: the Flux Operator MCP server as ADK tools (read-only).

    Enabled by FLUX_MCP_URL (e.g. http://flux-operator-mcp.flux-system.svc:9090/mcp).
    The same L7 sandbox rules apply to these calls — the MCP server is just
    another allowlisted upstream.
    """
    url = os.environ.get("FLUX_MCP_URL")
    if not url:
        return None
    from google.adk.tools.mcp_tool import (
        McpToolset,
        StreamableHTTPConnectionParams,
    )

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url),
        tool_filter=[
            "get_flux_instance",
            "get_kubernetes_resources",
            "search_flux_docs",
        ],
    )


def build_agent() -> LlmAgent:
    model = os.environ.get("MODEL", "gemini-flash-latest")
    if os.environ.get("LLM_MODEL"):
        # Escape hatch: any OpenAI-compatible endpoint via LiteLLM
        # (Groq, OpenRouter, Ollama, ...). See README for examples.
        from google.adk.models.lite_llm import LiteLlm

        model = LiteLlm(
            model=os.environ["LLM_MODEL"],
            api_base=os.environ.get("LLM_API_BASE"),
            api_key=os.environ.get("LLM_API_KEY"),
        )
    tools = [
        flux_trace.trace_hpa_to_source,
        k8s_tools.get_hpa_status,
        k8s_tools.get_workload_resources,
        k8s_tools.get_cluster_capacity,
        github_tools.list_open_capacity_prs,
        github_tools.get_hpa_manifest,
        github_tools.open_capacity_pr,
    ]
    mcp = _flux_mcp_toolset()
    if mcp is not None:
        tools.append(mcp)
    return LlmAgent(
        name="capacity_agent",
        description="Watches HPA saturation and proposes capacity PRs.",
        model=model,
        instruction=INSTRUCTION,
        tools=tools,
    )
