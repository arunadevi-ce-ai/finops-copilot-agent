# POLICY-FIN-15: Storage lifecycle and tiering

## When this applies
Cloud Storage buckets or objects on Standard storage class that have not
been read (not just not written) for more than 90 days, based on object
access logs.

## Required action before proposing a change
Write activity alone is not sufficient to judge staleness — a bucket can
be write-only (e.g. an audit log sink) and never read again except during
an actual incident investigation, which can be a year or more between
reads and is still entirely intentional. Check specifically for read
access, and separately check whether the bucket is tagged
retention-policy=compliance, since compliance-retained data has legally
mandated retention independent of access patterns and should not be
proposed for any tier change without legal/compliance sign-off.

## Approved actions
1. For buckets/objects with no reads in 90+ days and no compliance
   retention tag: propose moving to Nearline storage class. Propose
   Coldline instead if there have also been no reads in 365+ days.
2. Always calculate projected savings using the actual current storage
   volume and the specific class-to-class price difference, not a flat
   percentage estimate — storage tiering savings vary significantly by
   region and by how much data is involved.
3. Note in the proposal that Nearline and Coldline have a minimum storage
   duration and per-access retrieval cost, so a bucket with infrequent-but-
   nonzero reads may cost more after tiering if reads resume — flag this
   tradeoff explicitly rather than presenting tiering as a pure win.

## Precedent
INC-FIN-0019: an analytics archive bucket was moved to Coldline based on
140 days without a read, and then needed for an unplanned audit two weeks
later, incurring a retrieval fee larger than the tiering savings would
have produced over the following six months. The proposal format was
updated afterward to always show the retrieval-cost tradeoff explicitly
rather than only the storage-cost savings, so an approver can judge the
actual risk before approving.
