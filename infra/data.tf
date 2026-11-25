# Where Atlas keeps its data on AWS (lesson 7.5): Postgres, Valkey, the job queue and the
# snapshot bucket. The smallest size of each (D34); none is reachable from the internet.

data "aws_caller_identity" "current" {}

# ---------- Postgres: RDS for PostgreSQL ----------

resource "aws_db_subnet_group" "main" {
  name       = "atlas"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier = "atlas"
  # 17, as locally (pgvector/pgvector:pg17); RDS picks the newest 17.x and applies newer
  # minor versions in the maintenance window. RDS ships pgvector, so migration 0010's
  # CREATE EXTENSION works unchanged.
  engine                     = "postgres"
  engine_version             = "17"
  auto_minor_version_upgrade = true
  instance_class             = "db.t4g.micro"
  allocated_storage          = 20
  storage_type               = "gp3"
  storage_encrypted          = true

  db_name  = "atlas"
  username = "atlas"
  # RDS generates the password and keeps it in Secrets Manager. It never appears in this
  # code or in Terraform's state; Atlas's tasks read it from the secret (lesson 7.6).
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  # One copy in one zone: a standby in a second zone doubles the price, and an outage of
  # a study project costs nothing (D34).
  multi_az = false

  backup_retention_period = 1
  copy_tags_to_snapshot   = true
  # Atlas is destroyed after every study session (D34); the data survives as a final
  # snapshot, and the next session restores it with `-var database_snapshot=<its name>`.
  # The name has the creation time in it, because two snapshots can't share a name.
  skip_final_snapshot       = false
  final_snapshot_identifier = "atlas-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  snapshot_identifier       = var.database_snapshot

  lifecycle {
    # timestamp() changes on every plan: without this, every plan would show a change.
    # snapshot_identifier matters only when the database is created.
    ignore_changes = [final_snapshot_identifier, snapshot_identifier]
  }
}

# ---------- Valkey: ElastiCache, for the cache, rate limits and event stream ----------

resource "aws_elasticache_subnet_group" "main" {
  name       = "atlas"
  subnet_ids = aws_subnet.private[*].id
}

# A "replication group" of one node: the resource AWS documents for encrypted Valkey, and
# the one that can add a replica later without replacing the cache. Traffic is encrypted,
# so Atlas connects with a rediss:// URL (TLS) instead of redis://.
resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "atlas"
  description          = "Atlas's cache, rate limits and event stream"
  engine               = "valkey"
  engine_version       = "8.2"
  node_type            = "cache.t4g.micro"
  num_cache_clusters   = 1

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.cache.id]

  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  # Everything here can be rebuilt (cached responses, rate-limit windows, notifications
  # already sent), so it isn't backed up.
  snapshot_retention_limit = 0
  apply_immediately        = true
}

# ---------- The job queue: SQS ----------

resource "aws_sqs_queue" "jobs" {
  name = "atlas-jobs"
  # Five minutes, as RedisQueue's default: a received job that isn't acknowledged within it
  # is delivered again, to another worker if need be (lesson 5.1).
  visibility_timeout_seconds = 300
  sqs_managed_sse_enabled    = true
  # No dead-letter queue: attempts and the last error of each job are in Postgres's jobs
  # table, which stops retrying a job that keeps failing (lesson 5.1). Not covered: a job
  # that kills the worker itself never reaches that code; lesson 7.5's stretch adds one.
}

# ---------- Repository snapshots: S3 ----------

resource "aws_s3_bucket" "snapshots" {
  # Bucket names are global across all of AWS; the account id keeps this one unique.
  bucket = "atlas-snapshots-${data.aws_caller_identity.current.account_id}"
  # Destroyed with everything else after a study session (D34): snapshots are rebuilt
  # from the repositories at the next sync.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "snapshots" {
  bucket = aws_s3_bucket.snapshots.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# AWS's own keys, not a customer-managed KMS key ($1 a month, plus requests); the same
# trade-off as the state bucket (lesson 7.4).
#trivy:ignore:AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "snapshots" {
  bucket = aws_s3_bucket.snapshots.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
