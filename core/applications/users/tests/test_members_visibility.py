"""GET /organizations/{id}/members/: VIEW_TEAM permission, narrowed to the caller's branches."""

from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.models import Permission
from core.applications.users.models import RolePermission
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.enums import PermissionCode


class MembersVisibilityTests(TestCase):
    """
    Okafor Stores: Chidi (Owner, all), Ada (Manager, Lekki), Bola (Sales rep, Lekki),
    Tunde (Manager, Ikeja), Kemi (Sales rep, Ikeja), Femi (Inventory staff, Lekki + Ikeja),
    Grace (Accountant, all locations).
    """

    def setUp(self):
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)
        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK")
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")

        self.chidi = self._member("owner")
        self.ada = self._member("manager", self.lekki)
        self.bola = self._member("sales-representative", self.lekki)
        self.tunde = self._member("manager", self.ikeja)
        self.kemi = self._member("sales-representative", self.ikeja)
        self.femi = self._member("inventory-staff", self.lekki, self.ikeja)
        self.grace = self._member("accountant")  # all_locations=True

        # Someone in another organization never appears.
        other = OrganizationFactory()
        Membership.objects.create(user=UserFactory(), organization=other, role=seed_default_roles(other)["owner"])

    def _member(self, role_slug, *locations):
        user = UserFactory()
        membership = Membership.objects.create(
            user=user, organization=self.org, role=self.roles[role_slug], is_active=True,
            all_locations=not locations,
        )
        for location in locations:
            MembershipLocation.objects.create(membership=membership, location=location)
        return user

    def _members(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(f"/api/organizations/{self.org.pk}/members/")

    def _emails(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return {row["user"] for row in response.json()["results"]}

    def _all(self, *users):
        return {user.email for user in users}

    def test_owner_sees_everyone(self):
        everyone = self._all(self.chidi, self.ada, self.bola, self.tunde, self.kemi, self.femi, self.grace)
        self.assertEqual(self._emails(self._members(self.chidi)), everyone)

    def test_branch_manager_sees_only_their_branch_plus_all_location_members(self):
        self.assertEqual(
            self._emails(self._members(self.ada)),
            self._all(self.chidi, self.ada, self.bola, self.femi, self.grace),
        )
        self.assertEqual(
            self._emails(self._members(self.tunde)),
            self._all(self.chidi, self.tunde, self.kemi, self.femi, self.grace),
        )

    def test_role_without_view_team_is_forbidden(self):
        self.assertEqual(self._members(self.bola).status_code, 403)

    def test_granting_view_team_to_a_role_opens_its_branch_list(self):
        RolePermission.objects.create(
            role=self.roles["sales-representative"],
            permission=Permission.objects.get(code=PermissionCode.VIEW_TEAM),
        )
        self.assertEqual(
            self._emails(self._members(self.bola)),
            self._all(self.chidi, self.ada, self.bola, self.femi, self.grace),
        )

    def test_member_in_two_of_the_viewers_branches_is_listed_once(self):
        RolePermission.objects.create(
            role=self.roles["inventory-staff"], permission=Permission.objects.get(code=PermissionCode.VIEW_TEAM),
        )
        response = self._members(self.femi)  # Femi works at Lekki and Ikeja: sees both teams
        everyone = self._all(self.chidi, self.ada, self.bola, self.tunde, self.kemi, self.femi, self.grace)
        self.assertEqual(self._emails(response), everyone)
        self.assertEqual(response.json()["count"], len(everyone))

    def test_view_team_with_all_locations_sees_everyone(self):
        RolePermission.objects.create(
            role=self.roles["accountant"], permission=Permission.objects.get(code=PermissionCode.VIEW_TEAM),
        )
        self.assertEqual(len(self._emails(self._members(self.grace))), 7)

    def test_viewer_without_branches_still_sees_themselves(self):
        newcomer = UserFactory()
        Membership.objects.create(
            user=newcomer, organization=self.org, role=self.roles["manager"], is_active=True, all_locations=False,
        )
        self.assertEqual(
            self._emails(self._members(newcomer)), self._all(self.chidi, self.grace, newcomer)
        )

    def test_outsider_gets_404(self):
        self.assertEqual(self._members(UserFactory()).status_code, 404)
