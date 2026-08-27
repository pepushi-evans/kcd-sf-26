"""Read-only Kubernetes tools for the capacity agent.

The agent's ServiceAccount can get/list/watch — nothing else. Every fact the
LLM reasons about comes from these functions; every change it wants goes out
as a Git pull request instead (github_tools.py).
"""
import os

from kubernetes import client, config

HPA_NAMESPACE = os.environ.get("HPA_NAMESPACE", "checkout")
HPA_NAME = os.environ.get("HPA_NAME", "checkout")

_loaded = False


def _api():
    global _loaded
    if not _loaded:
        try:
            config.load_incluster_config()
            in_cluster = True
        except config.ConfigException:
            config.load_kube_config()
            in_cluster = False
        cfg = client.Configuration.get_default_copy()
        if in_cluster:
            # Dial the API server by DNS name instead of raw ClusterIP so the
            # sandbox proxy can match it against its hostname allowlist.
            cfg.host = "https://kubernetes.default.svc"
        # Under nono, all egress must traverse the sandbox proxy. The
        # kubernetes client ignores proxy env vars, so wire it explicitly —
        # and drop NO_PROXY so a loopback API server (kind on a laptop) is
        # proxied too instead of being direct-dialed into a Landlock denial.
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            cfg.proxy = proxy
            cfg.no_proxy = None  # the kubeconfig loader captures env NO_PROXY
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)
        client.Configuration.set_default(cfg)
        _loaded = True
    return client


def parse_cpu(q: str) -> int:
    """Kubernetes CPU quantity -> millicores."""
    if q.endswith("n"):
        return int(q[:-1]) // 1_000_000
    if q.endswith("u"):
        return int(q[:-1]) // 1000
    if q.endswith("m"):
        return int(q[:-1])
    return int(float(q) * 1000)


def get_hpa_status() -> dict:
    """Return the live status of the checkout HorizontalPodAutoscaler.

    Includes replica counts, the CPU utilization target vs. current value,
    and the HPA's own conditions (ScalingLimited=True with reason
    TooManyReplicas means the HPA wants more replicas than maxReplicas
    allows).
    """
    k = _api()
    hpa = k.AutoscalingV2Api().read_namespaced_horizontal_pod_autoscaler(
        HPA_NAME, HPA_NAMESPACE
    )
    current_util = None
    if hpa.status.current_metrics:
        for m in hpa.status.current_metrics:
            if m.type == "Resource" and m.resource.name == "cpu":
                current_util = m.resource.current.average_utilization
    target_util = None
    for m in hpa.spec.metrics or []:
        if m.type == "Resource" and m.resource.name == "cpu":
            target_util = m.resource.target.average_utilization
    return {
        "namespace": HPA_NAMESPACE,
        "name": HPA_NAME,
        "target_ref": f"{hpa.spec.scale_target_ref.kind}/{hpa.spec.scale_target_ref.name}",
        "min_replicas": hpa.spec.min_replicas,
        "max_replicas": hpa.spec.max_replicas,
        "current_replicas": hpa.status.current_replicas,
        "desired_replicas": hpa.status.desired_replicas,
        "target_cpu_utilization_pct": target_util,
        "current_cpu_utilization_pct": current_util,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (hpa.status.conditions or [])
        ],
    }


def get_workload_resources() -> dict:
    """Return per-replica CPU/memory requests and limits of the scale target."""
    k = _api()
    dep = k.AppsV1Api().read_namespaced_deployment(HPA_NAME, HPA_NAMESPACE)
    c = dep.spec.template.spec.containers[0]
    req = c.resources.requests or {}
    lim = c.resources.limits or {}
    return {
        "deployment": f"{HPA_NAMESPACE}/{dep.metadata.name}",
        "ready_replicas": dep.status.ready_replicas,
        "cpu_request_millicores": parse_cpu(req.get("cpu", "0")),
        "cpu_limit_millicores": parse_cpu(lim.get("cpu", "0")),
        "memory_request": req.get("memory"),
        "memory_limit": lim.get("memory"),
    }


def get_cluster_capacity() -> dict:
    """Return schedulable CPU headroom: per-node allocatable CPU minus the
    CPU requests of every scheduled pod, in millicores.

    free_cpu_millicores is how much MORE CPU the scheduler could still
    place — the number that tells you whether raising maxReplicas is safe.
    """
    k = _api()
    core = k.CoreV1Api()
    nodes = []
    for n in core.list_node().items:
        unschedulable = bool(n.spec.unschedulable)
        taints = [
            t.key for t in (n.spec.taints or [])
            if t.effect in ("NoSchedule", "NoExecute")
        ]
        nodes.append({
            "name": n.metadata.name,
            "allocatable_cpu_millicores": parse_cpu(n.status.allocatable["cpu"]),
            "requested_cpu_millicores": 0,
            "schedulable": not unschedulable and not taints,
            "taints": taints,
        })
    by_name = {n["name"]: n for n in nodes}
    for p in core.list_pod_for_all_namespaces(
        field_selector="status.phase!=Succeeded,status.phase!=Failed"
    ).items:
        node = by_name.get(p.spec.node_name)
        if node is None:
            continue
        for c in p.spec.containers:
            if c.resources and c.resources.requests:
                node["requested_cpu_millicores"] += parse_cpu(
                    c.resources.requests.get("cpu", "0")
                )
    schedulable = [n for n in nodes if n["schedulable"]]
    free = sum(
        max(0, n["allocatable_cpu_millicores"] - n["requested_cpu_millicores"])
        for n in schedulable
    )
    return {
        "nodes": nodes,
        "schedulable_nodes": len(schedulable),
        "free_cpu_millicores": free,
    }
