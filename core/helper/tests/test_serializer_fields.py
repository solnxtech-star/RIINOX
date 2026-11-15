"""Payment method validation shared by sales, supplier payments and invoices (PRD §26)."""

from __future__ import annotations

from django.test import SimpleTestCase

from core.applications.invoice.api.serializers.invoice_serializers import InvoiceConfirmSerializer
from core.applications.purchase.api.serializers import PurchasePaymentCreateSerializer
from core.applications.sales.api.serializers import SalePaymentTenderSerializer
from core.helper.enums import PaymentMethod


class PaymentMethodFieldTests(SimpleTestCase):
    def test_prd_methods_are_accepted(self):
        for method in ("cash", "pos", "card", "online", "bank_transfer"):
            serializer = SalePaymentTenderSerializer(data={"method": method})
            self.assertTrue(serializer.is_valid(), (method, serializer.errors))
            self.assertEqual(serializer.validated_data["method"], method)

    def test_case_insensitive_and_stored_lowercase(self):
        serializer = SalePaymentTenderSerializer(data={"method": "  POS "})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["method"], PaymentMethod.POS)

    def test_unknown_method_is_a_field_error_listing_valid_values(self):
        serializer = SalePaymentTenderSerializer(data={"method": "barter"})
        self.assertFalse(serializer.is_valid())
        message = str(serializer.errors["method"][0])
        self.assertIn("barter", message)
        self.assertIn("`pos`", message)

    def test_supplier_payments_use_the_same_list(self):
        serializer = PurchasePaymentCreateSerializer(data={"method": "card"})
        serializer.is_valid()
        self.assertNotIn("method", serializer.errors)
        serializer = PurchasePaymentCreateSerializer(data={"method": "barter"})
        serializer.is_valid()
        self.assertIn("method", serializer.errors)


class InvoiceConfirmBodyTests(SimpleTestCase):
    def test_empty_body_is_valid(self):
        serializer = InvoiceConfirmSerializer(data={})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.service_kwargs()["upfront_payment"], 0)

    def test_null_payment_is_tolerated(self):
        serializer = InvoiceConfirmSerializer(data={"payment": None})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_payment_is_refused_with_a_clear_message(self):
        serializer = InvoiceConfirmSerializer(data={"payment": {"amount": "100.00", "method": "cash"}})
        self.assertFalse(serializer.is_valid())
        self.assertIn("separate endpoint", str(serializer.errors["payment"][0]))
