"""Fixed inputs for the synthetic world. Changing any of these changes every generated file."""

SEED = 20260925
TENANT_ID = "00000000-0000-4000-8000-00000000d3a0"
TENANT_NAME = "Demo Tenant"
NAMESPACE = "beyondai-intake-demo"  # uuid5 namespace seed for stable row ids

# Tenant settings stored in tenants.settings and used by later milestones.
TENANT_SETTINGS = {
    "approval_amount_limit_minor": 1_000_000,  # 10,000.00 in the tenant's main currency (USD)
    # A limit is only compared with a total in its own currency (there are no exchange rates).
    "approval_amount_limits_minor": {"EUR": 1_000_000, "GBP": 1_000_000},
    "price_tolerance_pct": 2.0,
    "qty_tolerance_pct": 0.0,
    "auto_approve_cleared": False,
}

# Planted "future date" invoices use this year so they stay in the future for a long time.
FUTURE_YEAR = 2031

# Quality mix of the whole set (playbook §8.1).
QUALITY_MIX = {"clean": 0.5, "scanned": 0.3, "photo": 0.2}
GOLDEN_SIZE = 60
