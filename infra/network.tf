# Atlas's private network on AWS (lesson 7.5, decision D34).
#
#   public subnets   load balancer (7.6) and Atlas's tasks, which reach GitHub directly
#   private subnets  Postgres and Valkey, with no route to or from the internet
#
# Two of each, in two availability zones: RDS and load balancers refuse to be created
# with subnets in only one.

locals {
  availability_zones = ["${var.region}a", "${var.region}b"]
}

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
  # Names like the database's endpoint only resolve inside the VPC with both of these on.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "atlas" }
}

# The VPC's door to the internet. Free; a NAT gateway (about $33 a month) would let private
# subnets out too, which D34 decided Atlas doesn't need.
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "atlas" }
}

resource "aws_subnet" "public" {
  count = length(local.availability_zones)

  vpc_id            = aws_vpc.main.id
  availability_zone = local.availability_zones[count.index]
  cidr_block        = "10.0.${count.index}.0/24"
  # Off: a public IP costs money and exposes whatever gets it. Atlas's tasks ask for one
  # explicitly (7.6); nothing else launched here should get one by accident.
  map_public_ip_on_launch = false

  tags = { Name = "atlas-public-${local.availability_zones[count.index]}" }
}

resource "aws_subnet" "private" {
  count = length(local.availability_zones)

  vpc_id            = aws_vpc.main.id
  availability_zone = local.availability_zones[count.index]
  cidr_block        = "10.0.${count.index + 10}.0/24"

  tags = { Name = "atlas-private-${local.availability_zones[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "atlas-public" }
}

# No routes besides the one every route table has: traffic within the VPC. That's what
# makes these subnets private, so it's written out instead of left to the VPC's default.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "atlas-private" }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Security groups are firewalls around each resource. A group admits nothing until a rule
# says so, and a rule can name another group instead of addresses: "Atlas's tasks", which
# stays true however many tasks run and whatever IPs they get.

resource "aws_security_group" "app" {
  name        = "atlas-app"
  description = "Atlas's API, worker, scheduler and notifier tasks"
  vpc_id      = aws_vpc.main.id
}

# Out to anywhere: the worker clones from GitHub and every process calls AWS APIs (SQS, S3,
# Secrets Manager), all over HTTPS. Narrowing this means VPC endpoints for AWS and a proxy
# for GitHub, which cost more than everything else here together (D34).
#trivy:ignore:AWS-0104
resource "aws_vpc_security_group_egress_rule" "app_to_anywhere" {
  security_group_id = aws_security_group.app.id
  description       = "HTTPS to GitHub and AWS APIs, and the database and cache"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_security_group" "database" {
  name        = "atlas-database"
  description = "Postgres: only Atlas's tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "database_from_app" {
  security_group_id            = aws_security_group.database.id
  description                  = "Postgres from Atlas's tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_security_group" "cache" {
  name        = "atlas-cache"
  description = "Valkey: only Atlas's tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_app" {
  security_group_id            = aws_security_group.cache.id
  description                  = "Valkey from Atlas's tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}
