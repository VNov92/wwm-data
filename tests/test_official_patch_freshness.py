"""Tests for cross-source patch freshness and Steam patchnotes tag matching."""

import json
from pathlib import Path
from scrapling.parser import Selector

from crawler.constants import OFFICIAL_NEWS_URL
from crawler.models import RawPatch
from crawler.sources.official_patch import collect, steam_raw_patches

FIXTURES = Path(__file__).parent / "fixtures"


class _HtmlResp(Selector):
    def __init__(self, status: int, html: str, url: str = OFFICIAL_NEWS_URL):
        super().__init__(html, url=url)
        self.status = status


class _JsonResp:
    def __init__(self, status_code: int, data: dict):
        self.status_code, self._data, self.text = status_code, data, json.dumps(data)

    def json(self):
        return self._data


def test_steam_accepts_patchnotes_tag_without_title_keyword():
    payload = {
        "appnews": {
            "newsitems": [{
                "gid": "999",
                "title": "Balance Adjustment Announcement (November 13)",
                "feedname": "steam_community_announcements",
                "contents": "Detailed notes",
                "date": 1794494804,
                "tags": ["patchnotes"],
            }]
        }
    }
    patches = steam_raw_patches(payload)
    assert len(patches) == 1
    assert patches[0].url == "https://store.steampowered.com/news/app/3564740/view/999"


def test_collect_prefers_newer_steam_patch_over_older_official(monkeypatch):
    news_html = (FIXTURES / "official_news.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "official_article.html").read_text(encoding="utf-8")
    steam_payload = json.loads((FIXTURES / "steam_news.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(
        "crawler.sources.official_patch.Fetcher.get",
        lambda url, **kw: _HtmlResp(200, news_html if url == OFFICIAL_NEWS_URL else article_html, url=url),
    )
    monkeypatch.setattr("crawler.sources.official_patch.StealthyFetcher.fetch", lambda *a, **kw: None)
    monkeypatch.setattr("crawler.sources.official_patch.requests.get", lambda *a, **kw: _JsonResp(200, steam_payload))

    raw, results = collect()
    assert isinstance(raw, RawPatch)
    assert raw.url == "https://store.steampowered.com/news/app/3564740/view/1849192144183200"
    assert [(r.tier, r.result) for r in results] == [("html", "ok"), ("steam", "ok")]
