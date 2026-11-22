"""Tests for official patch collector with Steam fallback."""

import json
from pathlib import Path
import pytest
from scrapling.parser import Selector

from crawler.constants import OFFICIAL_NEWS_URL, STEAM_APP_ID, STEAM_NEWS_API
from crawler.models import RawPatch
from crawler.sources.official_patch import (
    article_to_raw,
    collect,
    find_patch_article,
    steam_raw_patches,
)

FIXTURES = Path(__file__).parent / "fixtures"
_OFFICIAL_PATCH_URL = "https://www.wherewindsmeetgame.com/news/official/1029patchnotes.html"
_DOWN = lambda *a, **kw: (_ for _ in ()).throw(OSError("down"))
_FORBIDDEN_STEAM = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("steam fallback should not run"))


@pytest.fixture(autouse=True)
def _isolate_adaptive_db(tmp_path, monkeypatch):
    monkeypatch.setattr("crawler.sources.official_patch.ADAPTIVE_DB_PATH", str(tmp_path / "adaptive.db"))


class _FakeResponse(Selector):
    """Minimal Selector-backed response stub for Fetcher/StealthyFetcher mocks."""

    def __init__(self, status: int, html: str, url: str = OFFICIAL_NEWS_URL):
        super().__init__(html, url=url)
        self.status = status


class _JsonResp:
    """Minimal requests.Response stub for Steam API mocks."""

    def __init__(self, status_code: int, data: dict):
        self.status_code, self._data = status_code, data
        self.text = json.dumps(data)

    def json(self):
        return self._data


def test_find_patch_article_prefers_patch_notes():
    html = '<a href="/news/event.html">Event</a><a href="/news/official/PatchNotes22.html">Notes</a>'
    assert find_patch_article(html) == "https://www.wherewindsmeetgame.com/news/official/PatchNotes22.html"


def test_find_patch_article_fixture_and_update_fallback():
    news_html = (FIXTURES / "official_news.html").read_text(encoding="utf-8")
    assert find_patch_article(news_html) == _OFFICIAL_PATCH_URL

    update_only = '<ul class="news_list"><li><a href="official/1122update.html">Update</a></li></ul>'
    assert find_patch_article(update_only) == "https://www.wherewindsmeetgame.com/news/official/1122update.html"
    assert find_patch_article('<a href="https://evil.example/patchnotes.html">External</a>') is None
    assert find_patch_article('<a href="/news/event.html">Event</a>') is None


def test_article_to_raw_strips_scripts():
    raw = article_to_raw(
        "https://www.wherewindsmeetgame.com/news/p.html",
        "<html><head><title>Patch 2.2 | Where Winds Meet</title><script>var x=1;</script></head>"
        "<body><p>Bug fixes</p></body></html>",
    )
    assert raw.title == "Patch 2.2"
    assert "var x=1" not in raw.text and "Bug fixes" in raw.text
    assert len(raw.text) <= 12000


def test_article_to_raw_official_fixture_and_truncation():
    article_html = (FIXTURES / "official_article.html").read_text(encoding="utf-8")
    raw = article_to_raw(_OFFICIAL_PATCH_URL, article_html)
    assert isinstance(raw, RawPatch)
    assert raw.title == "Version 2.3 Patch Notes: Optimizations and Bug Fixes (October 29)"
    assert raw.published_at == "2026-10-30"
    assert "October 29, 2026 Fixes and Optimizations" in raw.text
    assert "JOIN THE COMMUNITY" not in raw.text and "PCUrl" not in raw.text
    assert len(raw.text) <= 12000

    long_html = f"<html><head><title>Long | Where Winds Meet</title></head><body><p>{'A' * 15000}</p></body></html>"
    assert len(article_to_raw("https://www.wherewindsmeetgame.com/news/long.html", long_html).text) == 12000


def test_steam_filters_non_patch_posts():
    payload = json.loads((FIXTURES / "steam_news.json").read_text(encoding="utf-8"))
    # Inject one non-announcement and one non-patch announcement to test filtering
    payload["appnews"]["newsitems"] = [
        {"gid": "111", "title": "Cosmetic Bundle Showcase", "feedname": "steam_community_announcements", "contents": "Skins", "date": 1700000000},
        {"gid": "222", "title": "Patch 2.3 Update Notes", "feedname": "steam_community_announcements", "contents": "Details", "date": 1700000100},
        {"gid": "333", "title": "Patch 2.3 External", "feedname": "pcgamer", "contents": "External", "date": 1700000200},
    ]
    patches = steam_raw_patches(payload)
    assert len(patches) == 1
    assert patches[0].url == "https://store.steampowered.com/news/app/3564740/view/222"


def test_steam_raw_patches_real_fixture_strips_bbcode():
    payload = json.loads((FIXTURES / "steam_news.json").read_text(encoding="utf-8"))
    patches = steam_raw_patches(payload)
    assert len(patches) == 8
    assert all(p.url.startswith(f"https://store.steampowered.com/news/app/{STEAM_APP_ID}/view/") for p in patches)
    v23 = next(p for p in patches if p.url.endswith("/1847920302233930"))
    assert "[p]" not in v23.text and "[Autumn Eve Whimsy]" in v23.text
    assert v23.published_at == "2026-10-29" and len(v23.text) <= 12000


