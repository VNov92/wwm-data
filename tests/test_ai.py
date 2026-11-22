"""Tests for Gemini patch translation and giftcode extraction."""

import json
import pytest
import requests

from crawler.ai import extract_codes, should_translate, translate_patch
from crawler.models import RawPatch

RAW = RawPatch(
    "https://www.wherewindsmeetgame.com/news/p23.html",
    "Patch 2.3",
    "Fixes and new zone.",
    "2026-11-22",
)


class _Resp:
    """Minimal requests.Response stub for Gemini API tests."""

    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_should_translate_skips_same_url():
    prev = {"sources": [{"title": "Old", "url": RAW.url}]}
    assert should_translate(RAW, prev) is False
    assert should_translate(RAW, {"sources": [{"title": "Old", "url": "https://other"}]}) is True
    assert should_translate(RAW, None) is True


def test_translate_falls_back_to_next_model_on_503(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json, timeout))
        if len(calls) == 1:
            return _Resp(503, {"error": {"message": "overloaded"}})
        payload = {
            "version": "2.3",
            "releaseDate": "2026-11-22",
            "title": "Bản 2.3",
            "summary": "Tóm tắt",
            "highlights": ["H1"],
            "sections": [],
            "sources": [],
        }
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": __import__("json").dumps(payload)}]}}]})

    monkeypatch.setattr("crawler.ai.requests.post", fake_post)
    out = translate_patch(RAW, "fake-key")
    assert len(calls) == 2
    assert "gemini-3.5-flash-lite:generateContent" in calls[0][0]
    assert "gemini-3.1-flash-lite:generateContent" in calls[1][0]
    assert calls[0][1]["x-goog-api-key"] == "fake-key"
    assert calls[1][2]["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" in calls[1][2]["generationConfig"]
    assert out["version"] == "2.3"


def test_translate_forces_source(monkeypatch):
    def fake_post(*args, **kwargs):
        payload = {
            "version": "2.3",
            "releaseDate": "2026-11-22",
            "title": "Bản 2.3",
            "summary": "Tóm tắt",
            "highlights": [],
            "sections": [],
            "sources": [{"title": "Wrong", "url": "https://evil.example"}],
        }
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]})

    monkeypatch.setattr("crawler.ai.requests.post", fake_post)
    out = translate_patch(RAW, "fake-key")
    assert out["sources"] == [{"title": RAW.title, "url": RAW.url}]


def test_translate_falls_back_on_network_or_invalid_json(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("network glitch")
        payload = {
            "version": "2.3",
            "releaseDate": "2026-11-22",
            "title": "Bản 2.3",
            "summary": "Tóm tắt",
            "highlights": ["H1"],
            "sections": [{"title": "Sửa lỗi", "items": ["Sửa lỗi bản đồ"]}],
            "sources": [],
        }
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": __import__("json").dumps(payload)}]}}]})

    monkeypatch.setattr("crawler.ai.requests.post", fake_post)
    out = translate_patch(RAW, "fake-key")
    assert len(calls) == 2
    assert out["sections"] == [{"title": "Sửa lỗi", "items": ["Sửa lỗi bản đồ"]}]


def test_extract_codes_normalizes_and_deduplicates(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        if len(calls) == 1:
            return _Resp(200, {"candidates": [{"content": {"parts": [{"text": "not-json"}]}}]})
        return _Resp(
            200,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": __import__("json").dumps(
                                        [" wwmgift2026 ", "BAD!", "AB", "WWMGIFT2026", "ann6jc7qmy3"]
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("crawler.ai.requests.post", fake_post)
    codes = extract_codes("Use code wwmgift2026 or ann6jc7qmy3!", "fake-key")
    assert len(calls) == 2
    assert calls[0][1]["x-goog-api-key"] == "fake-key"
    assert codes == ["WWMGIFT2026", "ANN6JC7QMY3"]
    assert extract_codes("   ", "fake-key") == []


def test_translate_raises_when_all_models_fail(monkeypatch):
    monkeypatch.setattr(
        "crawler.ai.requests.post",
        lambda *a, **kw: _Resp(503, {"error": {"message": "busy"}}),
    )
    with pytest.raises(RuntimeError):
        translate_patch(RAW, "fake-key")


def test_translate_falls_back_when_raw_title_empty(monkeypatch):
    def fake_post(*args, **kwargs):
        payload = {
            "version": "2.3",
            "releaseDate": "2026-11-22",
            "title": "Bản vá 2.3",
            "summary": "Tóm tắt",
            "highlights": [],
            "sections": [],
            "sources": [],
        }
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]})

    monkeypatch.setattr("crawler.ai.requests.post", fake_post)
    raw_blank = RawPatch(RAW.url, "   ", RAW.text, RAW.published_at)
    out = translate_patch(raw_blank, "fake-key")
    assert out["sources"] == [{"title": "Bản vá 2.3", "url": RAW.url}]

    def fake_post_empty_title(*args, **kwargs):
        payload = {
            "version": "2.3",
            "releaseDate": "2026-11-22",
            "title": " ",
            "summary": "Tóm tắt",
            "highlights": [],
            "sections": [],
            "sources": [],
        }
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]})

    monkeypatch.setattr("crawler.ai.requests.post", fake_post_empty_title)
    out_default = translate_patch(raw_blank, "fake-key")
    assert out_default["sources"] == [{"title": "Ghi chú bản vá chính thức", "url": RAW.url}]
