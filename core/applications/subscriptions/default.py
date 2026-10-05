from decimal import Decimal
from typing import Final

DEFAULT_PLAN_NAME: Final = "Free"

FEATURES: Final = [
    {
        "code": "API_ACCESS",
        "name": "API access",
        "description": "Programmatic access to the organization's data."
    },
    {
        "code": "CUSTOM_TEMPLATES",
        "name": "Custom document templates",
        "description": "Branded invoice and receipt templates."
    },
    {
        "code": "INTEGRATIONS",
        "name": "Integrations",
        "description": "Connect third-party services."
    },
    {
        "code": "SMS_NOTIFICATIONS",
        "name": "SMS notifications",
        "description": "Send alerts and reminders by SMS."
    },
    {
        "code": "PRIORITY_SUPPORT",
        "name": "Priority support",
        "description": "Faster responses from support."
    },
]


DEFAULT_PLAN: Final = {
    "name": DEFAULT_PLAN_NAME,
    "description": "Core business management for small teams getting started.",
    "price": Decimal("0.00"),
    "billing_period_days": 30,
    "trial_period_days": 0,  # 0 -> start_subscription() creates it ACTIVE, not TRIALING
    "max_users": 3,
    "max_products": 100,
    "max_locations": 1,
    "max_transactions_per_month": 200,
    "storage_limit_mb": 500,
    "is_active": True,
    "features": (),  # codes of FEATURES enabled on this plan; paid plans unlock them (PRD §10)
}
