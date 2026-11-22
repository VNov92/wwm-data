"""Tests for multi-tier codes.yar.gg giftcode source."""

import json
from pathlib import Path
import sys
import types
import pytest
from scrapling.parser import Selector

from crawler.sources.yar_codes import (
    collect,
    extract_codes_from_text,
    parse_codes_html,
    parse_codes_json,
)

FIXTURES = Path(__file__).parent / "fixtures"
_DOWN = lambda *a, **kw: (_ for _ in ()).throw(OSError("down"))


@pytest.fixture(autouse=True)
def _isolate_adaptive_db(tmp_path, monkeypatch):
    monkeypatch.setattr("crawler.sources.yar_codes.ADAPTIVE_DB_PATH", str(tmp_path / "adaptive.db"))


class _FakeResponse(Selector):
    """Minimal Selector-backed response stub for Fetcher/StealthyFetcher mocks."""

    def __init__(self, status: int, html: str, url: str = "https://codes.yar.gg/"):
        super().__init__(html, url=url)
        self.status = status


class _JsonResp:
    """Minimal requests.Response stub for JSON API mocks."""

    def __init__(self, status_code: int, data: dict):
        self.status_code, self._data = status_code, data
        self.text = json.dumps(data)

    def json(self):
        return self._data


def test_parse_codes_json_fixture():
    data = json.loads((FIXTURES / "yar_api.json").read_text(encoding="utf-8"))
    items, expired = parse_codes_json(data)
    assert len(items) > 0
    assert {"code", "reward_text", "added_at"} <= items[0].keys()
    assert items[0]["code"].isupper()
    assert items[0]["reward_text"] is None or isinstance(items[0]["reward_text"], str)
    assert isinstance(expired, list) and len(expired) > 0


def test_parse_codes_html_fixture():
    rendered = (FIXTURES / "yar_rendered.html").read_text(encoding="utf-8")
    codes = [item["code"] for item in parse_codes_html(Selector(rendered, url="https://codes.yar.gg/"))]
    assert codes == ["ANNDDWJWYTQ", "ANN6JC7QMY3"]
    assert all(c.isupper() for c in codes)

    static_html = (FIXTURES / "yar_home.html").read_text(encoding="utf-8")
    assert parse_codes_html(Selector(static_html, url="https://codes.yar.gg/")) == []


def test_extract_codes_from_text():
    sample = "Use redeem code WWM2026GIFT for free Jade! Ignore 123456 and HELLO."
    assert extract_codes_from_text(sample) == ["WWM2026GIFT"]


def test_collect_uses_api_tier_first(monkeypatch):
    payload = json.loads((FIXTURES / "yar_api.json").read_text(encoding="utf-8"))
    monkeypatch.setattr("crawler.sources.yar_codes.requests.get", lambda *a, **kw: _JsonResp(200, payload))
    monkeypatch.setattr(
        "crawler.sources.yar_codes.Fetcher.get",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("html tier should not run")),
    )
    data, results = collect()
    assert data is not None and len(data["items"]) == 161 and len(data["expired"]) == 608
    assert [(r.tier, r.result) for r in results] == [("api", "ok")]


def test_collect_falls_back_through_csr_html_to_dynamic(monkeypatch):
    static_html = (FIXTURES / "yar_home.html").read_text(encoding="utf-8")
    rendered = (FIXTURES / "yar_rendered.html").read_text(encoding="utf-8")
    stealth_calls = []

    def fake_stealth(url, **kwargs):
        stealth_calls.append((url, kwargs))
        return _FakeResponse(200, rendered, url=url)

    monkeypatch.setattr("crawler.sources.yar_codes.requests.get", _DOWN)
    monkeypatch.setattr("crawler.sources.yar_codes.Fetcher.get", lambda url, **kw: _FakeResponse(200, static_html, url=url))
    monkeypatch.setattr("crawler.sources.yar_codes.StealthyFetcher.fetch", fake_stealth)

    data, results = collect()
    assert data is not None and [i["code"] for i in data["items"]] == ["ANNDDWJWYTQ", "ANN6JC7QMY3"]
    assert data["expired"] == []
    assert [(r.tier, r.result) for r in results] == [("api", "error"), ("html", "empty"), ("dynamic", "ok")]
    assert len(stealth_calls) == 1
    assert stealth_calls[0][1].get("network_idle") is True
    assert stealth_calls[0][1].get("wait_selector") == "[data-code]"
    assert "solve_cloudflare" not in stealth_calls[0][1]


