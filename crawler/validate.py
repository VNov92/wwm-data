"""Validators for crawled giftcodes and patch metadata."""

from datetime import date
import re
from urllib.parse import urlparse

from crawler.constants import (
    MAX_PATCH_SUMMARY_LEN,
    MAX_PATCH_TITLE_LEN,
    OFFICIAL_DOMAINS,
    SHRINK_RATIO,
)

_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")
_CODE_RE = re.compile(r"[A-Z0-9]{4,24}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAX_VERSION_COMPONENT = 99


def parse_version(version: str) -> tuple[int, ...] | None:
    """Parse a 2- or 3-part dot-separated version string with parts < 100."""
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        return None
    parts = tuple(int(part) for part in version.split("."))
    if any(part > _MAX_VERSION_COMPONENT for part in parts):
        return None
    return parts


def _pad_version(parts: tuple[int, ...]) -> tuple[int, int, int]:
    """Pad a parsed version tuple to 3 components for comparison."""
    padded = parts + (0, 0, 0)
    return padded[0], padded[1], padded[2]


def _is_official_https_url(url: object) -> bool:
    """Return True if url uses https and belongs to an official domain."""
    if not isinstance(url, str) or not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in OFFICIAL_DOMAINS
    )


def _is_valid_official_source(src: object) -> bool:
    """Return True if src is a dict with a non-empty title and official HTTPS URL."""
    if not isinstance(src, dict):
        return False
    src_title = src.get("title")
    return (
        isinstance(src_title, str)
        and bool(src_title.strip())
        and _is_official_https_url(src.get("url"))
    )


def validate_codes(
    items: list[dict],
    prev_items: list[dict],
    expired: list[str],
) -> tuple[bool, str]:
    """Validate crawled giftcode items against format and shrink rules."""
    if not items:
        return False, "codes list is empty"

    current_codes: set[str] = set()
    for item in items:
        code = item.get("code") if isinstance(item, dict) else None
        if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
            return False, f"invalid code format: {code!r}"
        current_codes.add(code)

    prev_codes = {
        item["code"]
        for item in prev_items
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    still_expected = prev_codes - set(expired)
    if still_expected:
        retained_ratio = len(current_codes & still_expected) / len(still_expected)
        if retained_ratio < SHRINK_RATIO:
            return (
                False,
                f"unexplained code shrink: retained {retained_ratio:.2f} < {SHRINK_RATIO}",
            )

    return True, "ok"


def validate_patch(patch: dict, prev: dict | None) -> tuple[bool, str]:
    """Validate a patch note object against schema, source, and version rules."""
    if not isinstance(patch, dict):
        return False, "patch must be a dict"

    parsed_ver = parse_version(patch.get("version", ""))
    if parsed_ver is None:
        return False, f"invalid patch version: {patch.get('version')!r}"

    release_date = patch.get("releaseDate")
    if not isinstance(release_date, str) or not _DATE_RE.fullmatch(release_date):
        return False, f"invalid releaseDate format: {release_date!r}"
    try:
        date.fromisoformat(release_date)
    except ValueError:
        return False, f"invalid calendar releaseDate: {release_date!r}"

    for field, max_len in (("title", MAX_PATCH_TITLE_LEN), ("summary", MAX_PATCH_SUMMARY_LEN)):
        val = patch.get(field)
        if not isinstance(val, str) or not val.strip():
            return False, f"missing or empty {field}"
        if len(val.strip()) > max_len:
            return False, f"{field} exceeds {max_len} chars"

    sources = patch.get("sources")
    if not isinstance(sources, list) or not any(_is_valid_official_source(src) for src in sources):
        return False, "patch requires at least one official https source with title"

    if isinstance(prev, dict) and isinstance(prev.get("version"), str):
        prev_ver = parse_version(prev["version"])
        if prev_ver is not None and _pad_version(parsed_ver) < _pad_version(prev_ver):
            return (
                False,
                f"version regression: {patch['version']} < {prev['version']}",
            )

    return True, "ok"
