variable "region" {
  description = "AWS region for everything except CloudFront's certificate, which must be in us-east-1 (lesson 7.3)."
  type        = string
  default     = "us-east-1"
}

variable "alert_email" {
  description = "Where budget alerts go. No default: an alert nobody receives is no alert."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost, before credits, that triggers alerts at 50%, 80% and 100%."
  type        = number
  default     = 5

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}