def test_collect_text_tier_regex_and_lazy_ai_fallback(monkeypatch):
    monkeypatch.setattr("crawler.sources.yar_codes.requests.get", _DOWN)
    monkeypatch.setattr(
        "crawler.sources.yar_codes.Fetcher.get",
        lambda url, **kw: _FakeResponse(200, "<html><body><p>Redeem code WWMTEXT2026 now</p></body></html>", url=url),
    )
    monkeypatch.setattr("crawler.sources.yar_codes.StealthyFetcher.fetch", _DOWN)
    data, results = collect()
    assert data == {"items": [{"code": "WWMTEXT2026", "reward_text": None, "added_at": None}], "expired": []}
    assert [r.tier for r in results] == ["api", "html", "dynamic", "text"]

    monkeypatch.setattr(
        "crawler.sources.yar_codes.Fetcher.get",
        lambda url, **kw: _FakeResponse(200, "<html><body><p>Fresh drop: WWMAIGIFT9</p></body></html>", url=url),
    )
    monkeypatch.setitem(sys.modules, "crawler.ai", None)
    data_missing_ai, results_missing_ai = collect(api_key="k1")
    assert data_missing_ai is None and results_missing_ai[-1].result == "empty"

    fake_ai = types.ModuleType("crawler.ai")
    fake_ai.extract_codes = lambda text, key: ["WWMAIGIFT9"] if key == "k1" else []
    monkeypatch.setitem(sys.modules, "crawler.ai", fake_ai)
    data_ai, _ = collect(api_key="k1")
    assert data_ai == {"items": [{"code": "WWMAIGIFT9", "reward_text": None, "added_at": None}], "expired": []}


def test_collect_records_blocked_tiers_and_auto_save_behavior(monkeypatch):
    save_calls = []

    class _TrackingResp(_FakeResponse):
        def css(self, selector, identifier="", adaptive=False, auto_save=False, percentage=40):
            save_calls.append((selector, adaptive, auto_save))
            return super().css(selector, identifier=identifier, adaptive=adaptive, auto_save=auto_save, percentage=percentage)

    monkeypatch.setattr("crawler.sources.yar_codes.requests.get", _DOWN)
    monkeypatch.setattr("crawler.sources.yar_codes.Fetcher.get", lambda url, **kw: _TrackingResp(200, "<section id='codes'></section>", url=url))
    monkeypatch.setattr("crawler.sources.yar_codes.StealthyFetcher.fetch", lambda url, **kw: _FakeResponse(403, "<title>Just a moment...</title>", url=url))
    data, results = collect()
    assert data is None
    assert [(r.tier, r.result) for r in results] == [("api", "error"), ("html", "empty"), ("dynamic", "blocked"), ("text", "empty")]
    assert all(not auto_save for _, _, auto_save in save_calls)

    save_calls.clear()
    rendered = (FIXTURES / "yar_rendered.html").read_text(encoding="utf-8")
    monkeypatch.setattr("crawler.sources.yar_codes.Fetcher.get", lambda url, **kw: _TrackingResp(200, rendered, url=url))
    data_ok, results_ok = collect()
    assert data_ok is not None and len(data_ok["items"]) == 2
    assert [(r.tier, r.result) for r in results_ok] == [("api", "error"), ("html", "ok")]
    assert any(auto_save for _, _, auto_save in save_calls)


def test_collect_api_url_fallback_and_dynamic_text_reuse(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            return _JsonResp(403, {"error": "blocked"})
        return _JsonResp(200, {"active": [{"code": "WWMBACKUP26", "addedAt": "2026-11-21"}], "expired": []})

    monkeypatch.setattr("crawler.sources.yar_codes.requests.get", fake_get)
    data, results = collect()
    assert len(calls) == 2 and data["items"][0]["code"] == "WWMBACKUP26"
    assert [(r.tier, r.result) for r in results] == [("api", "ok")]

    monkeypatch.setattr("crawler.sources.yar_codes.requests.get", _DOWN)
    monkeypatch.setattr("crawler.sources.yar_codes.Fetcher.get", lambda url, **kw: _FakeResponse(200, "<p>empty</p>", url=url))
    monkeypatch.setattr(
        "crawler.sources.yar_codes.StealthyFetcher.fetch",
        lambda url, **kw: _FakeResponse(200, "<section><p>Active code WWMSPA999</p></section>", url=url),
    )
    data_dyn, results_dyn = collect()
    assert data_dyn["items"][0]["code"] == "WWMSPA999"
    assert [(r.tier, r.result) for r in results_dyn] == [("api", "error"), ("html", "empty"), ("dynamic", "empty"), ("text", "ok")]
