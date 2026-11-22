"""Multi-tier giftcode collector for codes.yar.gg."""

from pathlib import Path
import re

import requests
from scrapling.fetchers import Fetcher, StealthyFetcher

from crawler.constants import ADAPTIVE_DB_PATH, YAR_API_URLS, YAR_HOME_URL
from crawler.models import TierResult
from crawler.tiers import BlockedError, detect_block, run_tiers
from crawler.validate import validate_codes

_CODE_RE = re.compile(r"[A-Z0-9]{4,24}")
_TEXT_CODE_RE = re.compile(r"\b[A-Z0-9]{6,24}\b")
_KEYWORD_RE = re.compile(r"code", re.IGNORECASE)
_TEXT_WINDOW = 80
_HTTP_TIMEOUT = 15


def _normalize_code(raw: object) -> str | None:
    """Return normalized uppercase giftcode if valid, else None."""
    code = raw.strip().upper() if isinstance(raw, str) else ""
    return code if _CODE_RE.fullmatch(code) else None


def _make_item(code: str, reward_text: str | None = None, added_at: str | None = None) -> dict:
    """Build a canonical giftcode dict matching schema_version=1."""
    return {"code": code, "reward_text": reward_text, "added_at": added_at}


def parse_codes_json(obj: dict) -> tuple[list[dict], list[str]]:
    """Parse active giftcode items and expired codes from yar.gg API JSON."""
    if not isinstance(obj, dict):
        return [], []
    items, seen_active = [], set()
    for entry in obj.get("active") or []:
        is_dict = isinstance(entry, dict)
        code = _normalize_code(entry.get("code") if is_dict else entry)
        if code and code not in seen_active:
            seen_active.add(code)
            reward = (entry.get("reward_text") or None) if is_dict else None
            added_at = (entry.get("addedAt") or entry.get("added_at")) if is_dict else None
            items.append(_make_item(code, reward, added_at))

    expired, seen_expired = [], set()
    for entry in obj.get("expired") or []:
        code = _normalize_code(entry.get("code") if isinstance(entry, dict) else entry)
        if code and code not in seen_expired:
            seen_expired.add(code)
            expired.append(code)
    return items, expired


def _is_archived_or_expired(el) -> bool:
    """Return True when a code card belongs to archived or expired entries."""
    if "is-expired" in (el.attrib.get("class") or "").split():
        return True
    iter_anc = getattr(el, "iterancestors", None)
    return callable(iter_anc) and any(a.attrib.get("id") == "archived-codes" for a in iter_anc())


def _extract_el_code(el) -> str | None:
    """Extract and validate a giftcode from a matched DOM card element."""
    if code := _normalize_code(el.attrib.get("data-code")):
        return code
    for field in el.css("input.code-field"):
        if field_code := _normalize_code(field.attrib.get("value")):
            return field_code
    return _normalize_code(getattr(el, "text", None))


def parse_codes_html(page, *, auto_save: bool = False) -> list[dict]:
    """Parse active giftcodes from HTML selector using [data-code]."""
    items, seen = [], set()
    for el in page.css("[data-code]", adaptive=True):
        if not _is_archived_or_expired(el) and (code := _extract_el_code(el)) and code not in seen:
            seen.add(code)
            items.append(_make_item(code))
    # Persist adaptive fingerprint only after confirming valid giftcodes were parsed.
    if auto_save and items:
        page.css("[data-code]", adaptive=True, auto_save=True)
    return items


def extract_codes_from_text(text: str) -> list[str]:
    """Extract uppercase alphanumeric giftcodes near 'code' keywords in text."""
    if not isinstance(text, str) or not text:
        return []
    found, seen = [], set()
    for match in _KEYWORD_RE.finditer(text):
        window = text[max(0, match.start() - _TEXT_WINDOW) : min(len(text), match.end() + _TEXT_WINDOW)]
        for token in _TEXT_CODE_RE.findall(window):
            if not token.isdigit() and not (token.isalpha() and len(token) < 6) and token not in seen:
                seen.add(token)
                found.append(token)
    return found


