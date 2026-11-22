"""Tests for atomic JSON storage and main crawler orchestration."""

from datetime import datetime, timezone

from crawler.main import run
from crawler.models import RawPatch, TierResult
from crawler.store import load, save_atomic

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)
OLD_CODES = {
    "schema_version": 1,
    "health": {"last_success_at": "2026-11-22T00:00:00Z", "status": "ok"},
    "items": [{"code": "KEEPME1", "reward_text": None, "added_at": None}],
    "expired": [],
}
OLD_PATCH = {
    "schema_version": 1,
    "health": {"last_success_at": "2026-11-22T00:00:00Z", "status": "ok"},
    "patch": {
        "version": "2.2",
        "releaseDate": "2026-10-03",
        "title": "2.2",
        "summary": "S",
        "highlights": [],
        "sections": [],
        "sources": [{"title": "O", "url": "https://www.wherewindsmeetgame.com/news/22.html"}],
    },
}


def test_codes_failure_keeps_items_updates_health(tmp_path):
    save_atomic(tmp_path / "codes.json", OLD_CODES)
    save_atomic(tmp_path / "patch.json", OLD_PATCH)
    rc = run(
        NOW,
        tmp_path,
        lambda **kw: (None, [TierResult("codes.yar.gg", "api", "blocked", "403")]),
        lambda: (None, [TierResult("official", "html", "error", "500")]),
        api_key=None,
    )
    assert rc == 0
    saved = load(tmp_path / "codes.json")
    assert saved["items"] == OLD_CODES["items"]
    assert saved["health"]["status"] == "degraded"


def test_codes_success_writes_items_and_expired(tmp_path):
    save_atomic(tmp_path / "codes.json", OLD_CODES)
    save_atomic(tmp_path / "patch.json", OLD_PATCH)
    new_data = {"items": [{"code": "NEWCODE1", "reward_text": "Jade", "added_at": None}], "expired": ["KEEPME1"]}
    run(
        NOW,
        tmp_path,
        lambda **kw: (new_data, [TierResult("codes.yar.gg", "api", "ok", "1")]),
        lambda: (None, []),
        api_key=None,
    )
    saved = load(tmp_path / "codes.json")
    assert saved["items"][0]["code"] == "NEWCODE1"
    assert saved["expired"] == ["KEEPME1"]
    assert saved["health"]["status"] == "ok"


def test_patch_same_url_skips_translation(tmp_path, monkeypatch):
    save_atomic(tmp_path / "codes.json", OLD_CODES)
    save_atomic(tmp_path / "patch.json", OLD_PATCH)
    called = False

    def fail_if_called(*a, **kw):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("crawler.main.translate_patch", fail_if_called)
    raw = RawPatch("https://www.wherewindsmeetgame.com/news/22.html", "2.2", "body")
    run(NOW, tmp_path, lambda **kw: (None, []), lambda: (raw, [TierResult("official", "html", "ok", "same")]), api_key="k")
    assert called is False
    assert load(tmp_path / "patch.json")["health"]["status"] == "ok"


def test_output_has_schema_version_1(tmp_path):
    run(NOW, tmp_path, lambda **kw: (None, []), lambda: (None, []), api_key=None)
    assert load(tmp_path / "codes.json")["schema_version"] == 1
    assert load(tmp_path / "patch.json")["schema_version"] == 1


def test_patch_missing_api_key_keeps_old_patch(tmp_path, monkeypatch):
    save_atomic(tmp_path / "codes.json", OLD_CODES)
    save_atomic(tmp_path / "patch.json", OLD_PATCH)
    monkeypatch.setattr("crawler.main.translate_patch", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("called")))
    raw = RawPatch("https://www.wherewindsmeetgame.com/news/23.html", "2.3", "new patch body")
    rc = run(NOW, tmp_path, lambda **kw: (None, []), lambda: (raw, [TierResult("official", "html", "ok", "new")]), api_key="")
    assert rc == 0
    saved = load(tmp_path / "patch.json")
    assert saved["patch"] == OLD_PATCH["patch"]
    assert saved["health"]["status"] == "degraded"
    assert any(s["name"] == "gemini" and s["message"] == "missing GEMINI_API_KEY" for s in saved["health"]["sources"])


