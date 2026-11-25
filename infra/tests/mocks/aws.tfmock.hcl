# What the mocked AWS returns, shared by every test file (`source = "./tests/mocks"`).
#
# A mock invents values for whatever AWS would fill in, and for a list it invents an empty
# one. RDS always returns the secret it made for the password, so the mock must too.
mock_resource "aws_db_instance" {
  defaults = {
    master_user_secret = [{
      secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-example"
      secret_status = "active"
      kms_key_id    = "arn:aws:kms:us-east-1:123456789012:key/example"
    }]
  }
}
