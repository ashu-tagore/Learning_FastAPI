provider "aws" {
  region = var.region

  # On every resource that supports tags: what it belongs to, and that Terraform manages
  # it, so nobody edits it by hand in the console (lesson 7.4's drift).
  default_tags {
    tags = {
      Project   = "atlas"
      ManagedBy = "terraform"
    }
  }
}
