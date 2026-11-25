# What Atlas's tasks need to connect (lesson 7.6), and what a person needs to look. No
# secrets: the database password stays in Secrets Manager, never in Terraform's outputs.

output "database_address" {
  value = aws_db_instance.main.address
}

output "database_secret_arn" {
  description = "The Secrets Manager secret that holds the database password."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "cache_url" {
  value = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "snapshot_bucket" {
  value = aws_s3_bucket.snapshots.bucket
}
