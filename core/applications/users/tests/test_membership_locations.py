from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.models import AuditLog
from core.applications.users.invitation_services import update_membership_locations
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import MembershipRuleViolation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.custom_exceptions import BusinessRuleError


def test_every_invitation_error_code_has_a_spec():
    # fail() looks codes up in _SPECS; a missing entry turns a 4xx into a 500.
    from core.applications.users.errors import _SPECS
    from core.applications.users.errors import InvitationErrorCode

    assert set(InvitationErrorCode) <= set(_SPECS)


class UpdateMembershipLocationsTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)

        self.owner = UserFactory()
        self.owner_membership = self._member(self.owner, "owner")
        self.staff = UserFactory()
        self.staff_membership = self._member(self.staff, "inventory-staff")

        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK")
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")

    def _member(self, user, role_slug):
        return Membership.objects.create(
            user=user, organization=self.org, role=self.roles[role_slug], is_active=True
        )

    def _update(self, membership=None, *, actor=None, all_locations=False, locations=()):
        return update_membership_locations(
            membership=membership or self.staff_membership,
            actor=actor or self.owner,
            all_locations=all_locations,
            locations=list(locations),
        )

    def _linked_codes(self, membership=None) -> set[str]:
        membership = membership or self.staff_membership
        return set(
            MembershipLocation.objects.filter(membership=membership).values_list("location__code", flat=True)
        )

    def test_restricts_member_to_chosen_locations_and_audits(self):
        self._update(locations=[self.lekki])

        self.staff_membership.refresh_from_db()
        self.assertFalse(self.staff_membership.all_locations)
        self.assertEqual(self._linked_codes(), {"LEK"})
        log = AuditLog.objects.get(action=AuditAction.MEMBERSHIP_LOCATIONS_CHANGED)
        self.assertEqual(log.previous_values["all_locations"], True)
        self.assertEqual(log.new_values["all_locations"], False)

    def test_replaces_only_the_difference(self):
        self._update(locations=[self.lekki])
        kept = MembershipLocation.objects.get(membership=self.staff_membership, location=self.lekki)

        self._update(locations=[self.lekki, self.ikeja])
        self.assertEqual(self._linked_codes(), {"LEK", "IKJ"})
        self.assertTrue(MembershipLocation.objects.filter(pk=kept.pk).exists())  # untouched row kept

        self._update(locations=[self.ikeja])
        self.assertEqual(self._linked_codes(), {"IKJ"})

    def test_back_to_all_locations_clears_links(self):
        self._update(locations=[self.lekki])

        self._update(all_locations=True)

        self.staff_membership.refresh_from_db()
        self.assertTrue(self.staff_membership.all_locations)
        self.assertEqual(self._linked_codes(), set())

    def test_no_change_writes_no_audit(self):
        self._update(all_locations=True)  # already all_locations by default

        self.assertFalse(AuditLog.objects.filter(action=AuditAction.MEMBERSHIP_LOCATIONS_CHANGED).exists())

    def test_rejects_location_of_another_organization(self):
        foreign = Warehouse.objects.create(organization=OrganizationFactory(), name="Abuja", code="ABJ")

        with self.assertRaises(BusinessRuleError) as ctx:
            self._update(locations=[foreign])

        self.assertEqual(ctx.exception.code, "INVALID_LOCATION")
        self.assertEqual(self._linked_codes(), set())

    def test_rejects_inactive_location(self):
        Warehouse.objects.filter(pk=self.ikeja.pk).update(is_active=False)

        with self.assertRaises(BusinessRuleError) as ctx:
            self._update(locations=[self.ikeja])

        self.assertEqual(ctx.exception.code, "INVALID_LOCATION")

    def test_administrator_cannot_be_restricted(self):
        admin_membership = self._member(UserFactory(), "administrator")

        with self.assertRaises(BusinessRuleError) as ctx:
            self._update(admin_membership, locations=[self.lekki])

        self.assertEqual(ctx.exception.code, "LOCATION_SCOPE_NOT_ALLOWED")

    def test_cannot_change_own_membership(self):
        with self.assertRaises(MembershipRuleViolation):
            self._update(self.owner_membership, actor=self.owner, locations=[self.lekki])

    def test_api_endpoint_updates_locations(self):
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.put(
            f"/api/memberships/{self.staff_membership.pk}/locations/",
            {"all_locations": False, "locations": [str(self.lekki.pk)]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self._linked_codes(), {"LEK"})
