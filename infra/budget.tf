# The budget from lesson 7.3, now in code. On the Free plan AWS doesn't charge, so the
# budget watches the credits being spent: it counts cost before credits are applied.
resource "aws_budgets_budget" "monthly" {
  name         = "atlas-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    # Credits excluded: with them, cost would read 0 until the credits ran out, and the
    # alert would come exactly when it's too late.
    include_credit = false
  }

  dynamic "notification" {
    for_each = toset([50, 80, 100])
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
