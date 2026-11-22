"""Official patch notes collector for wherewindsmeetgame.com with Steam fallback."""

from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from scrapling.fetchers import Fetcher, StealthyFetcher
from scrapling.parser import Selector

from crawler.constants import ADAPTIVE_DB_PATH, OFFICIAL_NEWS_URL, STEAM_APP_ID, STEAM_NEWS_API
from crawler.models import RawPatch, TierResult
from crawler.tiers import BlockedError, detect_block, run_tiers

_PATCH_NOTES_RE = re.compile(r"patch.*note", re.IGNORECASE)
_PATCH_UPDATE_RE = re.compile(r"patch|update", re.IGNORECASE)
_STEAM_TITLE_RE = re.compile(r"patch|update|maintenance notes|balance adjustment|version\s+\d+\.\d+", re.IGNORECASE)
_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*Where Winds Meet.*$", re.IGNORECASE)
_BBCODE_IMG_RE = re.compile(r"\[img\b[^\]]*\].*?\[/img\]", re.IGNORECASE | re.DOTALL)
_BBCODE_TAG_RE = re.compile(r"\[/?(?:p|b|i|u|h[1-6]|url|img|list|quote|code|spoiler|strike|table|tr|td|th|\*)(?:=[^\]]*|\s+[^\]]*)?\]", re.I)
_HTML_TAG_RE = re.compile(r"</?(?:p|br|div|span|ul|ol|li|h[1-6]|a|strong|em|b|i|img|table|tr|td|script|style)\b[^>]*>", re.I)
_WHITESPACE_RE = re.compile(r"\s+")
_OFFICIAL_HOST = "wherewindsmeetgame.com"
_STEAM_FEED = "steam_community_announcements"
_STEAM_COUNT = 10
_MAX_ARTICLE_CHARS = 12000
_HTTP_TIMEOUT = 15


def _clean_text(text: str, limit: int = _MAX_ARTICLE_CHARS) -> str:
    """Collapse whitespace and truncate text to the character limit."""
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]


def _node_text(node) -> str:
    """Extract visible text from a Scrapling node excluding script/style tags."""
    return str(node.get_all_text(separator=" ", strip=True, ignore_tags=("script", "style", "noscript")))


def _normalize_official_url(href: object) -> str | None:
    """Normalize a relative or absolute href to an https URL on wherewindsmeetgame.com."""
    cleaned = href.strip() if isinstance(href, str) else ""
    if not cleaned or cleaned.startswith(("#", "javascript:", "mailto:")):
        return None
    parsed = urlparse(urljoin(OFFICIAL_NEWS_URL, cleaned))
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in ("http", "https") or not (host == _OFFICIAL_HOST or host.endswith(f".{_OFFICIAL_HOST}")):
        return None
    return urlunparse(parsed._replace(scheme="https"))


def _pick_patch_url(anchors) -> str | None:
    """Pick first official URL matching patch-notes, else patch/update."""
    urls = [u for a in anchors if (u := _normalize_official_url(a.attrib.get("href")))]
    return next((u for pat in (_PATCH_NOTES_RE, _PATCH_UPDATE_RE) for u in urls if pat.search(u)), None)


def _find_patch_in_page(page, *, adaptive: bool = False) -> str | None:
    """Find the best patch article URL from a news list Selector/Response."""
    news_anchors = page.css("ul.news_list a[href]", adaptive=True) if adaptive else page.css("ul.news_list a[href]")
    if url := _pick_patch_url(news_anchors):
        # Persist adaptive fingerprint only after confirming a valid patch URL.
        if adaptive:
            page.css("ul.news_list a[href]", adaptive=True, auto_save=True)
        return url
    return _pick_patch_url(page.css("a[href]"))


def find_patch_article(news_html: str) -> str | None:
    """Find the latest patch notes URL from official news HTML."""
    if not isinstance(news_html, str) or not news_html.strip():
        return None
    return _find_patch_in_page(Selector(news_html, url=OFFICIAL_NEWS_URL))


def article_to_raw(url: str, html: str) -> RawPatch:
    """Parse an official news article page into a cleaned RawPatch."""
    if not isinstance(html, str) or not html.strip():
        return RawPatch(url=url, title="", text="", published_at=None)
    page = Selector(html, url=url)
    title_nodes = page.css("h1.title") or page.css("title") or page.css("h1")
    title = _TITLE_SUFFIX_RE.sub("", _clean_text(_node_text(title_nodes[0])) if title_nodes else "").strip()
    date_nodes = page.css(".contentbox .date .txt") or page.css(".date .txt")
    published_at = _clean_text(_node_text(date_nodes[0])) or None if date_nodes else None
    body_nodes = page.css(".artText") or page.css("article") or page.css("main") or page.css("body") or [page]
    return RawPatch(url=url, title=title, text=_clean_text(_node_text(body_nodes[0])), published_at=published_at)


