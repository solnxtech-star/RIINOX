from decimal import Decimal
from rest_framework import status
from rest_framework.test import APITestCase

from core.applications.inventory.models import Inventory
from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    ProductFactory,
    UserFactory,
    WarehouseFactory,
)
from core.applications.invoice.models import PurchaseInvoice
from core.applications.payment.models import PurchasePayment
from core.applications.products.models import ProductBatch
from core.applications.purchase.models import Purchase, PurchaseItem, PurchaseReturn
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.vendors.models import Vendor
from core.helper.enums import (
    InvoiceStatusChoices,
    PaymentMethod,
    PurchasePaymentStatusChoices,
    PurchaseStatusChoices,
    SubscriptionStatus,
)


class PurchaseAPITest(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)

        self.owner = UserFactory()
        self.owner_membership = Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        self.inventory_staff = UserFactory()
        self.staff_membership = Membership.objects.create(
            user=self.inventory_staff,
            organization=self.org,
            role=self.roles["inventory-staff"],
            is_active=True,
        )

        self.sales_rep = UserFactory()
        self.sales_rep_membership = Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=True,
        )

        self.other_org = OrganizationFactory()
        self.other_roles = seed_default_roles(self.other_org)
        self.other_user = UserFactory()
        self.other_membership = Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        self.warehouse = WarehouseFactory(organization=self.org)
        self.product = ProductFactory(organization=self.org)
        self.vendor = Vendor.objects.create(organization=self.org, company_name="Test Vendor")

        self.purchase = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.warehouse,
            ordered_by=self.owner,
            other_costs=5000,
            discount=1000,
        )
        self.purchase_item = PurchaseItem.objects.create(
            purchase=self.purchase,
            product=self.product,
            quantity_ordered=100,
            purchase_cost=10.0,
        )

        self.client.force_authenticate(user=self.owner)
        self.base_url = f"/api/organizations/{self.org.id}/purchases/"
        self.detail_url = f"/api/organizations/{self.org.id}/purchases/{self.purchase.id}/"

    def test_list_purchases(self):
        """Owner can list purchases scoped to their organization."""
        url = self.base_url
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(str(results[0]["id"]).replace("-", ""), str(self.purchase.id).replace("-", ""))

    def test_create_purchase_with_items_round_trip(self):
        """
        Verify creating a Purchase Order WITH nested items (per serializer standard).
        Performs Round-Trip check: POST -> verify DB -> GET detail endpoint.
        """
        url = self.base_url
        product2 = ProductFactory(organization=self.org)
        data = {
            "organization": str(self.org.id),
            "vendor": str(self.vendor.id),
            "warehouse": str(self.warehouse.id),
            "delivery_method": "Supplier Delivery",
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity_ordered": 50,
                    "purchase_cost": "15.00",
                },
                {
                    "product": str(product2.id),
                    "quantity_ordered": 20,
                    "purchase_cost": "30.00",
                },
            ],
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        purchase_id = response.data["id"]

        created_purchase = Purchase.objects.get(id=purchase_id)
        self.assertEqual(created_purchase.items.count(), 2)
        item1 = created_purchase.items.get(product=self.product)
        self.assertEqual(item1.quantity_ordered, 50)
        self.assertEqual(item1.purchase_cost, Decimal("15.00"))

        get_response = self.client.get(f"/api/organizations/{self.org.id}/purchases/{purchase_id}/")
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_response.data["items"]), 2)
        self.assertEqual(get_response.data["status"], PurchaseStatusChoices.DRAFT)

    def test_receive_purchase_transitions_status(self):
        """
        Integration test verifying goods receipt:
        - Updates PurchaseItem.quantity_received
        - Transitions Purchase.status from draft -> partially_received -> received
        """
        url = f"{self.detail_url}receive/"
        item_uuid = str(self.purchase_item.id)

        data = {
            "items_received": [
                {
                    "item_id": item_uuid,
                    "quantity_received": 50,
                    "batch_number": "BATCH-API-001",
                    "expiry_date": "2028-06-30",
                }
            ]
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["purchase_status"], PurchaseStatusChoices.PARTIALLY_RECEIVED)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatusChoices.PARTIALLY_RECEIVED)
        self.assertEqual(self.purchase.received_by, self.owner)
        self.assertTrue(
            ProductBatch.objects.filter(product=self.product, batch_number="BATCH-API-001").exists()
        )

        response2 = self.client.post(url, data, format="json")
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.data["purchase_status"], PurchaseStatusChoices.RECEIVED)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatusChoices.RECEIVED)

    def test_receive_purchase_over_receiving_rejected(self):
        """Over-receiving goods beyond quantity_ordered must fail with HTTP 400."""
        url = f"{self.detail_url}receive/"
        data = {
            "items_received": [
                {
                    "item_id": str(self.purchase_item.id),
                    "quantity_received": 150,
                }
            ]
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "RECEIVE_ERROR")

    def test_cannot_delete_received_purchase(self):
        """Orders with received items cannot be hard-deleted (PRD §18)."""
        self.purchase_item.quantity_received = 10
        self.purchase_item.save(update_fields=["quantity_received"])

        url = self.detail_url
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "CANNOT_DELETE_RECEIVED_PURCHASE")
        self.assertTrue(Purchase.objects.filter(id=self.purchase.id).exists())

    def test_receive_purchase_invalid_uuid(self):
        """Test rejection when passing an invalid UUID string."""
        url = f"{self.detail_url}receive/"
        data = {
            "items_received": [
                {
                    "item_id": "not-a-valid-uuid-1234",
                    "quantity_received": 50,
                }
            ]
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_summary_and_export_endpoints(self):
        summary_resp = self.client.get(f"{self.base_url}summary/")
        self.assertEqual(summary_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(summary_resp.data["total_purchase_orders"], 1)
        self.assertEqual(summary_resp.data["pending_delivery_count"], 1)

        export_resp = self.client.get(f"{self.base_url}export/")
        self.assertEqual(export_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(export_resp["Content-Type"], "text/csv")
        self.assertIn(self.purchase.purchase_number, export_resp.content.decode("utf-8"))

    def test_cancel_purchase_endpoint(self):
        cancel_resp = self.client.post(
            f"{self.detail_url}cancel/",
            {"reason": "Ordered by mistake"},
            format="json",
        )
        self.assertEqual(cancel_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(cancel_resp.data["status"], PurchaseStatusChoices.CANCELLED)
        self.assertEqual(cancel_resp.data["cancel_reason"], "Ordered by mistake")

    def test_purchase_invoice_payments_returns_and_stock_activity_endpoints(self):
        # 1. Receive 60 units
        receive_resp = self.client.post(
            f"{self.detail_url}receive/",
            {
                "items_received": [
                    {
                        "item_id": str(self.purchase_item.id),
                        "quantity_received": 60,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(receive_resp.status_code, status.HTTP_200_OK)

        # 2. Record Supplier Invoice (PurchaseInvoice)
        inv_resp = self.client.post(
            f"{self.detail_url}purchase-invoice/",
            {
                "vendor_invoice_number": "VEN-INV-881",
                "billed_to": "RIINOX Operations",
                "invoice_type": "supplier_invoice",
                "issue_date": "2026-10-15",
                "due_date": "2026-11-15",
                "tax_rate": "5.00",
                "notes": "Net 30 supplier terms",
                "items": [
                    {
                        "purchase_item_id": str(self.purchase_item.id),
                        "invoice_quantity": 60,
                        "unit_cost": "10.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(inv_resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(inv_resp.data["invoice_number"].startswith("PINV-"))
        self.assertEqual(inv_resp.data["status"], InvoiceStatusChoices.CONFIRMED)
        self.assertEqual(PurchaseInvoice.objects.filter(purchase=self.purchase).count(), 1)

        list_inv_resp = self.client.get(f"{self.detail_url}purchase-invoice/")
        self.assertEqual(list_inv_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_inv_resp.data), 1)

        # 3. Record Supplier Payment
        pay_resp = self.client.post(
            f"{self.detail_url}payments/",
            {
                "amount": "1000.00",
                "method": PaymentMethod.BANK_TRANSFER,
                "payment_date": "2026-10-16",
                "notes": "Partial wire transfer",
            },
            format="json",
        )
        self.assertEqual(pay_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(pay_resp.data["amount"]), Decimal("1000.00"))
        self.assertEqual(PurchasePayment.objects.filter(purchase=self.purchase).count(), 1)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, PurchasePaymentStatusChoices.PARTIALLY_PAID)

        list_pay_resp = self.client.get(f"{self.detail_url}payments/")
        self.assertEqual(list_pay_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_pay_resp.data), 1)

        # 4. Record Supplier Return
        ret_resp = self.client.post(
            f"{self.detail_url}returns/",
            {
                "reason": "10 units damaged in transit",
                "items": [
                    {
                        "purchase_item": str(self.purchase_item.id),
                        "quantity": 10,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(ret_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(ret_resp.data["total_refund_amount"]), Decimal("100.00"))
        self.assertEqual(PurchaseReturn.objects.filter(purchase=self.purchase).count(), 1)

        inv_stock = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv_stock.quantity, 50)

        list_ret_resp = self.client.get(f"{self.detail_url}returns/")
        self.assertEqual(list_ret_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_ret_resp.data), 1)

        # 5. Stock Activity tab
        activity_resp = self.client.get(f"{self.detail_url}stock-activity/")
        self.assertEqual(activity_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(activity_resp.data), 2)  # +60 receipt and -10 return

    def test_sales_rep_cannot_view_or_create_purchases(self):
        """Sales Rep lacks VIEW_PURCHASES and CREATE_PURCHASE -> 403 Forbidden."""
        self.client.force_authenticate(user=self.sales_rep)

        list_resp = self.client.get(self.base_url)
        self.assertEqual(list_resp.status_code, status.HTTP_403_FORBIDDEN)

        create_resp = self.client.post(
            self.base_url,
            {
                "organization": str(self.org.id),
                "vendor": str(self.vendor.id),
                "warehouse": str(self.warehouse.id),
                "items": [],
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_staff_can_view_and_receive_but_cannot_create(self):
        """Inventory Staff has VIEW_PURCHASES and RECEIVE_GOODS, but lacks CREATE_PURCHASE."""
        self.client.force_authenticate(user=self.inventory_staff)

        list_resp = self.client.get(self.base_url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)

        create_resp = self.client.post(
            self.base_url,
            {
                "organization": str(self.org.id),
                "vendor": str(self.vendor.id),
                "warehouse": str(self.warehouse.id),
                "items": [],
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Inventory staff can view payments/returns/purchase-invoice (GET) but cannot POST
        get_pay_resp = self.client.get(f"{self.detail_url}payments/")
        self.assertEqual(get_pay_resp.status_code, status.HTTP_200_OK)
        post_pay_resp = self.client.post(
            f"{self.detail_url}payments/",
            {"method": PaymentMethod.CASH},
            format="json",
        )
        self.assertEqual(post_pay_resp.status_code, status.HTTP_403_FORBIDDEN)

        receive_resp = self.client.post(
            f"{self.detail_url}receive/",
            {
                "items_received": [
                    {
                        "item_id": str(self.purchase_item.id),
                        "quantity_received": 10,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(receive_resp.status_code, status.HTTP_200_OK)

    def test_tenant_isolation_cross_tenant_access_denied(self):
        """User from Org B cannot retrieve, update, delete, or receive goods on Org A purchase."""
        self.client.force_authenticate(user=self.other_user)

        list_resp = self.client.get(self.base_url)
        self.assertEqual(list_resp.status_code, status.HTTP_404_NOT_FOUND)

        get_resp = self.client.get(self.detail_url)
        self.assertEqual(get_resp.status_code, status.HTTP_404_NOT_FOUND)

        receive_resp = self.client.post(
            f"{self.detail_url}receive/",
            {"items_received": [{"item_id": str(self.purchase_item.id), "quantity_received": 10}]},
            format="json",
        )
        self.assertEqual(receive_resp.status_code, status.HTTP_404_NOT_FOUND)

        create_resp = self.client.post(
            self.base_url,
            {
                "organization": str(self.org.id),
                "vendor": str(self.vendor.id),
                "warehouse": str(self.warehouse.id),
                "items": [],
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_404_NOT_FOUND)

        other_base_url = f"/api/organizations/{self.other_org.id}/purchases/"
        own_list_resp = self.client.get(other_base_url)
        self.assertEqual(own_list_resp.status_code, status.HTTP_200_OK)
        results = own_list_resp.data.get("results", []) if isinstance(own_list_resp.data, dict) else own_list_resp.data
        purchase_ids = [str(r["id"]).replace("-", "") for r in results]
        self.assertNotIn(str(self.purchase.id).replace("-", ""), purchase_ids)

    def test_inactive_subscription_blocks_purchase_operations(self):
        """Inactive subscription prevents creating, updating, or receiving purchases."""
        self.client.force_authenticate(user=self.owner)
        self.org.subscription.status = SubscriptionStatus.CANCELED
        self.org.subscription.save(update_fields=["status"])

        create_resp = self.client.post(
            self.base_url,
            {
                "vendor": str(self.vendor.id),
                "warehouse": str(self.warehouse.id),
                "items": [],
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(create_resp.data.get("code"), "SUBSCRIPTION_REQUIRED")

        receive_resp = self.client.post(
            f"{self.detail_url}receive/",
            {"items_received": [{"item_id": str(self.purchase_item.id), "quantity_received": 5}]},
            format="json",
        )
        self.assertEqual(receive_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(receive_resp.data.get("code"), "SUBSCRIPTION_REQUIRED")
