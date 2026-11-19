"""Roles & permissions management: /organizations/{id}/roles/ and /permissions/."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.utils import timezone

from django.test import TestCase
from rest_framework.test import APIClient

from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.models import AuditLog
from core.applications.users.models import Invitation
from core.applications.users.models import Membership
from core.applications.users.models import Permission
from core.applications.users.models import Role
from core.applications.users.models import RolePermission
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.helper.enums import PermissionCode as P

CASHIER = ["VIEW_PRODUCTS", "VIEW_SALES", "CREATE_SALE"]


def dashed(pk) -> str:
    """The API returns ids with dashes; the nested routes only accept that form."""
    return str(uuid.UUID(str(pk)))


class RolesApiTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)
        self.owner = self._member("owner")
        self.admin = self._member("administrator")
        self.manager = self._member("manager")
        self.rep = self._member("sales-representative")
        self.base = f"/api/organizations/{dashed(self.org.pk)}"

    def _member(self, slug, role=None):
        user = UserFactory()
        Membership.objects.create(user=user, organization=self.org, role=role or self.roles[slug], is_active=True)
        return user

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _grant(self, slug, *codes):
        for code in codes:
            RolePermission.objects.get_or_create(role=self.roles[slug], permission=Permission.objects.get(code=code))

    def _create(self, user=None, **body):
        body.setdefault("name", "Cashier")
        body.setdefault("permissions", CASHIER)
        return self._client(user or self.owner).post(f"{self.base}/roles/", body, format="json")

    # -- reading ---------------------------------------------------------------
    def test_list_shows_roles_with_permissions_and_member_counts(self):
        response = self._client(self.owner).get(f"{self.base}/roles/")
        self.assertEqual(response.status_code, 200)
        rows = {row["slug"]: row for row in response.json()}
        self.assertEqual(rows["owner"]["member_count"], 1)
        self.assertTrue(rows["owner"]["is_locked"])
        self.assertEqual(set(rows["owner"]["permissions"]), set(P.values))
        self.assertFalse(rows["manager"]["is_locked"])
        self.assertFalse(rows["manager"]["can_rename"])
        self.assertIn("VIEW_TEAM", rows["manager"]["permissions"])

    def test_viewing_roles_needs_view_roles(self):
        self.assertEqual(self._client(self.manager).get(f"{self.base}/roles/").status_code, 403)
        self.assertEqual(self._client(UserFactory()).get(f"{self.base}/roles/").status_code, 404)

    def test_permission_catalog_is_grouped_by_module(self):
        response = self._client(self.owner).get(f"{self.base}/permissions/")
        self.assertEqual(response.status_code, 200)
        groups = response.json()
        self.assertEqual(groups[0]["module"], "dashboard")
        codes = [p["code"] for group in groups for p in group["permissions"]]
        self.assertEqual(sorted(codes), sorted(P.values))

    # -- create ----------------------------------------------------------------
    def test_create_custom_role(self):
        response = self._create(description="Front counter")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["name"], body["slug"], body["is_system"]), ("Cashier", "cashier", False))
        self.assertEqual(body["permissions"], sorted(CASHIER))
        self.assertTrue(body["can_delete"])
        role = Role.objects.get(pk=body["id"])
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.ROLE_CREATED, resource_id=role.pk.hex).exists())

        # Immediately offered in the invite form.
        assignable = self._client(self.owner).get(f"{self.base}/invitations/assignable-roles/").json()
        self.assertIn("Cashier", [role["name"] for role in assignable])

    def test_names_are_unique_case_insensitively_and_slugs_never_clash(self):
        self._create()
        response = self._create(name="  cashier ")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "ROLE_NAME_TAKEN")

        role = Role.objects.get(organization=self.org, slug="cashier")
        self._client(self.owner).patch(f"{self.base}/roles/{dashed(role.pk)}/", {"name": "Old cashier"}, format="json")
        self.assertEqual(self._create().json()["slug"], "cashier-2")

    def test_unknown_permission_code_is_rejected(self):
        response = self._create(permissions=["VIEW_EVERYTHING"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "UNKNOWN_PERMISSION")

    def test_creating_needs_manage_roles(self):
        self.assertEqual(self._create(user=self.rep).status_code, 403)

    # -- update ----------------------------------------------------------------
    def test_owner_and_administrator_are_locked(self):
        client = self._client(self.owner)
        for slug in ("owner", "administrator"):
            response = client.patch(f"{self.base}/roles/{dashed(self.roles[slug].pk)}/", {"permissions": []}, format="json")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "ROLE_LOCKED")

    def test_built_in_role_permissions_change_but_name_does_not(self):
        url = f"{self.base}/roles/{dashed(self.roles['sales-representative'].pk)}/"
        client = self._client(self.owner)
        response = client.patch(url, {"permissions": [*CASHIER, "VIEW_TEAM"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["permissions"], sorted([*CASHIER, "VIEW_TEAM"]))
        log = AuditLog.objects.get(action=AuditAction.ROLE_PERMISSIONS_CHANGED)
        self.assertIn("VIEW_TEAM", log.new_values["added"])

        response = client.patch(url, {"name": "Rep"}, format="json")
        self.assertEqual(response.json()["code"], "SYSTEM_ROLE_NOT_RENAMEABLE")

    def test_permission_change_applies_on_the_next_request(self):
        members_url = f"{self.base}/members/"
        self.assertEqual(self._client(self.rep).get(members_url).status_code, 403)
        self._client(self.owner).patch(
            f"{self.base}/roles/{dashed(self.roles['sales-representative'].pk)}/", {"permissions": ["VIEW_TEAM"]},
            format="json",
        )
        self.assertEqual(self._client(self.rep).get(members_url).status_code, 200)

    def test_no_privilege_escalation(self):
        self._grant("manager", P.VIEW_ROLES, P.MANAGE_ROLES)
        # Adding a permission the manager doesn't hold.
        response = self._create(user=self.manager, permissions=[*CASHIER, "MANAGE_BILLING"])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "PERMISSION_ESCALATION")
        self.assertEqual(response.json()["permissions"], ["MANAGE_BILLING"])
        # Within their own permissions: fine.
        self.assertEqual(self._create(user=self.manager).status_code, 201)
        # Removing a permission they don't hold from another role: refused too.
        url = f"{self.base}/roles/{dashed(self.roles['accountant'].pk)}/"
        response = self._client(self.manager).patch(url, {"permissions": []}, format="json")
        self.assertEqual(response.json()["code"], "PERMISSION_ESCALATION")

    def test_unknown_or_foreign_role_is_404(self):
        foreign = seed_default_roles(OrganizationFactory())["manager"]
        response = self._client(self.owner).patch(f"{self.base}/roles/{dashed(foreign.pk)}/", {"permissions": []},
                                                   format="json")
        self.assertEqual(response.status_code, 404)

    # -- delete ----------------------------------------------------------------
    def test_delete_unused_custom_role(self):
        role_id = self._create().json()["id"]
        response = self._client(self.owner).delete(f"{self.base}/roles/{dashed(role_id)}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Role.objects.filter(pk=role_id).exists())
        log = AuditLog.objects.get(action=AuditAction.ROLE_DELETED)
        self.assertEqual(log.previous_values["permissions"], sorted(CASHIER))

    def test_role_in_use_cannot_be_deleted(self):
        role = Role.objects.get(pk=self._create().json()["id"])
        self._member("", role=role)
        response = self._client(self.owner).delete(f"{self.base}/roles/{dashed(role.pk)}/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual((response.json()["code"], response.json()["members"]), ("ROLE_IN_USE", 1))

    def test_role_on_a_pending_invitation_cannot_be_deleted(self):
        role = Role.objects.get(pk=self._create().json()["id"])
        Invitation.objects.create(organization=self.org, email="new@x.test", role=role, token_hash="a" * 64,
                                  invited_by=self.owner, expires_at=timezone.now() + timedelta(days=7))
        response = self._client(self.owner).delete(f"{self.base}/roles/{dashed(role.pk)}/")
        self.assertEqual(response.json()["pending_invitations"], 1)

    def test_built_in_roles_cannot_be_deleted(self):
        response = self._client(self.owner).delete(f"{self.base}/roles/{dashed(self.roles['manager'].pk)}/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "SYSTEM_ROLE_NOT_DELETABLE")

    def test_put_is_not_allowed(self):
        role_id = self._create().json()["id"]
        response = self._client(self.owner).put(f"{self.base}/roles/{dashed(role_id)}/", {"name": "X"}, format="json")
        self.assertEqual(response.status_code, 405)