def _clean_steam_contents(raw: object) -> str:
    """Strip Steam BBCode and HTML markup while keeping bracketed game terms."""
    if not isinstance(raw, str) or not raw:
        return ""
    txt = _BBCODE_TAG_RE.sub(" ", _BBCODE_IMG_RE.sub(" ", raw)).replace("\\[", "[")
    if _HTML_TAG_RE.search(txt):
        txt = _node_text(Selector(txt))
    return _clean_text(txt)


def _unix_to_iso_date(ts: object) -> str | None:
    """Convert Unix epoch seconds to YYYY-MM-DD UTC, or None if invalid."""
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return None


def _has_patchnotes_tag(entry: dict) -> bool:
    tags = entry.get("tags")
    return isinstance(tags, list) and any(isinstance(t, str) and t.strip().lower() == "patchnotes" for t in tags)


def _is_newer_date(candidate: str | None, current: str | None) -> bool:
    return bool(candidate and (not current or candidate > current))


def steam_raw_patches(api_json: dict) -> list[RawPatch]:
    """Extract official Steam announcement patch notes from ISteamNews JSON."""
    items = (api_json.get("appnews") or {}).get("newsitems") if isinstance(api_json, dict) else None
    if not isinstance(items, list):
        return []
    patches: list[RawPatch] = []
    for entry in items:
        if not isinstance(entry, dict) or entry.get("feedname") != _STEAM_FEED:
            continue
        title = _clean_text(str(entry.get("title") or ""))
        gid = str(entry.get("gid") or "").strip()
        if not gid or not (_has_patchnotes_tag(entry) or _STEAM_TITLE_RE.search(title)):
            continue
        url = f"https://store.steampowered.com/news/app/{STEAM_APP_ID}/view/{gid}"
        patches.append(RawPatch(url, title, _clean_steam_contents(entry.get("contents")), _unix_to_iso_date(entry.get("date"))))
    return patches


def _selector_config() -> dict:
    """Return Scrapling adaptive selector config backed by ADAPTIVE_DB_PATH."""
    Path(ADAPTIVE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return {"adaptive": True, "storage_args": {"storage_file": ADAPTIVE_DB_PATH, "url": OFFICIAL_NEWS_URL}}


def _resp_body(resp) -> str:
    """Return decoded HTML string from a Scrapling response."""
    if isinstance(html := getattr(resp, "html_content", None), str) and html:
        return html
    raw = getattr(resp, "body", b"")
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")


def _check_response(status: int, body: str, url: str, tier: str) -> None:
    """Raise BlockedError on challenges or RuntimeError on HTTP error status."""
    if detect_block(status, body):
        raise BlockedError(f"Blocked on {tier} tier (HTTP {status})")
    if status >= 400:
        raise RuntimeError(f"HTTP {status} from {url}")


def _collect_official(fetch_fn, tier: str) -> RawPatch | None:
    """Fetch the official news list, locate a patch link, and parse its article."""
    news_resp = fetch_fn(OFFICIAL_NEWS_URL, selector_config=_selector_config())
    _check_response(news_resp.status, _resp_body(news_resp), OFFICIAL_NEWS_URL, tier)
    article_url = _find_patch_in_page(news_resp, adaptive=True)
    if not article_url:
        return None
    art_resp = fetch_fn(article_url)
    art_body = _resp_body(art_resp)
    _check_response(art_resp.status, art_body, article_url, tier)
    return article_to_raw(article_url, art_body)


def collect() -> tuple[RawPatch | None, list[TierResult]]:
    """Collect the latest patch notes from official site with Steam fallback."""

    def tier_html() -> RawPatch | None:
        return _collect_official(lambda u, **kw: Fetcher.get(u, impersonate="chrome", **kw), "html")

    def tier_dynamic() -> RawPatch | None:
        return _collect_official(lambda u, **kw: StealthyFetcher.fetch(u, network_idle=True, **kw), "dynamic")

    def tier_steam() -> RawPatch | None:
        params = {"appid": STEAM_APP_ID, "count": _STEAM_COUNT, "feeds": _STEAM_FEED}
        resp = requests.get(STEAM_NEWS_API, params=params, timeout=_HTTP_TIMEOUT)
        _check_response(resp.status_code, resp.text, STEAM_NEWS_API, "steam")
        return next((p for p in steam_raw_patches(resp.json()) if p.text.strip()), None)

    def validator(payload: object) -> tuple[bool, str]:
        if not isinstance(payload, RawPatch) or not payload.url or not payload.text.strip():
            return False, "empty patch text"
        return True, f"{len(payload.text)} chars"

    official_raw, official_results = run_tiers("wherewindsmeetgame.com", [("html", tier_html), ("dynamic", tier_dynamic)], validator)
    steam_raw, steam_results = run_tiers("store.steampowered.com", [("steam", tier_steam)], validator)
    results = official_results + steam_results
    if official_raw is None:
        return steam_raw, results
    if steam_raw is not None and _is_newer_date(steam_raw.published_at, official_raw.published_at):
        return steam_raw, results
    return official_raw, results
