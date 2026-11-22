"""Data models for crawler tier results and raw patch notes."""

from dataclasses import dataclass


@dataclass
class TierResult:
    """Outcome of a single fetch tier attempt."""

    name: str
    tier: str
    result: str
    message: str
    data: object | None = None


@dataclass
class RawPatch:
    """Raw patch article fetched from an official news source."""

    url: str
    title: str
    text: str
    published_at: str | None = None
