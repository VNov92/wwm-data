"""Gemini API client for patch note translation and giftcode extraction."""

import json
import re

import requests

from crawler.constants import GEMINI_MODELS
from crawler.models import RawPatch

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_HTTP_TIMEOUT = 30
_MAX_PATCH_TOKENS = 4096
_MAX_CODES_TOKENS = 1024
_MAX_INPUT_CHARS = 12000
_CODE_RE = re.compile(r"[A-Z0-9]{4,24}")

_PATCH_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "version": {"type": "STRING"},
        "releaseDate": {"type": "STRING"},
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "highlights": {"type": "ARRAY", "items": {"type": "STRING"}},
        "sections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"title": {"type": "STRING"}, "items": {"type": "ARRAY", "items": {"type": "STRING"}}},
                "required": ["title", "items"],
            },
        },
        "sources": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"title": {"type": "STRING"}, "url": {"type": "STRING"}},
                "required": ["title", "url"],
            },
        },
    },
    "required": ["version", "releaseDate", "title", "summary", "highlights", "sections", "sources"],
}

_CODES_RESPONSE_SCHEMA = {"type": "ARRAY", "items": {"type": "STRING"}}


def should_translate(raw: RawPatch, prev_patch: dict | None) -> bool:
    """Return False when prev_patch already cites raw.url in its sources."""
    if not isinstance(prev_patch, dict):
        return True
    sources = prev_patch.get("sources") or []
    return not any(isinstance(src, dict) and src.get("url") == raw.url for src in sources)


def _extract_candidate_text(payload: object) -> str | None:
    """Return the first non-empty text part from a Gemini response payload."""
    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    parts = ((candidates[0] or {}).get("content") or {}).get("parts")
    if not isinstance(parts, list):
        return None
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
            return part["text"]
    return None


def _error_message(resp) -> str:
    """Extract a concise error message from an unsuccessful Gemini HTTP response."""
    try:
        err = (resp.json() or {}).get("error") or {}
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    except Exception:
        pass
    return f"HTTP {resp.status_code}"


def _call_gemini(prompt: str, api_key: str, schema: dict, max_tokens: int, expect_type: type) -> object:
    """Call Gemini generateContent across GEMINI_MODELS with fallback on 503/network/JSON errors."""
    key = api_key.strip() if isinstance(api_key, str) else ""
    if not key:
        raise ValueError("Gemini API key is required")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "maxOutputTokens": max_tokens},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    last_error: Exception | None = None

    for model in GEMINI_MODELS:
        try:
            resp = requests.post(_GEMINI_ENDPOINT.format(model=model), headers=headers, json=body, timeout=_HTTP_TIMEOUT)
            if resp.status_code in (400, 401, 403):
                raise RuntimeError(f"Gemini HTTP {resp.status_code} ({model}): {_error_message(resp)}")
            if resp.status_code >= 400:
                last_error = RuntimeError(f"Gemini HTTP {resp.status_code} ({model}): {_error_message(resp)}")
                continue
            text = _extract_candidate_text(resp.json())
            if not text:
                last_error = RuntimeError(f"Gemini returned empty response ({model})")
                continue
            parsed = json.loads(text)
            if not isinstance(parsed, expect_type):
                last_error = ValueError(f"Expected {expect_type.__name__} from Gemini ({model})")
                continue
            return parsed
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            last_error = exc

    raise RuntimeError(str(last_error) if last_error else "All Gemini models failed")


def _build_patch_prompt(raw: RawPatch) -> str:
    """Build Vietnamese prompt instructing Gemini to extract and translate patch details."""
    return (
        "Bạn là biên tập viên theo dõi bản cập nhật Where Winds Meet.\n"
        f"Dưới đây là thông báo cập nhật chính thức ({raw.url}):\n"
        f"Tiêu đề: {raw.title or 'Ghi chú bản vá chính thức'}\n"
        f"Ngày đăng: {raw.published_at or 'chưa rõ'}\n"
        f"Nội dung: {raw.text[:_MAX_INPUT_CHARS]}\n\n"
        "Quy tắc:\n"
        "1. Chỉ coi ghi chú vá lỗi, cập nhật nội dung hoặc bảo trì có thay đổi game là thông tin bản vá. "
        "Không nhầm bài giới thiệu vật phẩm, trang phục, giftcode, thư nhà phát triển hay lịch sự kiện với phiên bản mới.\n"
        "2. Không tự suy diễn số phiên bản, ngày phát hành hay tính năng không có trong bài viết.\n"
        "3. Trích xuất và dịch sang tiếng Việt theo đúng JSON schema: version (X.Y hoặc X.Y.Z), "
        "releaseDate (YYYY-MM-DD), title (ngắn gọn), summary (2-3 câu), highlights (3-4 điểm nổi bật), "
        "sections ({title, items} dịch rõ nghĩa), sources."
    )


def _normalize_patch(data: dict, raw: RawPatch) -> dict:
    """Normalize Gemini patch JSON into PatchInfo dict and force authoritative source."""
    release_date = str(data.get("releaseDate") or data.get("release_date") or "").strip()
    highlights = [item.strip() for item in (data.get("highlights") or []) if isinstance(item, str) and item.strip()]
    sections = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sec_title = str(sec.get("title") or "").strip()
        items = [it.strip() for it in (sec.get("items") or []) if isinstance(it, str) and it.strip()]
        if sec_title and items:
            sections.append({"title": sec_title, "items": items})

    return {
        "version": str(data.get("version") or "").strip(),
        "releaseDate": release_date,
        "title": str(data.get("title") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "highlights": highlights,
        "sections": sections,
        "sources": [{"title": raw.title, "url": raw.url}],
    }


def translate_patch(raw: RawPatch, api_key: str) -> dict:
    """Translate and normalize a RawPatch into Vietnamese PatchInfo JSON via Gemini."""
    data = _call_gemini(_build_patch_prompt(raw), api_key, _PATCH_RESPONSE_SCHEMA, _MAX_PATCH_TOKENS, dict)
    return _normalize_patch(data, raw)


def extract_codes(text: str, api_key: str) -> list[str]:
    """Extract normalized uppercase giftcodes from raw text using Gemini."""
    if not isinstance(text, str) or not text.strip() or not isinstance(api_key, str) or not api_key.strip():
        return []
    prompt = (
        "Extract all valid active Where Winds Meet giftcodes / redeem codes found in the text below. "
        "Return only a JSON array of alphanumeric code strings (4-24 chars). "
        "Do not guess or include ordinary English words.\n\n"
        f"Text:\n{text[:_MAX_INPUT_CHARS]}"
    )
    items = _call_gemini(prompt, api_key, _CODES_RESPONSE_SCHEMA, _MAX_CODES_TOKENS, list)
    normalized: list[str] = []
    for raw_code in items:
        if isinstance(raw_code, str):
            code = raw_code.strip().upper()
            if _CODE_RE.fullmatch(code) and code not in normalized:
                normalized.append(code)
    return normalized
