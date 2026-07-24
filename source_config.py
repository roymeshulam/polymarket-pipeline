"""Typed configuration for Hebrew and Israel-focused news sources."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SOURCE_KINDS = {"rss", "twitter", "telegram"}


@dataclass(frozen=True)
class SourceProfile:
    """Operational and editorial policy for one independently reviewed source."""

    source_id: str
    kind: str
    name: str
    independence_group: str = ""
    enabled: bool = False
    language: str = "he"
    max_age_seconds: int = 300
    relevance: float = 0.5
    trust_tier: int = 3
    min_confirmations: int = 2
    allow_live: bool = False
    poll_interval_seconds: int = 60
    topics: tuple[str, ...] = field(default_factory=tuple)
    url: str = ""
    query: str = ""
    channel_id: str = ""

    def validate(self) -> None:
        if not self.source_id or not self.source_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"invalid source_id: {self.source_id!r}")
        if self.kind not in SUPPORTED_SOURCE_KINDS:
            raise ValueError(f"{self.source_id}: unsupported kind {self.kind!r}")
        if not self.independence_group:
            raise ValueError(f"{self.source_id}: independence_group must not be empty")
        if self.max_age_seconds <= 0:
            raise ValueError(f"{self.source_id}: max_age_seconds must be positive")
        if not 0.0 <= self.relevance <= 1.0:
            raise ValueError(f"{self.source_id}: relevance must be between 0 and 1")
        if self.trust_tier not in {1, 2, 3, 4, 5}:
            raise ValueError(f"{self.source_id}: trust_tier must be 1-5")
        if self.min_confirmations < 1:
            raise ValueError(f"{self.source_id}: min_confirmations must be at least 1")
        if self.poll_interval_seconds < 10:
            raise ValueError(f"{self.source_id}: poll_interval_seconds must be at least 10")
        if self.enabled and self.kind == "rss" and not self.url:
            raise ValueError(f"{self.source_id}: enabled RSS source requires url")
        if self.enabled and self.kind == "twitter" and not self.query:
            raise ValueError(f"{self.source_id}: enabled Twitter source requires query")
        if self.enabled and self.kind == "twitter" and not {
            "-is:reply",
            "-is:retweet",
        }.issubset(self.query.split()):
            raise ValueError(
                f"{self.source_id}: Twitter queries must exclude replies and retweets"
            )
        if self.enabled and self.kind == "telegram" and not self.channel_id:
            raise ValueError(f"{self.source_id}: enabled Telegram source requires channel_id")
        if self.allow_live and self.trust_tier > 2:
            raise ValueError(
                f"{self.source_id}: live trading is restricted to trust tiers 1-2"
            )


def _profile_from_dict(data: dict) -> SourceProfile:
    profile = SourceProfile(
        source_id=str(data.get("id", "")).strip(),
        kind=str(data.get("kind", "")).strip().lower(),
        name=str(data.get("name", data.get("id", ""))).strip(),
        independence_group=str(
            data.get("independence_group", data.get("id", ""))
        ).strip().lower(),
        enabled=bool(data.get("enabled", False)),
        language=str(data.get("language", "he")).strip().lower(),
        max_age_seconds=int(data.get("max_age_seconds", 300)),
        relevance=float(data.get("relevance", 0.5)),
        trust_tier=int(data.get("trust_tier", 3)),
        min_confirmations=int(data.get("min_confirmations", 2)),
        allow_live=bool(data.get("allow_live", False)),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 60)),
        topics=tuple(str(value).strip().lower() for value in data.get("topics", [])),
        url=str(data.get("url", "")).strip(),
        query=str(data.get("query", "")).strip(),
        channel_id=str(data.get("channel_id", "")).strip(),
    )
    profile.validate()
    return profile


def load_source_profiles(path: str | Path) -> list[SourceProfile]:
    """Load and validate source profiles from a JSON document."""
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(
            f"source configuration not found: {config_path}. "
            "Copy sources.example.json to sources.json."
        )

    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    raw_sources = payload.get("sources", []) if isinstance(payload, dict) else []
    if not isinstance(raw_sources, list):
        raise ValueError("source configuration must contain a 'sources' list")

    profiles = [_profile_from_dict(item) for item in raw_sources if isinstance(item, dict)]
    ids = [profile.source_id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("source IDs must be unique")
    enabled_telegram_ids = [
        profile.channel_id
        for profile in profiles
        if profile.enabled and profile.kind == "telegram"
    ]
    if len(enabled_telegram_ids) != len(set(enabled_telegram_ids)):
        raise ValueError("enabled Telegram channel IDs must be unique")
    if not profiles:
        raise ValueError("at least one source profile is required")
    return profiles


def profiles_by_kind(
    profiles: list[SourceProfile],
    kind: str,
    *,
    enabled_only: bool = True,
) -> list[SourceProfile]:
    """Return profiles for an ingestion adapter."""
    return [
        profile
        for profile in profiles
        if profile.kind == kind and (profile.enabled or not enabled_only)
    ]


def profile_map(profiles: list[SourceProfile]) -> dict[str, SourceProfile]:
    return {profile.source_id: profile for profile in profiles}
