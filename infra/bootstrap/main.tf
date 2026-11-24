# Run once per account, before infra/: the S3 bucket that holds Terraform's state.
# It can't keep its own state in a bucket that doesn't exist yet, so this small module
# keeps a local state file, which is fine for one bucket that rarely changes (lesson 7.4).
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.74.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "state" {
  # Bucket names are global across all of AWS: the account id makes this one unique.
  bucket = "atlas-terraform-state-${data.aws_caller_identity.current.account_id}"
}

# Every write keeps the previous version: a broken state can be rolled back.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypted with keys S3 manages, at no charge. The scanner (AWS-0132) prefers a customer
# managed KMS key, which gives control over rotation and access, for a monthly fee per key
# (AWS KMS documentation). For a learning account's state bucket, that control isn't worth
# spending credits on; the bucket is private and versioned either way (lesson 7.4).
#trivy:ignore:AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# State contains every resource's details, sometimes secrets: never public.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  description = "Pass to infra/: terraform init -backend-config=\"bucket=<this>\""
  value       = aws_s3_bucket.state.bucket
}
