from dataclasses import dataclass
from typing import Any, Optional
from django.contrib.contenttypes.models import ContentType
from core.applications.transactions.models import Transaction

@dataclass
class TransactionData:
    transaction_id: str
    organization: Any
    transaction_type: str
    status: str
    content_type: ContentType
    object_id: int
    created_by: Optional[Any] = None
    notes: Optional[str] = None

def create_transaction(data: TransactionData) -> Transaction:
    """
    Centralized service method to create a unified business event Transaction.
    """
    return Transaction.objects.create(
        transaction_id=data.transaction_id,
        organization=data.organization,
        transaction_type=data.transaction_type,
        status=data.status,
        content_type=data.content_type,
        object_id=data.object_id,
        created_by=data.created_by,
        notes=data.notes
    )
