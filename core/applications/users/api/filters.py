from core.helper.enums import InvitationStatus

# Query-param value -> queryset narrowing. "expired" is derived from expires_at,
# not a stored status.
INVITATION_STATUS_FILTERS = {
    "pending": lambda qs: qs.pending(),
    "expired": lambda qs: qs.expired(),
    "accepted": lambda qs: qs.filter(status=InvitationStatus.ACCEPTED),
    "revoked": lambda qs: qs.filter(status=InvitationStatus.REVOKED),
}
