from datetime import datetime, timedelta, timezone
from crawler.health import build_health
from crawler.models import TierResult

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)


def test_success_sets_ok_and_success_time():
    h = build_health(None, NOW, True, [TierResult("codes.yar.gg", "api", "ok", "2 codes")])
    assert h["status"] == "ok"
    assert h["last_attempt_at"] == "2026-11-22T06:00:00Z"
    assert h["last_success_at"] == "2026-11-22T06:00:00Z"
    assert h["sources"][0]["tier"] == "api"


def test_fallback_tier_success_is_ok():
    results = [
        TierResult("codes.yar.gg", "api", "blocked", "403"),
        TierResult("codes.yar.gg", "html", "ok", "10 codes"),
    ]
    h = build_health(None, NOW, True, results)
    assert h["status"] == "ok"
    assert len(h["sources"]) == 2


def test_failure_within_24h_degraded():
    prev = {"last_success_at": (NOW - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    h = build_health(prev, NOW, False, [TierResult("codes.yar.gg", "api", "error", "timeout")])
    assert h["status"] == "degraded"
    assert h["last_success_at"] == prev["last_success_at"]


def test_failure_over_24h_failing():
    prev = {"last_success_at": (NOW - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    h = build_health(prev, NOW, False, [])
    assert h["status"] == "failing"


def test_failure_never_succeeded_failing():
    h = build_health(None, NOW, False, [])
    assert h["status"] == "failing"
    assert h["last_success_at"] is None
