"""Health status builder for crawler runs."""

from datetime import datetime, timedelta, timezone

from crawler.constants import STALE_HOURS
from crawler.models import TierResult

ISO_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp string, returning None if invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _to_utc(parsed)
    except ValueError:
        return None


def _serialize_source(item: TierResult) -> dict[str, str]:
    """Serialize a TierResult without the raw payload data field."""
    return {
        "name": item.name,
        "tier": item.tier,
        "result": item.result,
        "message": item.message,
    }


def build_health(
    prev: dict | None,
    attempt_at: datetime,
    success: bool,
    results: list[TierResult],
) -> dict:
    """Build the health summary dictionary for a crawler run."""
    attempt_utc = _to_utc(attempt_at)
    attempt_str = attempt_utc.strftime(ISO_UTC_FORMAT)

    if success:
        status = "ok"
        last_success_at: str | None = attempt_str
    else:
        last_success_at = prev.get("last_success_at") if prev else None
        prev_success_dt = _parse_utc(last_success_at)
        if prev_success_dt is not None and (attempt_utc - prev_success_dt) <= timedelta(hours=STALE_HOURS):
            status = "degraded"
        else:
            status = "failing"

    return {
        "status": status,
        "last_attempt_at": attempt_str,
        "last_success_at": last_success_at,
        "sources": [_serialize_source(r) for r in results],
    }
