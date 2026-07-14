# POLICY-FIN-07: Orphaned resource cleanup

## When this applies
Persistent disks with no attached instance, and reserved static IP
addresses with no attached instance or forwarding rule, present in the
resource inventory for more than 3 consecutive days.

## Required action before proposing a change
Orphaned does not always mean unused. Check whether the disk has a
snapshot schedule attached — a disk can be intentionally detached from its
instance as part of a backup or migration workflow while still being
actively needed. A disk with an active snapshot schedule and no attached
instance for less than 14 days should be flagged as "monitor" rather than
"delete", not proposed for deletion.

## Approved actions
1. For orphaned persistent disks older than 14 days with no snapshot
   schedule: propose deletion, with the projected monthly savings based on
   the disk's size and type.
2. For unused static IP addresses: propose release of the reservation.
   Static IPs incur a charge specifically because they are reserved and
   idle — an attached static IP does not incur this charge, only a
   reserved-but-unattached one does.
3. Always include the resource's creation date and last-attached date (if
   known) in the proposal so the approver can judge whether this looks
   like an abandoned resource or a recent, deliberate detachment.

## Precedent
INC-FIN-0033: an orphaned-disk cleanup proposal was approved and executed
for a disk that turned out to be a deliberately detached disaster-recovery
volume for the payments database, reattached only during a specific
quarterly DR drill. The disk had no snapshot schedule (DR volumes in this
org are snapshotted externally, not via GCP's schedule feature), so the
existing check did not catch it. Since then, any orphaned-disk proposal
for a resource tagged purpose=disaster-recovery requires the owning team's
explicit written approval regardless of snapshot schedule status.