def test_patch_invalid_api_key_keeps_old_patch(tmp_path, monkeypatch):
    save_atomic(tmp_path / "codes.json", OLD_CODES)
    save_atomic(tmp_path / "patch.json", OLD_PATCH)

    def raise_auth(*a, **kw):
        raise RuntimeError("Gemini HTTP 401: API key not valid")

    monkeypatch.setattr("crawler.main.translate_patch", raise_auth)
    raw = RawPatch("https://www.wherewindsmeetgame.com/news/23.html", "2.3", "new patch body")
    rc = run(NOW, tmp_path, lambda **kw: (None, []), lambda: (raw, [TierResult("official", "html", "ok", "new")]), api_key="bad")
    assert rc == 0
    saved = load(tmp_path / "patch.json")
    assert saved["patch"] == OLD_PATCH["patch"]
    assert saved["health"]["status"] == "degraded"
    assert any(s["name"] == "gemini" and "401" in s["message"] for s in saved["health"]["sources"])


def test_patch_valid_translation_and_validation_guard(tmp_path, monkeypatch):
    save_atomic(tmp_path / "codes.json", OLD_CODES)
    save_atomic(tmp_path / "patch.json", OLD_PATCH)
    raw = RawPatch("https://www.wherewindsmeetgame.com/news/23.html", "2.3", "new patch body")
    valid_patch = {
        "version": "2.3",
        "releaseDate": "2026-11-22",
        "title": "Bản cập nhật 2.3",
        "summary": "Tóm tắt",
        "highlights": ["Điểm 1"],
        "sections": [],
        "sources": [{"title": "2.3", "url": "https://www.wherewindsmeetgame.com/news/23.html"}],
    }
    monkeypatch.setattr("crawler.main.translate_patch", lambda r, k: valid_patch)
    run(NOW, tmp_path, lambda **kw: (None, []), lambda: (raw, [TierResult("official", "html", "ok", "new")]), api_key="ok")
    saved = load(tmp_path / "patch.json")
    assert saved["patch"]["version"] == "2.3"
    assert saved["health"]["status"] == "ok"

    # Version regression in translated candidate must keep previous patch.
    regressed = {**valid_patch, "version": "1.0"}
    monkeypatch.setattr("crawler.main.translate_patch", lambda r, k: regressed)
    raw_next = RawPatch("https://www.wherewindsmeetgame.com/news/24.html", "1.0", "old")
    run(NOW, tmp_path, lambda **kw: (None, []), lambda: (raw_next, [TierResult("official", "html", "ok", "new")]), api_key="ok")
    saved_after = load(tmp_path / "patch.json")
    assert saved_after["patch"]["version"] == "2.3"
    assert saved_after["health"]["status"] == "degraded"


def test_codes_invalid_payload_keeps_old_items(tmp_path):
    save_atomic(tmp_path / "codes.json", OLD_CODES)
    save_atomic(tmp_path / "patch.json", OLD_PATCH)
    bad_data = {"items": [{"code": "!!"}], "expired": []}
    run(NOW, tmp_path, lambda **kw: (bad_data, [TierResult("codes.yar.gg", "api", "ok", "1")]), lambda: (None, []))
    saved = load(tmp_path / "codes.json")
    assert saved["items"] == OLD_CODES["items"]
    assert saved["health"]["status"] == "degraded"


def test_store_load_missing_or_corrupted_and_atomic_write(tmp_path):
    assert load(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load(bad) is None
    target = tmp_path / "nested" / "out.json"
    save_atomic(target, {"xin_chao": "Việt Nam"})
    assert load(target) == {"xin_chao": "Việt Nam"}
    assert not target.with_name("out.json.tmp").exists()