def test_collect_uses_official_html_first(monkeypatch):
    news_html = (FIXTURES / "official_news.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "official_article.html").read_text(encoding="utf-8")
    save_calls = []

    class _TrackingResp(_FakeResponse):
        def css(self, selector, identifier="", adaptive=False, auto_save=False, percentage=40):
            save_calls.append((selector, adaptive, auto_save))
            return super().css(selector, identifier=identifier, adaptive=adaptive, auto_save=auto_save, percentage=percentage)

    def fake_get(url, **kwargs):
        return _TrackingResp(200, news_html, url=url) if url == OFFICIAL_NEWS_URL else _FakeResponse(200, article_html, url=url)

    monkeypatch.setattr("crawler.sources.official_patch.Fetcher.get", fake_get)
    monkeypatch.setattr("crawler.sources.official_patch.StealthyFetcher.fetch", _DOWN)
    monkeypatch.setattr("crawler.sources.official_patch.requests.get", _FORBIDDEN_STEAM)

    raw, results = collect()
    assert isinstance(raw, RawPatch) and raw.url == _OFFICIAL_PATCH_URL
    assert [(r.tier, r.result) for r in results] == [("html", "ok")]
    assert any(adaptive and auto_save for _, adaptive, auto_save in save_calls)


def test_collect_falls_back_to_dynamic_before_steam(monkeypatch):
    news_html = (FIXTURES / "official_news.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "official_article.html").read_text(encoding="utf-8")
    stealth_calls = []

    def fake_stealth(url, **kwargs):
        stealth_calls.append((url, kwargs))
        html = news_html if url == OFFICIAL_NEWS_URL else article_html
        return _FakeResponse(200, html, url=url)

    monkeypatch.setattr(
        "crawler.sources.official_patch.Fetcher.get",
        lambda url, **kw: _FakeResponse(403, "<title>Just a moment...</title>", url=url),
    )
    monkeypatch.setattr("crawler.sources.official_patch.StealthyFetcher.fetch", fake_stealth)
    monkeypatch.setattr("crawler.sources.official_patch.requests.get", _FORBIDDEN_STEAM)

    raw, results = collect()
    assert isinstance(raw, RawPatch) and raw.url == _OFFICIAL_PATCH_URL
    assert [(r.tier, r.result) for r in results] == [("html", "blocked"), ("dynamic", "ok")]
    assert len(stealth_calls) == 2
    assert all(kw.get("network_idle") is True and "solve_cloudflare" not in kw for _, kw in stealth_calls)
    assert "selector_config" in stealth_calls[0][1]


def test_collect_falls_back_to_steam_only_when_official_fails(monkeypatch):
    steam_payload = json.loads((FIXTURES / "steam_news.json").read_text(encoding="utf-8"))
    steam_calls = []

    def fake_steam(url, **kwargs):
        steam_calls.append((url, kwargs))
        return _JsonResp(200, steam_payload)

    monkeypatch.setattr(
        "crawler.sources.official_patch.Fetcher.get",
        lambda url, **kw: _FakeResponse(403, "<title>Attention Required</title>", url=url),
    )
    monkeypatch.setattr(
        "crawler.sources.official_patch.StealthyFetcher.fetch",
        lambda url, **kw: _FakeResponse(200, "<html><body><p>No links</p></body></html>", url=url),
    )
    monkeypatch.setattr("crawler.sources.official_patch.requests.get", fake_steam)

    raw, results = collect()
    assert isinstance(raw, RawPatch)
    assert raw.url == "https://store.steampowered.com/news/app/3564740/view/1847920302233930"
    assert [(r.tier, r.result) for r in results] == [("html", "blocked"), ("dynamic", "empty"), ("steam", "ok")]
    assert steam_calls[0][0] == STEAM_NEWS_API and steam_calls[0][1]["params"]["appid"] == STEAM_APP_ID

    img_only = "<html><head><title>Update</title></head><body><div class='artText'><img src='x.jpg'></div></body></html>"
    link_html = '<ul class="news_list"><li><a href="/news/official/1122update.html">U</a></li></ul>'
    monkeypatch.setattr(
        "crawler.sources.official_patch.Fetcher.get",
        lambda url, **kw: _FakeResponse(200, link_html if url == OFFICIAL_NEWS_URL else img_only, url=url),
    )
    monkeypatch.setattr("crawler.sources.official_patch.StealthyFetcher.fetch", _DOWN)
    raw2, results2 = collect()
    assert raw2 is not None and raw2.url.endswith("/1847920302233930")
    assert [(r.tier, r.result) for r in results2] == [("html", "error"), ("dynamic", "error"), ("steam", "ok")]

    monkeypatch.setattr("crawler.sources.official_patch.Fetcher.get", _DOWN)
    monkeypatch.setattr("crawler.sources.official_patch.requests.get", _DOWN)
    raw_none, results_none = collect()
    assert raw_none is None and [(r.tier, r.result) for r in results_none] == [
        ("html", "error"), ("dynamic", "error"), ("steam", "error")
    ]
