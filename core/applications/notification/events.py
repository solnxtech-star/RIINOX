"""
The notification event catalogue (PRD §33). Code-owned, like permissions: an
organization only stores the settings it changed (NotificationSetting); every
other event uses the defaults below.

`triggered=False` marks events the settings screen can configure but nothing
emits yet, because the feature behind them doesn't exist (sales orders,
recording failed customer payments). The API reports it so the UI can say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

INVENTORY, SALES, SECURITY, OPERATIONS = "inventory", "sales", "security", "operations"
CATEGORY_LABELS = {INVENTORY: "Inventory", SALES: "Sales", SECURITY: "Security", OPERATIONS: "Operations"}

# Option schema: name -> (type, default, minimum, maximum, help)
OptionSpec = tuple[type, Any, Any, Any, str]


@dataclass(frozen=True)
class EventSpec:
    code: str
    category: str
    label: str
    description: str
    default_roles: tuple[str, ...]
    default_email: bool = True
    triggered: bool = True
    options: dict[str, OptionSpec] = field(default_factory=dict)
    # Whether recipients are limited to members who can access the event's branch.
    branch_scoped: bool = True
    # Roles that can't mute this event in their personal preferences (security alerts
    # must always reach the people responsible for the account).
    mandatory_for_roles: tuple[str, ...] = ()
    # Grouping: while a person has an unread notification of this event, new
    # occurrences update it ("12 products are below ...") instead of adding rows.
    # `group_message` is formatted with {count}; `group_link` replaces the item link.
    group_message: str = ""
    group_link: dict | None = None

    @property
    def groupable(self) -> bool:
        return bool(self.group_message)


LOW_STOCK = "low_stock"
OUT_OF_STOCK = "out_of_stock"
STOCK_VARIANCE = "stock_variance"
BATCH_EXPIRY = "batch_expiry"
LARGE_SALE = "large_sale"
FAILED_TRANSACTION = "failed_transaction"
REFUND = "refund"
OVERDUE_PAYMENT = "overdue_payment"
FAILED_LOGIN = "failed_login"
PERMISSION_CHANGE = "permission_change"
PENDING_APPROVAL = "pending_approval"
APPROVAL_DECISION = "approval_decision"
INCOMING_SALES_ORDER = "incoming_sales_order"

EVENTS: dict[str, EventSpec] = {
    spec.code: spec
    for spec in (
        EventSpec(LOW_STOCK, INVENTORY, "Low Stock",
                  "A product's stock at a branch is at or below its minimum stock level.",
                  ("inventory-manager", "manager"),
                  group_message="{count} products are below their reorder level",
                  group_link={"resource": "products", "query": {"low_stock": "true"}}),
        EventSpec(OUT_OF_STOCK, INVENTORY, "Out of Stock",
                  "A product has run out at a branch.", ("inventory-manager", "manager"),
                  group_message="{count} products are out of stock",
                  group_link={"resource": "products", "query": {"low_stock": "true"}}),
        EventSpec(STOCK_VARIANCE, INVENTORY, "Stock Variance",
                  "A physical stock count finished with differences from the system.",
                  ("inventory-manager",), default_email=False),
        EventSpec(BATCH_EXPIRY, INVENTORY, "Batch/Expiry",
                  "Batches with stock on hand are about to expire (checked daily).",
                  ("inventory-manager",),
                  options={"days_before_expiry": (int, 30, 1, 365, "Warn this many days before expiry.")}),
        EventSpec(LARGE_SALE, SALES, "Large Sale",
                  "A sale's total reaches the threshold.", ("owner", "manager"),
                  options={"threshold": (int, 500_000, 1, 10**12, "Sale total (in the business currency) that counts as large.")}),
        EventSpec(FAILED_TRANSACTION, SALES, "Failed Transaction",
                  "A customer payment failed.", ("manager", "accountant"), triggered=False),
        EventSpec(REFUND, SALES, "Refunds",
                  "A sale return / refund was processed.", ("manager",), default_email=False),
        EventSpec(OVERDUE_PAYMENT, SALES, "Overdue Payment",
                  "A customer invoice is past its due date with money still owed (checked daily, "
                  "repeated weekly while unpaid).",
                  ("owner", "manager", "accountant")),
        EventSpec(FAILED_LOGIN, SECURITY, "Failed Logins",
                  "Repeated failed logins on a team member's account.", ("owner", "administrator"),
                  branch_scoped=False, mandatory_for_roles=("owner", "administrator"),
                  options={"attempts": (int, 5, 3, 50, "Failed attempts within 15 minutes before alerting.")}),
        EventSpec(PERMISSION_CHANGE, SECURITY, "Permission Changes",
                  "A team member's role or branch access changed.", ("owner", "administrator"),
                  branch_scoped=False, mandatory_for_roles=("owner", "administrator")),
        EventSpec(PENDING_APPROVAL, OPERATIONS, "Pending Approvals",
                  "A stock adjustment is waiting for approval.", ("owner", "administrator", "inventory-manager"),
                  group_message="{count} requests are waiting for your approval",
                  group_link={"resource": "inventory/adjustments", "query": {}}),
        EventSpec(APPROVAL_DECISION, OPERATIONS, "Approval decisions",
                  "A request was approved or rejected. The requester is always told.", ("inventory-manager",)),
        EventSpec(INCOMING_SALES_ORDER, OPERATIONS, "Incoming Sales Orders",
                  "A new sales order arrived.", ("manager", "sales-manager"), triggered=False),
    )
}
