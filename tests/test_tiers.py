"""Tests for multi-tier runner and block detection."""

from crawler.tiers import BlockedError, detect_block, run_tiers


def test_stops_at_first_valid_tier():
    called = []

    def t1():
        called.append("api")
        return {"v": 1}

    def t2():
        called.append("html")
        return {"v": 2}

    data, results = run_tiers("src", [("api", t1), ("html", t2)], lambda x: (True, "ok"))
    assert data == {"v": 1}
    assert called == ["api"]
    assert [(r.tier, r.result) for r in results] == [("api", "ok")]


def test_exception_recorded_as_error():
    def t1():
        raise TimeoutError("timed out")

    def t2():
        return {"v": 2}

    data, results = run_tiers("src", [("api", t1), ("html", t2)], lambda x: (True, "ok"))
    assert data == {"v": 2}
    assert results[0].result == "error" and "timed out" in results[0].message
    assert results[1].result == "ok"


def test_blocked_error_recorded_as_blocked():
    def t1():
        raise BlockedError("HTTP 403")

    data, results = run_tiers("src", [("api", t1)], lambda x: (True, "ok"))
    assert data is None
    assert results[0].result == "blocked"


def test_all_fail_returns_none():
    data, results = run_tiers("src", [("api", lambda: None)], lambda x: (True, "ok"))
    assert data is None
    assert results[0].result == "empty"


def test_challenge_page_is_blocked():
    assert detect_block(200, "<title>Just a moment...</title>") is True
    assert detect_block(403, "Forbidden") is True
    assert detect_block(200, '{"active": []}') is False
