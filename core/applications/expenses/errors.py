from __future__ import annotations

from rest_framework import status

from core.helper.custom_exceptions import DomainError


class ExpenseAlreadyVoid(DomainError):
    code = "EXPENSE_ALREADY_VOID"
    default_message = "This expense has already been voided."
    status_code = status.HTTP_409_CONFLICT


class ExpenseNotEditable(DomainError):
    code = "EXPENSE_NOT_EDITABLE"
    default_message = "A voided expense can't be edited."
    status_code = status.HTTP_409_CONFLICT


class ExpenseDateInFuture(DomainError):
    code = "EXPENSE_DATE_IN_FUTURE"
    default_message = "An expense can't be dated in the future."
