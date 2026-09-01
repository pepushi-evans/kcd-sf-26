# checkout: raise HPA maxReplicas 4 -> 8

## Summary

The `checkout` HorizontalPodAutoscaler is currently saturated at its ceiling of `4` replicas, with CPU utilization at `88%` against a target of `60%`. This PR raises `maxReplicas` from `4` to `8` in `apps/checkout/hpa.yaml`.

---

### Observed HPA State

| Metric | Value |
| --- | --- |
| Target Ref | `Deployment/checkout` |
| Namespace | `checkout` |
| Current Replicas | `4` |
| Desired Replicas | `4` |
| Min Replicas | `2` |
| Current Max Replicas | `4` |
| Target CPU Utilization | `60%` |
| Current CPU Utilization | `88%` |
| Scaling Limited Condition | `True` (`TooManyReplicas` - the desired replica count is more than the maximum replica count) |

---

### Capacity & Demand Analysis

1. **Demand Estimation**:
   - Estimated required replicas: $\lceil 4 \times \frac{88}{60} \rceil = \lceil 5.87 \rceil = 6$ replicas.
   - Adding 1-2 replicas of headroom yields a target ceiling of **8** replicas (at most double the current max).

2. **Per-Replica Resource Footprint**:
   - CPU Request: `150m`
   - CPU Limit: `150m`
   - Memory Request: `64Mi`
   - Memory Limit: `128Mi`

3. **Cluster Schedulable Headroom**:
   - Total schedulable free CPU: **26,290m** across 2 worker nodes (`kcd-sf-26-worker`, `kcd-sf-26-worker2`).
   - 50% Safety Budget: `13,145m`.
   - Additional Replicas: $8 - 4 = 4$ replicas.
   - Additional CPU Request: $4 \times 150\text{m} = 600\text{m}$.
   - Headroom consumption: $\frac{600\text{m}}{26,290\text{m}} \approx 2.28\%$ of available free CPU (well within the 50% limit).

---

### GitOps Context

- **Managed by Flux Kustomization**: `flux-system/apps` (path: `./apps`)
- **Manifest**: `apps/checkout/hpa.yaml`

---

### Expected Post-Merge Behavior

- Flux Kustomization `apps` reconciles the updated HPA manifest.
- HPA condition `ScalingLimited` will clear.
- HPA will scale the `checkout` deployment up to ~6 replicas, bringing average CPU utilization down to the target 60%.

---

### Rollback Plan

If unexpected behavior occurs, revert the merge commit via Git:
```bash
git revert <merge-commit-sha>
git push origin main
```
Flux will automatically reconcile the previous HPA configuration.

