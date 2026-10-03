from dataclasses import dataclass

from django.conf import settings


def get_client_ip(request) -> str | None:
    """
    Trust X-Forwarded-For only for as many proxy hops as you actually run
    (settings.TRUSTED_PROXY_COUNT, default 0); otherwise it is client-spoofable.
    """
    hops = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and hops:
        parts = [p.strip() for p in forwarded.split(",")]
        if len(parts) >= hops:
            return parts[-hops]
    return request.META.get("REMOTE_ADDR")


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Who/where an action came from. Built at the HTTP edge, passed into services."""

    ip_address: str | None = None
    user_agent: str = ""
    request_id: str = ""

    @classmethod
    def from_request(cls, request) -> "AuditContext":
        return cls(
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:1000],
            request_id=request.headers.get("X-Request-ID", "")[:64],
        )
