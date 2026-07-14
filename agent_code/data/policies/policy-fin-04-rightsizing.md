# POLICY-FIN-04: Rightsizing and idle compute

## When this applies
Any Compute Engine instance or GKE node pool running below 20 percent
average CPU utilization over a sustained 7-day window, where the resource
is not tagged env=dev or env=staging (dev/staging idle is expected and out
of scope for this policy).

## Required action before proposing a change
Check the resource's labels for a `batch-schedule` tag before proposing
any rightsizing or shutdown. Several analytics and ML workloads run
scheduled batch jobs that are idle 90 percent of the time by design and
are correctly sized for their peak, not their average. Only propose
rightsizing if the workload's utilization is flat-low across all hours,
not idle-except-during-a-scheduled-window.

## Approved actions
1. Rightsize the machine type down to the smallest type whose peak-hour
   utilization would stay under 70 percent, based on the last 30 days of
   peak observed usage, not average usage.
2. For GKE node pools specifically, enabling cluster autoscaler with an
   appropriate minimum is preferred over a one-time manual resize, since it
   self-corrects as load changes rather than requiring re-review later.
3. Do not propose deleting or stopping a resource under this policy —
   rightsizing only. Stopping a resource that's actually in use, even at
   low utilization, is a service-impact risk this policy is not authorized
   to approve; that requires the owning team's explicit sign-off.

## Precedent
INC-FIN-0021: a rightsizing proposal for the fraud-detection service was
incorrectly generated based on average CPU across 30 days, which included
weekends when the service is genuinely idle by design. The proposal would
have undersized the instance for weekday peak load. Since then, this
policy requires checking for a `batch-schedule` label before proposing
any change based on averaged utilization.
