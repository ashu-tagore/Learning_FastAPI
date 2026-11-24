# Atlas on AWS (Part 7). Everything Atlas needs on AWS is described here, in code, so it
# can be reviewed in a pull request, created again in minutes, and destroyed after each
# lesson (lesson 7.4).
terraform {
  # 1.10 introduced use_lockfile: state locking in S3 itself, without a DynamoDB table.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Patch releases are fine; a new minor version is a reviewed change to this line.
      version = "~> 6.74.0"
    }
  }

  # The state (Terraform's record of what it created) lives in S3, not on a laptop:
  # anyone, including CI, plans against the same state, and the lock file stops two
  # people applying at once. The bucket comes from infra/bootstrap; its name includes
  # the account id, so it's passed at init time:
  #   terraform init -backend-config="bucket=atlas-terraform-state-<account id>"
  backend "s3" {
    key          = "atlas/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
