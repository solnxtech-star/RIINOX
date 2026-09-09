from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.applications.users.models import AdminProfile, CustomerProfile, MemberProfile, Membership, OwnersProfile
from core.helper.enums import UsersRole


@receiver(post_save, sender=Membership)
def create_profile_on_membership_creation(sender, instance, created, **kwargs):
    """
    Automatically create a related profile (AdminProfile or CustomerProfile)
    when a Membership is created.

    - If role is ADMIN → AdminProfile
    - If role is CLIENT → CustomerProfile
    """
    if created and instance.user:
        _create_profile_for_role(instance.user, instance.role)


@receiver(pre_save, sender=Membership)
def handle_role_change(sender, instance, **kwargs):
    """
    If a Membership's role changes, update the related profiles accordingly.
    Ensures only one profile type exists per user.
    """
    if not instance.pk or not instance.user:
        return  # skip new objects or missing user

    try:
        old_instance = Membership.objects.get(pk=instance.pk)
    except Membership.DoesNotExist:
        return

    # Role changed → clean up old profile & create new one
    if old_instance.role != instance.role:
        user = instance.user

        # Delete old profile if it exists
        if old_instance.role == UsersRole.ADMIN:
            AdminProfile.objects.filter(user=user).delete()
        elif old_instance.role == UsersRole.MEMBER:
            CustomerProfile.objects.filter(user=user).delete()

        # Create new profile for updated role
        _create_profile_for_role(user, instance.role)


def _create_profile_for_role(user, role):
    """
    Internal helper to create the correct profile based on role.
    Uses get_or_create to avoid duplicates.
    """
    if role == UsersRole.OWNER:
        OwnersProfile.objects.get_or_create(user=user)
    elif role == UsersRole.ADMIN:
        AdminProfile.objects.get_or_create(user=user)
    elif role == UsersRole.MEMBER:
        MemberProfile.objects.get_or_create(user=user)