def _selector_config() -> dict:
    """Return Scrapling adaptive selector config backed by ADAPTIVE_DB_PATH."""
    Path(ADAPTIVE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return {"adaptive": True, "storage_args": {"storage_file": ADAPTIVE_DB_PATH, "url": YAR_HOME_URL}}


def _resp_body(resp) -> str:
    """Return decoded HTML/body string from a Scrapling response."""
    if isinstance(html := getattr(resp, "html_content", None), str) and html:
        return html
    raw = getattr(resp, "body", b"")
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")


def _raise_if_blocked(status: int, body: str, tier: str) -> None:
    """Raise BlockedError when HTTP status or body matches a challenge/block."""
    if detect_block(status, body):
        raise BlockedError(f"Blocked on {tier} tier (HTTP {status})")


def _visible_text(resp) -> str:
    """Extract visible text excluding script/style tags when supported."""
    getter = getattr(resp, "get_all_text", None)
    return str(getter()) if callable(getter) else _resp_body(resp)


def _extract_with_ai(text: str, api_key: str) -> list[str]:
    """Lazy-import Gemini extractor when available without requiring crawler.ai."""
    if not api_key or not text.strip():
        return []
    try:
        from crawler.ai import extract_codes as ai_extract_codes
    except ImportError:
        return []
    return list(dict.fromkeys(c for raw in ai_extract_codes(text, api_key) if (c := _normalize_code(raw))))


def _parse_dom_tier(resp, tier: str) -> dict | None:
    """Check block status and parse giftcodes from an HTML/browser response."""
    _raise_if_blocked(resp.status, _resp_body(resp), tier)
    items = parse_codes_html(resp, auto_save=True)
    return {"items": items, "expired": []} if items else None


def collect(
    prev_items: list[dict] | None = None,
    api_key: str | None = None,
) -> tuple[dict | None, list[TierResult]]:
    """Collect active and expired giftcodes from codes.yar.gg using 4 tiers."""
    dynamic_text: str | None = None

    def tier_api() -> dict | None:
        any_ok, last_err = False, None
        for url in YAR_API_URLS:
            try:
                resp = requests.get(url, timeout=_HTTP_TIMEOUT)
                _raise_if_blocked(resp.status_code, resp.text, "api")
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code} from {url}")
                any_ok = True
                if (parsed := parse_codes_json(resp.json()))[0]:
                    return {"items": parsed[0], "expired": parsed[1]}
            except Exception as exc:
                last_err = exc
        if not any_ok and last_err is not None:
            raise last_err
        return None

    def tier_html() -> dict | None:
        resp = Fetcher.get(YAR_HOME_URL, impersonate="chrome", selector_config=_selector_config())
        return _parse_dom_tier(resp, "html")

    def tier_dynamic() -> dict | None:
        nonlocal dynamic_text
        resp = StealthyFetcher.fetch(
            YAR_HOME_URL, network_idle=True, wait_selector="[data-code]", selector_config=_selector_config()
        )
        out = _parse_dom_tier(resp, "dynamic")
        if out is None:
            dynamic_text = _visible_text(resp)
        return out

    def tier_text() -> dict | None:
        if dynamic_text is not None:
            visible = dynamic_text
        else:
            resp = Fetcher.get(YAR_HOME_URL, impersonate="chrome")
            _raise_if_blocked(resp.status, _resp_body(resp), "text")
            visible = _visible_text(resp)
        codes = extract_codes_from_text(visible) or (_extract_with_ai(visible, api_key) if api_key else [])
        return {"items": [_make_item(c) for c in codes], "expired": []} if codes else None

    def validator(payload: object) -> tuple[bool, str]:
        if not isinstance(payload, dict):
            return False, "invalid payload"
        return validate_codes(payload.get("items", []), prev_items or [], payload.get("expired", []))

    tiers = [("api", tier_api), ("html", tier_html), ("dynamic", tier_dynamic), ("text", tier_text)]
    return run_tiers("codes.yar.gg", tiers, validator)
