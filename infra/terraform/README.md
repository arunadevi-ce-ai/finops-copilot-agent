# Terraform (reference copy)

This is a copy for documentation purposes only — it isn't applied from
here, and running `terraform` in this directory won't work (no
provider config, no other `variable` blocks, no state).

The real, applied Terraform lives in a separate repo,
`infra-copilot-agent`, shared with the Infra Copilot project. That
repo already provisions the GKE cluster, a shared Google service
account (`agent_gsa`), and Infra Copilot's own Workload Identity
binding. `finops_copilot_WI_BQ.tf` is the addition on top of that —
FinOps Copilot's own namespace, its own namespace-scoped Kubernetes
service account, a Workload Identity binding letting that KSA
impersonate the same shared `agent_gsa`, and the three BigQuery tables
`detector.py` reads from when `BIGQUERY_DATASET` is set.

Reused deliberately, not duplicated: the GSA, the GKE cluster itself,
and the WIF-based CI/CD pattern all already exist for Infra Copilot —
this file only adds what FinOps Copilot specifically needs on top of
that, rather than standing up a second, separate set of cloud
identities and permissions for the same account.

`variable.k8s_finops_namespace`, `var.k8s_finops_service_account_name`,
`var.project_id`, and `var.gcp_region` are declared in that other
repo's `variables.tf`, not here — that's why this file can't run on
its own.
