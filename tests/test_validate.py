from crawler.validate import parse_version, validate_codes, validate_patch


def _mk_codes(n):
    return [{"code": f"CODE{i:04d}"} for i in range(n)]


def test_empty_codes_rejected():
    ok, _ = validate_codes([], [], [])
    assert not ok


def test_large_unexplained_shrink_rejected():
    ok, _ = validate_codes(_mk_codes(3), _mk_codes(10), [])
    assert not ok


def test_shrink_explained_by_expired_is_ok():
    prev = _mk_codes(10)
    expired = [f"CODE{i:04d}" for i in range(7)]
    ok, _ = validate_codes(prev[7:], prev, expired)
    assert ok


def test_invalid_code_format_rejected():
    ok, _ = validate_codes([{"code": "bad code!"}], [], [])
    assert not ok


def test_version_component_over_99_rejected():
    assert parse_version("1.0.12345") is None
    ok, _ = validate_patch({
        "version": "1.0.12345", "releaseDate": "2026-11-20", "title": "Update",
        "summary": "Notes", "sources": [{"title": "Steam", "url": "https://store.steampowered.com/news/app/3564740/view/1"}]
    }, None)
    assert not ok


def test_patch_version_regression_rejected():
    prev = {"version": "2.2"}
    ok, _ = validate_patch({
        "version": "2.1", "releaseDate": "2026-11-20", "title": "Old",
        "summary": "Notes", "sources": [{"title": "Site", "url": "https://www.wherewindsmeetgame.com/news/a.html"}]
    }, prev)
    assert not ok


def test_patch_non_official_source_rejected():
    ok, _ = validate_patch({
        "version": "2.3", "releaseDate": "2026-11-20", "title": "Patch",
        "summary": "Notes", "sources": [{"title": "Blog", "url": "https://random-blog.example/patch"}]
    }, None)
    assert not ok


def test_valid_patch_accepted():
    ok, _ = validate_patch({
        "version": "2.3", "releaseDate": "2026-11-20", "title": "Patch 2.3",
        "summary": "Summary", "sources": [{"title": "Official", "url": "https://www.wherewindsmeetgame.com/news/p23.html"}]
    }, {"version": "2.2"})
    assert ok


def test_patch_title_over_180_rejected():
    ok, _ = validate_patch({
        "version": "2.3", "releaseDate": "2026-11-20", "title": "A" * 181,
        "summary": "Summary", "sources": [{"title": "Official", "url": "https://www.wherewindsmeetgame.com/news/p23.html"}]
    }, None)
    assert not ok


def test_patch_summary_over_1200_rejected():
    ok, _ = validate_patch({
        "version": "2.3", "releaseDate": "2026-11-20", "title": "Patch 2.3",
        "summary": "S" * 1201, "sources": [{"title": "Official", "url": "https://www.wherewindsmeetgame.com/news/p23.html"}]
    }, None)
    assert not ok


def test_patch_source_empty_title_rejected():
    for empty_title in ("", "   ", None):
        ok, _ = validate_patch({
            "version": "2.3", "releaseDate": "2026-11-20", "title": "Patch 2.3",
            "summary": "Summary", "sources": [{"title": empty_title, "url": "https://www.wherewindsmeetgame.com/news/p23.html"}]
        }, None)
        assert not ok
