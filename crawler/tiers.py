"""Multi-tier fetch orchestrator and challenge/block detector."""

from collections.abc import Callable

from crawler.constants import CHALLENGE_MARKERS
from crawler.models import TierResult

_BLOCKED_STATUS_CODES = (403, 429, 503)


class BlockedError(Exception):
    """Raised when a target blocks or challenges the crawler."""


def detect_block(status: int, body: str) -> bool:
    """Return True when HTTP status or response body indicates a block or challenge."""
    if status in _BLOCKED_STATUS_CODES:
        return True
    lower_body = (body or "").lower()
    return any(marker.lower() in lower_body for marker in CHALLENGE_MARKERS)


def run_tiers(
    name: str,
    tiers: list[tuple[str, Callable[[], object]]],
    validator: Callable[[object], tuple[bool, str]],
) -> tuple[object | None, list[TierResult]]:
    """Run fetch tiers in order, stopping at the first tier that passes validation."""
    results: list[TierResult] = []
    for tier_name, fn in tiers:
        try:
            out = fn()
            if out is None or out == [] or out == {}:
                results.append(
                    TierResult(
                        name=name,
                        tier=tier_name,
                        result="empty",
                        message="empty result",
                    )
                )
                continue
            valid, reason = validator(out)
        except BlockedError as exc:
            results.append(
                TierResult(
                    name=name,
                    tier=tier_name,
                    result="blocked",
                    message=str(exc) or "blocked",
                )
            )
            continue
        except Exception as exc:
            results.append(
                TierResult(
                    name=name,
                    tier=tier_name,
                    result="error",
                    message=str(exc) or type(exc).__name__,
                )
            )
            continue

        if not valid:
            results.append(
                TierResult(
                    name=name,
                    tier=tier_name,
                    result="error",
                    message=reason or "validation failed",
                )
            )
            continue

        results.append(
            TierResult(
                name=name,
                tier=tier_name,
                result="ok",
                message=reason or "ok",
                data=out,
            )
        )
        return out, results

    return None, results
