# The rules lesson 7.5 is about, checked against a mocked AWS: what can reach the data,
# and what happens to it when Atlas is torn down.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  alert_email = "alerts@example.com"
}

run "the_data_lives_in_private_subnets_with_no_route_to_the_internet" {
  command = apply

  assert {
    condition     = aws_db_instance.main.publicly_accessible == false
    error_message = "The database must not get a public address."
  }

  assert {
    condition     = toset(aws_db_subnet_group.main.subnet_ids) == toset(aws_subnet.private[*].id)
    error_message = "The database must be in the private subnets only."
  }

  assert {
    condition     = toset(aws_elasticache_subnet_group.main.subnet_ids) == toset(aws_subnet.private[*].id)
    error_message = "Valkey must be in the private subnets only."
  }

  assert {
    condition     = length([for route in aws_route_table.private.route : route if route.cidr_block == "0.0.0.0/0"]) == 0
    error_message = "A private subnet with a route to the internet isn't private."
  }

  assert {
    condition     = alltrue([for subnet in aws_subnet.public : subnet.map_public_ip_on_launch == false])
    error_message = "Nothing should get a public IP without asking for one."
  }
}

run "only_atlas_tasks_can_reach_postgres_and_valkey" {
  command = apply

  assert {
    condition     = aws_vpc_security_group_ingress_rule.database_from_app.referenced_security_group_id == aws_security_group.app.id
    error_message = "Postgres must admit Atlas's tasks, by security group, not by address."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.database_from_app.from_port == 5432 && aws_vpc_security_group_ingress_rule.database_from_app.to_port == 5432
    error_message = "Postgres must admit its own port only."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.cache_from_app.referenced_security_group_id == aws_security_group.app.id
    error_message = "Valkey must admit Atlas's tasks, by security group, not by address."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.cache_from_app.from_port == 6379 && aws_vpc_security_group_ingress_rule.cache_from_app.to_port == 6379
    error_message = "Valkey must admit its own port only."
  }
}

run "data_is_encrypted_and_the_password_never_enters_terraform" {
  command = apply

  assert {
    condition     = aws_db_instance.main.storage_encrypted && aws_db_instance.main.manage_master_user_password
    error_message = "The database must be encrypted, with RDS keeping its password in Secrets Manager."
  }

  assert {
    condition     = aws_elasticache_replication_group.main.transit_encryption_enabled && aws_elasticache_replication_group.main.at_rest_encryption_enabled
    error_message = "Valkey must encrypt traffic and storage."
  }

  assert {
    condition     = startswith(output.cache_url, "rediss://")
    error_message = "Atlas must connect to Valkey over TLS (rediss://)."
  }
}

run "tearing_down_keeps_the_data_as_a_snapshot" {
  command = apply

  assert {
    condition     = aws_db_instance.main.skip_final_snapshot == false && startswith(aws_db_instance.main.final_snapshot_identifier, "atlas-final-")
    error_message = "Destroying Atlas must leave a final snapshot of the database (D34)."
  }
}

run "the_next_session_restores_the_snapshot" {
  # A state of its own: the restore happens when the database is created, after the last
  # session's `terraform destroy` emptied the state. In the state the runs above share, the
  # database already exists, and ignore_changes keeps it as it is.
  state_key = "next_session"
  command   = plan

  variables {
    database_snapshot = "atlas-final-20261125190000"
  }

  assert {
    condition     = aws_db_instance.main.snapshot_identifier == "atlas-final-20261125190000"
    error_message = "database_snapshot must be the snapshot the database is created from."
  }
}

run "the_queue_hides_a_job_as_long_as_redis_queue_does" {
  command = apply

  assert {
    condition     = aws_sqs_queue.jobs.visibility_timeout_seconds == 300
    error_message = "SQS's visibility timeout must match RedisQueue's (300 seconds), or jobs behave differently on AWS."
  }
}
