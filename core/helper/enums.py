from django.db.models import TextChoices

class UsersRole(TextChoices):
    ADMIN = ("Admin", "Admin")
    CLIENT = ("Client", "Client")


class InvoiceStatusChoices(TextChoices):
    PENDING = ("Pending", "Pending")
    PAID = ("Paid", "Paid")
    OVERDUE = ("Overdue", "Overdue")
    
    
class NotificationTypeChoices(TextChoices):
    INVOICE_ISSURED = ("Invoice Issured", "Invoice Issured")
    INVOICE_DUE = ("Invoice Due", "Invoice Due")
    PAYMENT_CONFIRMATION = ("Payment Confirmation", "Payment Confirmation")
    

class NotificationStatusChoices(TextChoices):
    PENDING = ("Pending", "Pending")
    SENT = ("Sent", "Sent")
    FAILED = ("Failed", "Failed")

class ReportTypeChoice(TextChoices):
    REVENUE = ("revenue", "Revenue Summary")
    OUTSTANDING = ("outstanding", "Outstanding Payments")
    CLIENT = ("client", "Client Invoice Summary")
    
    
    
class ActionChoice(TextChoices):
    CREATE = ("create", "Create")
    UPDATE = ("update", "Update")
    DELETE = ("delete", "Delete")
    LOGIN = ("login", "Login")
    LOGOUT = ("logout", "Logout")