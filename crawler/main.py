"""Main orchestration runner for the self-healing crawler."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path

from crawler.ai import should_translate, translate_patch
from crawler.constants import SCHEMA_VERSION
from crawler.health import build_health
from crawler.models import TierResult
from crawler.sources import official_patch, yar_codes
from crawler.store import load, save_atomic
from crawler.validate import validate_codes, validate_patch

logger = logging.getLogger(__name__)


def _log_results(label: str, results: list[TierResult]) -> None:
    """Log every tier attempt outcome for diagnostics."""
    for res in results:
        logger.info("[%s] tier=%s name=%s result=%s message=%s", label, res.tier, res.name, res.result, res.message)


def _prev_list(doc: dict | None, key: str) -> list:
    """Extract a list field from a previous JSON document or return empty list."""
    val = doc.get(key) if isinstance(doc, dict) else None
    return val if isinstance(val, list) else []


def _prev_dict(doc: dict | None, key: str) -> dict | None:
    """Extract a dict field from a previous JSON document or return None."""
    val = doc.get(key) if isinstance(doc, dict) else None
    return val if isinstance(val, dict) else None


def _process_codes(prev_doc: dict | None, now: datetime, codes_source, api_key: str | None) -> dict:
    """Collect, validate, and package giftcodes with updated health status."""
    prev_items = _prev_list(prev_doc, "items")
    prev_expired = _prev_list(prev_doc, "expired")
    prev_health = _prev_dict(prev_doc, "health")

    codes_data, raw_results = codes_source(prev_items=prev_items, api_key=api_key)
    results = list(raw_results or [])
    items, expired, success = prev_items, prev_expired, False

    if isinstance(codes_data, dict):
        cand_items = codes_data.get("items") if isinstance(codes_data.get("items"), list) else []
        cand_expired = codes_data.get("expired") if isinstance(codes_data.get("expired"), list) else []
        ok, reason = validate_codes(cand_items, prev_items, cand_expired)
        if ok:
            items, expired, success = cand_items, cand_expired, True
        else:
            results.append(TierResult("codes.yar.gg", "validate", "error", reason))

    _log_results("codes", results)
    return {
        "schema_version": SCHEMA_VERSION,
        "health": build_health(prev_health, now, success, results),
        "items": items,
        "expired": expired,
    }


def _process_patch(prev_doc: dict | None, now: datetime, patch_source, api_key: str | None) -> dict:
    """Collect, optionally translate, validate, and package patch metadata."""
    prev_patch = _prev_dict(prev_doc, "patch")
    prev_health = _prev_dict(prev_doc, "health")

    raw, raw_results = patch_source()
    results = list(raw_results or [])
    patch, success = prev_patch, False

    if raw is not None:
        if not should_translate(raw, prev_patch):
            success = True
        else:
            key = api_key.strip() if isinstance(api_key, str) else ""
            if not key:
                results.append(TierResult("gemini", "translate", "error", "missing GEMINI_API_KEY"))
            else:
                try:
                    candidate = translate_patch(raw, key)
                    ok, reason = validate_patch(candidate, prev_patch)
                    if ok:
                        patch, success = candidate, True
                        results.append(TierResult("gemini", "translate", "ok", f"v{candidate.get('version', '?')}"))
                    else:
                        results.append(TierResult("gemini", "translate", "error", f"validation failed: {reason}"))
                except Exception as exc:
                    results.append(TierResult("gemini", "translate", "error", str(exc)))

    _log_results("patch", results)
    return {
        "schema_version": SCHEMA_VERSION,
        "health": build_health(prev_health, now, success, results),
        "patch": patch,
    }


def run(
    now: datetime | None = None,
    data_dir: Path = Path("data"),
    codes_source=yar_codes.collect,
    patch_source=official_patch.collect,
    api_key: str | None = None,
) -> int:
    """Execute one crawler run and atomically persist codes.json and patch.json."""
    attempt_at = now or datetime.now(timezone.utc)
    codes_path = data_dir / "codes.json"
    patch_path = data_dir / "patch.json"

    codes_doc = _process_codes(load(codes_path), attempt_at, codes_source, api_key)
    patch_doc = _process_patch(load(patch_path), attempt_at, patch_source, api_key)

    save_atomic(codes_path, codes_doc)
    save_atomic(patch_path, patch_doc)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(run(api_key=os.environ.get("GEMINI_API_KEY")))
