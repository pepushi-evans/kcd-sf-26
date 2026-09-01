# checkout: raise HPA maxReplicas 4 -> 7

### Capacity Analysis & Proposal: `checkout` HPA

#### 1. Observed HPA State
| Metric | Value |
| --- | --- |
| Target Ref | `Deployment/checkout` |
| Namespace | `checkout` |
| Current Replicas | 4 |
| Desired Replicas | 4 |
| Min Replicas | 2 |
| Current Max Replicas | 4 |
| Target CPU Utilization | 60% |
| Observed CPU Utilization | 86% |
| Status Condition | `ScalingLimited: True (Reason: TooManyReplicas)` |

The HPA is saturated and actively constrained by `maxReplicas: 4`.

---

#### 2. Demand Estimation
- **Base Demand**: $\lceil 4 \times \frac{86}{60} \rceil = \lceil 5.733 \rceil = 6 \text{ replicas}$
- **Headroom Buffer**: $+1 \text{ replica}$
- **Proposed `maxReplicas`**: **7** (increase of 3 replicas)

---

#### 3. Node Headroom & Capacity Verification
- **Per-replica CPU Request**: 150m
- **Per-replica Memory Request**: 64Mi
- **Cluster Schedulable CPU Headroom**: 26,300m across 2 schedulable worker nodes (`kcd-sf-26-worker`, `kcd-sf-26-worker2`)
- **Capacity Ceiling (50% safety budget)**: $0.50 \times 26,300\text{m} = 13,150\text{m}$
- **Additional CPU Required**: $3 \text{ replicas} \times 150\text{m} = 450\text{m}$

$450\text{m} \ll 13,150\text{m}$ (well within the safety threshold). Schedulable nodes have ample resources to host the additional pods.

---

#### 4. Expected Outcome
Once this PR is merged:
1. Flux will synchronize the updated HPA manifest (`apps/checkout/hpa.yaml`) setting `maxReplicas: 7`.
2. The HPA controller will scale out the `checkout` deployment from 4 to 6 replicas, bringing CPU utilization back near the 60% target.
3. The `ScalingLimited` condition will clear.

---

#### 5. Rollback Plan
If unexpected behavior occurs, revert the merge commit via Git:
```bash
git revert <merge-commit-sha>
git push origin main
```
Flux will automatically reconcile the HPA back to `maxReplicas: 4`.

