

from core.applications.notification.audit.context import AuditContext
from core.applications.report.models import AuditLog


def record(
    *, action, organization=None, actor=None, resource=None,
    previous_values=None, new_values=None, reason="", approval_id=None,
    metadata=None, context: AuditContext | None = None,
) -> AuditLog:
    """Call inside the same transaction.atomic() block as the change it describes."""
    context = context or AuditContext()
    return AuditLog.objects.create(
        organization=organization,
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=action,
        resource_type=resource._meta.label_lower if resource is not None else "",
        resource_id=str(resource.pk) if resource is not None else "",
        resource_repr=str(resource)[:255] if resource is not None else "",
        previous_values=previous_values,
        new_values=new_values,
        reason=reason,
        approval_id=approval_id,
        metadata=metadata or {},
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        request_id=context.request_id,
    )
