from __future__ import annotations

import uuid
from rest_framework import status
from rest_framework.test import APITestCase

from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    UserFactory,
)
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.warehouse.models import Warehouse, StockLocation


class WarehouseAPITest(APITestCase):
    def setUp(self):
        # 1. Organization & Roles
        self.org = OrganizationFactory()
        if hasattr(self.org, "subscription") and self.org.subscription:
            self.org.subscription.plan.max_locations = 10
            self.org.subscription.plan.save(update_fields=["max_locations"])
        self.roles = seed_default_roles(self.org)

        # 2. Owner user (all permissions)
        self.owner = UserFactory()
        self.owner_membership = Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        # 3. Inventory Staff (has VIEW_INVENTORY, but lacks MANAGE_LOCATIONS)
        self.inventory_staff = UserFactory()
        self.staff_membership = Membership.objects.create(
            user=self.inventory_staff,
            organization=self.org,
            role=self.roles["inventory-staff"],
            is_active=True,
        )

        # 4. Sales Representative (lacks VIEW_INVENTORY and MANAGE_LOCATIONS)
        self.sales_rep = UserFactory()
        self.sales_rep_membership = Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=True,
        )

        # 5. Other Org & Other User for Tenant Boundary Isolation
        self.other_org = OrganizationFactory()
        if hasattr(self.other_org, "subscription") and self.other_org.subscription:
            self.other_org.subscription.plan.max_locations = 10
            self.other_org.subscription.plan.save(update_fields=["max_locations"])
        self.other_roles = seed_default_roles(self.other_org)
        self.other_user = UserFactory()
        self.other_membership = Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        # 6. Resources
        self.warehouse = Warehouse.objects.create(
            organization=self.org,
            name="Main Depot",
            code="DEP-01",
            address="123 Industrial Ave",
        )
        self.location = StockLocation.objects.create(
            warehouse=self.warehouse,
            name="Shelf A1",
            description="Top shelf",
        )

        # Authenticate as owner
        self.client.force_authenticate(user=self.owner)
        self.base_url = f"/api/organizations/{self.org.id}/warehouses/"
        self.detail_url = f"{self.base_url}{self.warehouse.id}/"
        self.locations_url = f"/api/organizations/{self.org.id}/locations/"
        self.location_detail_url = f"{self.locations_url}{self.location.id}/"

    def test_list_warehouses(self):
        """Owner can list warehouses scoped to their organization."""
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(str(results[0]["id"]).replace("-", ""), str(self.warehouse.id).replace("-", ""))

    def test_retrieve_warehouse(self):
        """Owner can retrieve warehouse detail."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Main Depot")
        self.assertEqual(response.data["code"], "DEP-01")

    def test_create_warehouse(self):
        """Owner can create a new warehouse in their organization."""
        data = {
            "name": "Secondary Depot",
            "code": "DEP-02",
            "address": "456 Commerce Rd",
        }
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_wh_id = response.data["id"]

        new_wh = Warehouse.objects.get(id=new_wh_id)
        self.assertEqual(str(new_wh.organization_id).replace("-", ""), str(self.org.id).replace("-", ""))
        self.assertEqual(new_wh.code, "DEP-02")

    def test_update_warehouse(self):
        """Owner can update warehouse metadata."""
        data = {"address": "Updated Address 789"}
        response = self.client.patch(self.detail_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.warehouse.refresh_from_db()
        self.assertEqual(self.warehouse.address, "Updated Address 789")

    def test_delete_warehouse(self):
        """Owner can delete a warehouse."""
        temp_wh = Warehouse.objects.create(
            organization=self.org,
            name="Temporary Depot",
            code="TEMP-01",
        )
        url = f"{self.base_url}{temp_wh.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Warehouse.objects.filter(id=temp_wh.id).exists())

    def test_list_stock_locations(self):
        """Owner can list stock locations for their organization."""
        response = self.client.get(self.locations_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(str(results[0]["id"]).replace("-", ""), str(self.location.id).replace("-", ""))

    def test_create_stock_location(self):
        """Owner can create a stock location inside a warehouse belonging to their org."""
        data = {
            "warehouse": str(self.warehouse.id),
            "name": "Bin B2",
            "description": "Small parts bin",
        }
        response = self.client.post(self.locations_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_loc_id = response.data["id"]

        new_loc = StockLocation.objects.get(id=new_loc_id)
        self.assertEqual(str(new_loc.warehouse_id).replace("-", ""), str(self.warehouse.id).replace("-", ""))
        self.assertEqual(new_loc.name, "Bin B2")

    def test_create_stock_location_cross_tenant_warehouse_rejected(self):
        """Cannot link a stock location to a warehouse belonging to another organization."""
        other_wh = Warehouse.objects.create(
            organization=self.other_org,
            name="Other Org Depot",
            code="OTH-01",
        )
        data = {
            "warehouse": str(other_wh.id),
            "name": "Invalid Shelf",
        }
        response = self.client.post(self.locations_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("warehouse", str(response.data))

    # -------------------------------------------------------------------------
    # Granular Role-Based Permission Tests
    # -------------------------------------------------------------------------
    def test_sales_rep_cannot_view_or_manage_warehouses(self):
        """Sales Rep lacks VIEW_INVENTORY and MANAGE_LOCATIONS -> 403 Forbidden."""
        self.client.force_authenticate(user=self.sales_rep)

        list_resp = self.client.get(self.base_url)
        self.assertEqual(list_resp.status_code, status.HTTP_403_FORBIDDEN)

        create_resp = self.client.post(self.base_url, {
            "name": "Unauthorized Depot",
            "code": "UNAUTH-01",
        }, format="json")
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_staff_can_view_but_cannot_create_warehouses(self):
        """Inventory Staff has VIEW_INVENTORY, but lacks MANAGE_LOCATIONS."""
        self.client.force_authenticate(user=self.inventory_staff)

        list_resp = self.client.get(self.base_url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)

        create_resp = self.client.post(self.base_url, {
            "name": "Staff Depot",
            "code": "STAFF-01",
        }, format="json")
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # Multi-Tenant Boundary Isolation Tests
    # -------------------------------------------------------------------------
    def test_tenant_isolation_non_member_access_returns_404(self):
        """User from Org B accessing Org A endpoints receives 404 Not Found."""
        self.client.force_authenticate(user=self.other_user)

        # Org A endpoints return 404 to non-member
        self.assertEqual(self.client.get(self.base_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.locations_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.location_detail_url).status_code, status.HTTP_404_NOT_FOUND)

        post_resp = self.client.post(self.base_url, {
            "name": "Injected Depot",
            "code": "INJ-01",
        }, format="json")
        self.assertEqual(post_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Accessing own Org B endpoint is allowed and does not expose Org A warehouses
        other_base_url = f"/api/organizations/{self.other_org.id}/warehouses/"
        own_list = self.client.get(other_base_url)
        self.assertEqual(own_list.status_code, status.HTTP_200_OK)
        results = own_list.data.get("results", []) if isinstance(own_list.data, dict) else own_list.data
        wh_ids = [str(r["id"]).replace("-", "") for r in results]
        self.assertNotIn(str(self.warehouse.id).replace("-", ""), wh_ids)
