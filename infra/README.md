# infra

Terraform for Atlas on AWS (Part 7, from lesson 7.4). Written without an AWS account so far
(decision D33): it's checked offline in CI, and verified on a real account later.

| Path | What it is |
|---|---|
| `bootstrap/` | Run once per account: the S3 bucket that holds Terraform's state (local state). |
| `*.tf` | Atlas's AWS resources; state in that bucket. |
| `tests/` | `terraform test` files, run against a mocked AWS provider. |
| `terraform.tfvars.example` | Copy to `terraform.tfvars` (ignored by git) and fill in. |

```bash
cd infra/bootstrap && terraform init && terraform apply      # once, needs an AWS account
cd infra && terraform init -backend-config="bucket=atlas-terraform-state-<account id>"
cd infra && terraform plan                                     # needs an AWS account
cd infra && terraform init -backend=false && terraform test    # offline, no account
```
