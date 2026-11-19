"""Automatic SKUs, read-only opening_stock/visible, and product photos."""

from __future__ import annotations

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient

from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import UserFactory
from core.applications.products.models import Product
from core.applications.products.models import ProductImage
from core.applications.products.services import MAX_IMAGES_PER_PRODUCT
from core.applications.products.services import _sku_prefix
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.warehouse.models import Warehouse

MEDIA_ROOT = tempfile.mkdtemp(prefix="riinox-test-media-")


def image_file(name="photo.png", image_format="PNG", size=(20, 20)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=image_format)
    content_type = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}[image_format]
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


class ProductTestBase(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        roles = seed_default_roles(self.org)
        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=roles["owner"], is_active=True)
        self.rep = UserFactory()
        Membership.objects.create(user=self.rep, organization=self.org, role=roles["sales-representative"],
                                  is_active=True)
        self.warehouse = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK")
        self.base = f"/api/organizations/{self.org.pk}/products/"
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _create(self, **body):
        body.setdefault("name", "Coca-Cola 50cl")
        body.setdefault("customer_sale_price", "300.00")
        return self.client.post(self.base, body, format="json")


class SkuAndReadOnlyFieldTests(ProductTestBase):
    def test_sku_is_generated_when_left_out(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.content)
        sku = response.json()["sku"]
        self.assertRegex(sku, r"^CC5-[A-HJ-NP-Z2-9]{6}$")

    def test_blank_sku_is_generated_too_and_never_repeats(self):
        skus = {self._create(sku="").json()["sku"] for _ in range(5)}
        self.assertEqual(len(skus), 5)

    def test_prefixes(self):
        self.assertEqual(_sku_prefix("Indomie"), "IND")
        self.assertEqual(_sku_prefix("Peak Milk Powder 400g"), "PMP")
        self.assertEqual(_sku_prefix("!!!"), "PRD")

    def test_given_sku_is_kept_and_duplicates_are_a_clean_400(self):
        self.assertEqual(self._create(sku="COKE-50").json()["sku"], "COKE-50")
        response = self._create(sku="coke-50")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")
        self.assertIn("sku", response.json()["errors"])

    def test_sku_cannot_be_emptied_or_duplicated_on_update(self):
        first = self._create(sku="A-1").json()["id"]
        self._create(sku="B-1")
        url = f"{self.base}{first}/"
        self.assertIn("sku", self.client.patch(url, {"sku": ""}, format="json").json()["errors"])
        self.assertIn("sku", self.client.patch(url, {"sku": "b-1"}, format="json").json()["errors"])
        self.assertEqual(self.client.patch(url, {"sku": "A-2"}, format="json").json()["sku"], "A-2")

    def test_opening_stock_comes_from_quantity_in_stock_and_cannot_be_written(self):
        response = self._create(quantity_in_stock=40, warehouse_id=str(self.warehouse.pk), opening_stock=999)
        self.assertEqual(response.status_code, 201, response.content)
        product = Product.objects.get(pk=response.json()["id"])
        self.assertEqual(product.opening_stock, 40)
        self.assertEqual(product.current_stock, 40)

        self.client.patch(f"{self.base}{product.pk}/", {"opening_stock": 5}, format="json")
        product.refresh_from_db()
        self.assertEqual(product.opening_stock, 40)

    def test_visible_is_read_only(self):
        product_id = self._create(visible=False).json()["id"]
        self.assertTrue(Product.objects.get(pk=product_id).visible)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ProductImageTests(ProductTestBase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.product_id = self._create().json()["id"]
        self.images_url = f"{self.base}{self.product_id}/images/"

    def _upload(self, client=None, **extra):
        data = {"image": extra.pop("file", None) or image_file(), **extra}
        return (client or self.client).post(self.images_url, data, format="multipart")

    def test_upload_first_image_becomes_primary_and_shows_on_the_product(self):
        response = self._upload()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["is_primary"])
        self.assertTrue(response.json()["image"].startswith("http"))

        product = self.client.get(f"{self.base}{self.product_id}/").json()
        self.assertEqual(len(product["images"]), 1)
        self.assertEqual(product["primary_image_url"], response.json()["image"])

    def test_new_primary_demotes_old_and_order_follows_sort_order(self):
        first = self._upload().json()
        second = self._upload(is_primary=True).json()
        self.assertTrue(second["is_primary"])
        self.assertEqual(second["sort_order"], 1)
        self.assertFalse(ProductImage.objects.get(pk=first["id"]).is_primary)

        listed = self.client.get(self.images_url).json()
        self.assertEqual([image["id"] for image in listed], [first["id"], second["id"]])

    def test_patch_makes_main_and_refuses_unsetting_main(self):
        first = self._upload().json()
        second = self._upload().json()
        response = self.client.patch(f"{self.images_url}{second['id']}/", {"is_primary": True}, format="json")
        self.assertTrue(response.json()["is_primary"])
        self.assertFalse(ProductImage.objects.get(pk=first["id"]).is_primary)

        response = self.client.patch(f"{self.images_url}{second['id']}/", {"is_primary": False}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_deleting_main_promotes_next(self):
        first = self._upload().json()
        second = self._upload().json()
        self.assertEqual(self.client.delete(f"{self.images_url}{first['id']}/").status_code, 204)
        self.assertTrue(ProductImage.objects.get(pk=second["id"]).is_primary)

    def test_rejects_wrong_format_and_non_images(self):
        response = self._upload(file=image_file("anim.gif", "GIF"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.json()["errors"])
        fake = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        self.assertEqual(self._upload(file=fake).status_code, 400)

    def test_jpeg_is_accepted(self):
        self.assertEqual(self._upload(file=image_file("p.jpg", "JPEG")).status_code, 201)

    def test_limit_per_product(self):
        for _ in range(MAX_IMAGES_PER_PRODUCT):
            self._upload()
        response = self._upload()
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.json()["errors"])

    def test_permissions_and_tenant_isolation(self):
        rep = APIClient()
        rep.force_authenticate(self.rep)
        self.assertEqual(self._upload(client=rep).status_code, 403)  # no EDIT_PRODUCT
        self.assertEqual(rep.get(self.images_url).status_code, 200)  # VIEW_PRODUCTS

        outsider = APIClient()
        outsider.force_authenticate(UserFactory())
        self.assertIn(outsider.get(self.images_url).status_code, (403, 404))

        other_org = OrganizationFactory()
        other_owner = UserFactory()
        Membership.objects.create(user=other_owner, organization=other_org,
                                  role=seed_default_roles(other_org)["owner"], is_active=True)
        foreign = APIClient()
        foreign.force_authenticate(other_owner)
        url = f"/api/organizations/{other_org.pk}/products/{self.product_id}/images/"
        self.assertEqual(foreign.get(url).status_code, 404)
