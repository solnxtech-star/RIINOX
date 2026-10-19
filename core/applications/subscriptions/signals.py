from django.dispatch import Signal

# Sent after commit for every recorded subscription state change.
# Receivers: notifications (PRD §33), central audit log (PRD §31), analytics.
# kwargs: event (SubscriptionEvent)
subscription_changed = Signal()

# Sent after commit when money moved but the books don't add up (amount mismatch,
# settlement after a plan change). Wire to alerting; these need a human.
# kwargs: kind (str), reference (str), detail (str)
billing_anomaly = Signal()
