import uuid
from rest_framework import status
from rest_framework.test import APITestCase

from core.applications.products.models import Brand, Category, Product
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory


class CategoryAndBrandAPITest(APITestCase):

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

        self.manager = UserFactory()
        self.manager_membership = Membership.objects.create(
            user=self.manager,
            organization=self.org,
            role=self.roles["manager"],
            is_active=True,
        )

        self.inventory_manager = UserFactory()
        self.inventory_manager_membership = Membership.objects.create(
            user=self.inventory_manager,
            organization=self.org,
            role=self.roles["inventory-manager"],
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

        self.client.force_authenticate(user=self.owner)

    @property
    def categories_url(self):
        return f"/api/organizations/{self.org.id}/categories/"

    @property
    def brands_url(self):
        return f"/api/organizations/{self.org.id}/brands/"

    def test_owner_can_list_and_create_category(self):
        data = {
            "name": "Pharmaceuticals",
            "description": "Prescription and OTC medicines",
        }
        response = self.client.post(self.categories_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Pharmaceuticals")
        self.assertEqual(str(response.data["organization"]).replace("-", ""), str(self.org.id).replace("-", ""))

        list_resp = self.client.get(self.categories_url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        results = list_resp.data.get("results", list_resp.data)
        self.assertTrue(any(c["name"] == "Pharmaceuticals" for c in results))

    def test_category_uniqueness_within_organization(self):
        Category.objects.create(organization=self.org, name="Antibiotics")

        dup_response = self.client.post(
            self.categories_url,
            {"name": "antibiotics"},
            format="json",
        )
        self.assertEqual(dup_response.status_code, status.HTTP_400_BAD_REQUEST)

        other_url = f"/api/organizations/{self.other_org.id}/categories/"
        self.client.force_authenticate(user=self.other_user)
        other_resp = self.client.post(
            other_url,
            {"name": "Antibiotics"},
            format="json",
        )
        self.assertEqual(other_resp.status_code, status.HTTP_201_CREATED)

    def test_category_update_and_delete(self):
        category = Category.objects.create(organization=self.org, name="Vitamins")
        detail_url = f"{self.categories_url}{category.id}/"

        patch_resp = self.client.patch(detail_url, {"description": "Daily supplements"}, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertEqual(category.description, "Daily supplements")

        del_resp = self.client.delete(detail_url)
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(id=category.id).exists())

    def test_owner_can_list_and_create_brand(self):
        data = {"name": "Pfizer"}
        response = self.client.post(self.brands_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Pfizer")
        self.assertEqual(str(response.data["organization"]).replace("-", ""), str(self.org.id).replace("-", ""))

        list_resp = self.client.get(self.brands_url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        results = list_resp.data.get("results", list_resp.data)
        self.assertTrue(any(b["name"] == "Pfizer" for b in results))

    def test_brand_uniqueness_within_organization(self):
        Brand.objects.create(organization=self.org, name="Novartis")

        dup_response = self.client.post(
            self.brands_url,
            {"name": "novartis"},
            format="json",
        )
        self.assertEqual(dup_response.status_code, status.HTTP_400_BAD_REQUEST)

        other_url = f"/api/organizations/{self.other_org.id}/brands/"
        self.client.force_authenticate(user=self.other_user)
        other_resp = self.client.post(
            other_url,
            {"name": "Novartis"},
            format="json",
        )
        self.assertEqual(other_resp.status_code, status.HTTP_201_CREATED)

    def test_brand_update_and_delete(self):
        brand = Brand.objects.create(organization=self.org, name="GSK")
        detail_url = f"{self.brands_url}{brand.id}/"

        patch_resp = self.client.patch(detail_url, {"name": "GlaxoSmithKline"}, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        brand.refresh_from_db()
        self.assertEqual(brand.name, "GlaxoSmithKline")

        del_resp = self.client.delete(detail_url)
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Brand.objects.filter(id=brand.id).exists())

    def test_sales_rep_rbac_permissions(self):
        category = Category.objects.create(organization=self.org, name="Diagnostics")
        brand = Brand.objects.create(organization=self.org, name="Roche")

        self.client.force_authenticate(user=self.sales_rep)

        self.assertEqual(self.client.get(self.categories_url).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(self.brands_url).status_code, status.HTTP_200_OK)

        post_cat = self.client.post(self.categories_url, {"name": "New Cat"}, format="json")
        self.assertEqual(post_cat.status_code, status.HTTP_403_FORBIDDEN)

        post_brand = self.client.post(self.brands_url, {"name": "New Brand"}, format="json")
        self.assertEqual(post_brand.status_code, status.HTTP_403_FORBIDDEN)

        patch_cat = self.client.patch(f"{self.categories_url}{category.id}/", {"name": "Updated"}, format="json")
        self.assertEqual(patch_cat.status_code, status.HTTP_403_FORBIDDEN)

        del_brand = self.client.delete(f"{self.brands_url}{brand.id}/")
        self.assertEqual(del_brand.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_manager_has_manage_categories_permission(self):
        self.client.force_authenticate(user=self.inventory_manager)

        cat_resp = self.client.post(self.categories_url, {"name": "Inventory Manager Category"}, format="json")
        self.assertEqual(cat_resp.status_code, status.HTTP_201_CREATED)

        brand_resp = self.client.post(self.brands_url, {"name": "Inventory Manager Brand"}, format="json")
        self.assertEqual(brand_resp.status_code, status.HTTP_201_CREATED)

    def test_cross_tenant_isolation(self):
        category = Category.objects.create(organization=self.org, name="Tenant 1 Category")
        brand = Brand.objects.create(organization=self.org, name="Tenant 1 Brand")

        self.client.force_authenticate(user=self.other_user)

        self.assertEqual(self.client.get(self.categories_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.brands_url).status_code, status.HTTP_404_NOT_FOUND)

        detail_cat = f"{self.categories_url}{category.id}/"
        detail_brand = f"{self.brands_url}{brand.id}/"
        self.assertEqual(self.client.get(detail_cat).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(detail_brand).status_code, status.HTTP_404_NOT_FOUND)

    def test_category_and_brand_active_filtering(self):
        Category.objects.create(organization=self.org, name="Active Cat", is_active=True)
        Category.objects.create(organization=self.org, name="Inactive Cat", is_active=False)

        resp_active = self.client.get(f"{self.categories_url}?is_active=true")
        results_active = resp_active.data.get("results", resp_active.data)
        self.assertTrue(all(c["is_active"] is True for c in results_active))

        resp_inactive = self.client.get(f"{self.categories_url}?is_active=false")
        results_inactive = resp_inactive.data.get("results", resp_inactive.data)
        self.assertTrue(all(c["is_active"] is False for c in results_inactive))

    def test_product_creation_referencing_new_category_and_brand(self):
        cat_resp = self.client.post(self.categories_url, {"name": "Antibacterial"}, format="json")
        self.assertEqual(cat_resp.status_code, status.HTTP_201_CREATED)
        category_id = cat_resp.data["id"]

        brand_resp = self.client.post(self.brands_url, {"name": "GSK Pharma"}, format="json")
        self.assertEqual(brand_resp.status_code, status.HTTP_201_CREATED)
        brand_id = brand_resp.data["id"]

        products_url = f"/api/organizations/{self.org.id}/products/"
        product_data = {
            "name": "Amoxicillin 500mg",
            "sku": f"AMX-{uuid.uuid4().hex[:6]}",
            "category": category_id,
            "brand": brand_id,
            "customer_sale_price": "1500.00",
            "purchase_cost": "1000.00",
        }
        prod_resp = self.client.post(products_url, product_data, format="json")
        self.assertEqual(prod_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(prod_resp.data["category"]).replace("-", ""), str(category_id).replace("-", ""))
        self.assertEqual(str(prod_resp.data["brand"]).replace("-", ""), str(brand_id).replace("-", ""))
