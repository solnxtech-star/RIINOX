class AuditAction:
    """Dotted `domain.event` codes. Each app adds its own here."""
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
    INVITATION_CREATED = "invitation.created"
    INVITATION_RESENT = "invitation.resent"
    INVITATION_REVOKED = "invitation.revoked"
    INVITATION_ACCEPTED = "invitation.accepted"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    MEMBERSHIP_DEACTIVATED = "membership.deactivated"
    MEMBERSHIP_REACTIVATED = "membership.reactivated"
    ROLE_PERMISSIONS_CHANGED = "role.permissions_changed"
    
    # Product Events
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    PRODUCT_ARCHIVED = "product.archived"
    PRODUCT_RESTORED = "product.restored"

    # Inventory Events
    INVENTORY_ADJUSTED = "inventory.adjusted"
    INVENTORY_OPENING_STOCK_SET = "inventory.opening_stock_set"

    # Purchase Events
    PURCHASE_ORDER_CREATED = "purchase_order.created"
    PURCHASE_ORDER_APPROVED = "purchase_order.approved"
    PURCHASE_ORDER_UPDATED = "purchase_order.updated"
    PURCHASE_ORDER_RECEIVED = "purchase_order.received"
    
    # Sales Events
    SALES_ORDER_CREATED = "sales_order.created"
    SALES_ORDER_FULFILLED = "sales_order.fulfilled"
