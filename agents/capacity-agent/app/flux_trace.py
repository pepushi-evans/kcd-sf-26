"""Flux ownership trace: derive the agent's GitHub target from the cluster.

The only configuration the agent takes is the target HPA. Everything else is
discovered flux-trace style, by following Flux's ownership labels:

    HPA  --kustomize.toolkit.fluxcd.io/{name,namespace} labels-->
      Kustomization (spec.path, spec.sourceRef) -->
        GitRepository (spec.url, spec.ref)

so the repository and branch are derived state, never env vars. The nono
profile's static allowlist deliberately does NOT come from this trace: it is
the independent security control, so a misconfigured (or tampered-with)
trace lands on the sandbox wall instead of on a foreign repository.

The trace runs through the Flux Operator MCP server
(get_kubernetes_resources) when FLUX_MCP_URL is set, and falls back to
direct read-only kube API calls otherwise (e.g. run-local.sh without MCP).
"""
import os
import re

import yaml

FLUX_MCP_URL = os.environ.get("FLUX_MCP_URL")


async def _mcp_get(kind: str, api_version: str, name: str, namespace: str) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(FLUX_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_kubernetes_resources",
                {"apiVersion": api_version, "kind": kind,
                 "name": name, "namespace": namespace},
            )
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    for doc in yaml.safe_load_all(text):
        if isinstance(doc, dict) and doc.get("kind") == kind:
            return doc
        # list responses wrap items
        if isinstance(doc, dict) and isinstance(doc.get("items"), list):
            for item in doc["items"]:
                if item.get("kind") == kind:
                    return item
    raise RuntimeError(f"MCP returned no {kind} {namespace}/{name}")


def _direct_get(kind: str, api_version: str, name: str, namespace: str) -> dict:
    from kubernetes import client

    import k8s_tools

    k8s_tools._api()
    group, _, version = api_version.partition("/")
    plural = {
        "HorizontalPodAutoscaler": "horizontalpodautoscalers",
        "Kustomization": "kustomizations",
        "GitRepository": "gitrepositories",
    }[kind]
    return client.CustomObjectsApi().get_namespaced_custom_object(
        group=group, version=version, namespace=namespace,
        plural=plural, name=name,
    )


async def _get(kind: str, api_version: str, name: str, namespace: str) -> dict:
    if FLUX_MCP_URL:
        return await _mcp_get(kind, api_version, name, namespace)
    return _direct_get(kind, api_version, name, namespace)


def _parse_github_url(url: str) -> str:
    m = re.match(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([^/]+)/([^/]+?)(?:\.git)?/?$",
        url,
    )
    if not m:
        raise RuntimeError(f"GitRepository url is not a GitHub repo: {url}")
    return f"{m.group(1)}/{m.group(2)}"


async def trace_hpa_to_source() -> dict:
    """Trace the target HPA to the Git repository that manages it.

    Follows Flux ownership: the HPA's kustomize.toolkit.fluxcd.io labels
    name the Kustomization that applied it; its sourceRef names the
    GitRepository; that object holds the URL and branch. Returns the full
    chain — this is where the agent's GitHub coordinates come from (they
    are never configured by hand). Include the chain in any PR body as the
    GitOps context.
    """
    import k8s_tools

    ns, name = k8s_tools.HPA_NAMESPACE, k8s_tools.HPA_NAME
    hpa = await _get("HorizontalPodAutoscaler", "autoscaling/v2", name, ns)
    labels = hpa.get("metadata", {}).get("labels", {})
    ks_name = labels.get("kustomize.toolkit.fluxcd.io/name")
    ks_ns = labels.get("kustomize.toolkit.fluxcd.io/namespace")
    if not ks_name or not ks_ns:
        raise RuntimeError(
            f"HPA {ns}/{name} has no Flux ownership labels — not GitOps-managed?"
        )
    ks = await _get("Kustomization", "kustomize.toolkit.fluxcd.io/v1", ks_name, ks_ns)
    src = ks["spec"]["sourceRef"]
    if src.get("kind") != "GitRepository":
        raise RuntimeError(f"Kustomization source is {src.get('kind')}, not GitRepository")
    repo = await _get(
        "GitRepository", "source.toolkit.fluxcd.io/v1",
        src["name"], src.get("namespace", ks_ns),
    )
    url = repo["spec"]["url"]
    ref = repo["spec"].get("ref", {})
    branch = ref.get("branch") or ref.get("name", "refs/heads/main")
    branch = branch.removeprefix("refs/heads/")
    return {
        "hpa": f"{ns}/{name}",
        "kustomization": f"{ks_ns}/{ks_name}",
        "kustomization_path": ks["spec"].get("path", "./").strip("./") or ".",
        "git_repository": f"{src.get('namespace', ks_ns)}/{src['name']}",
        "url": url,
        "branch": branch,
        "github_repo": _parse_github_url(url),
        "traced_via": "flux-operator-mcp" if FLUX_MCP_URL else "kubernetes-api",
    }
