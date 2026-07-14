# POLICY-FIN-11: Idle-while-running GPU and accelerator instances

## When this applies
Any GPU-attached instance (A2, G2 machine families) where utilization
drops below 10 percent for a sustained period after previously running at
high utilization, without a corresponding shutdown or scale-down event.
This is different from a resource that was always idle — this policy is
specifically about a workload that finished and the instance was simply
left running.

## Required action before proposing a change
Distinguish a genuinely finished job from a job that is idle between
processing batches. Check whether the utilization drop is sustained
(multiple consecutive days flat-low) versus intermittent (idle for an
hour, busy again, repeating) — intermittent idle-then-busy cycling is
often a normal queue-processing pattern and should not be flagged.
Sustained flat-low utilization after a period of sustained high
utilization is the pattern this policy targets.

## Approved actions
1. Propose stopping the instance (not deleting — GPU instances are
   frequently needed again soon for the next training run) and include
   the exact per-hour cost being incurred by the idle instance so the
   approver can see the accumulating cost of delay.
2. If the instance belongs to a team with a documented "keep warm between
   runs" exception (check labels for `keep-warm=true`), do not propose
   stopping it — recommend a smaller always-on instance instead, if one
   exists at a materially lower hourly cost, so the team keeps the
   fast-restart benefit without paying full GPU price while idle.
3. GPU instances are typically the highest hourly-cost resource type in
   this org; a proposal here should always state the projected savings in
   dollars per hour, not just per month, since stakeholders often
   underestimate how quickly idle GPU cost accumulates.

## Precedent
INC-FIN-0028: an A2 GPU instance for the ml-training team was found
running at 4 percent utilization for 3 days after a training job
completed, accumulating roughly 140 dollars of idle cost before being
noticed. The team had no monitoring alert configured for this pattern.
This policy was written specifically as the standing rule for the FinOps
copilot to catch this pattern automatically going forward, rather than
relying on the team noticing it themselves.
