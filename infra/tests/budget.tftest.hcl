# Runs without an AWS account: mock_provider answers every AWS call with made-up values,
# so these tests check Atlas's own configuration, not AWS's behaviour (lesson 7.4).
mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  alert_email = "alerts@example.com"
}

run "budget_alerts_at_50_80_and_100_percent_before_credits" {
  command = plan

  assert {
    condition     = toset([for n in aws_budgets_budget.monthly.notification : n.threshold]) == toset([50, 80, 100])
    error_message = "The budget must alert at 50%, 80% and 100%."
  }

  assert {
    condition     = aws_budgets_budget.monthly.cost_types[0].include_credit == false
    error_message = "Credits must be excluded, or the budget reads 0 until they run out."
  }

  assert {
    condition     = aws_budgets_budget.monthly.limit_amount == "5.00"
    error_message = "The default budget is USD 5.00 a month."
  }
}

run "an_alert_address_must_be_an_email" {
  command = plan

  variables {
    alert_email = "not-an-email"
  }

  expect_failures = [var.alert_email]
}
