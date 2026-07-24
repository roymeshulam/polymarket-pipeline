"""Hebrew-aware event-to-market matching for Israel-focused markets."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import config
from markets import Market

HEBREW_DIACRITICS_RE = re.compile(r"[\u0591-\u05C7]")
TOKEN_RE = re.compile(r"[\w\u0590-\u05FF]+", re.UNICODE)

# Canonical concepts bridge Hebrew reporting to English Polymarket questions.
# Keep aliases factual and unambiguous; source-specific editorial judgment belongs
# in sources.json, not in this dictionary.
CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "israel": ("israel", "israeli", "ישראל", "ישראלי", "ישראלית"),
    "iran": ("iran", "iranian", "איראן", "איראני", "איראנית"),
    "gaza": ("gaza", "עזה", "רצועת עזה"),
    "hamas": ("hamas", "חמאס"),
    "hezbollah": ("hezbollah", "hizballah", "חיזבאללה"),
    "lebanon": ("lebanon", "lebanese", "לבנון", "לבנוני"),
    "syria": ("syria", "syrian", "סוריה", "סורי"),
    "west_bank": ("west bank", "judea and samaria", "יהודה ושומרון", "יו״ש", 'יו"ש'),
    "jerusalem": ("jerusalem", "ירושלים"),
    "netanyahu": ("netanyahu", "נתניהו", "ראש הממשלה"),
    "knesset": ("knesset", "כנסת"),
    "election": ("election", "elections", "בחירות"),
    "resignation": ("resign", "resignation", "steps down", "פרישה", "התפטר", "התפטרות"),
    "arrest": ("arrest", "detain", "custody", "מעצר", "נעצר", "עצר"),
    "ceasefire": ("ceasefire", "truce", "הפסקת אש", "הפוגה"),
    "agreement": ("agreement", "deal", "הסכם", "עסקה", "מתווה"),
    "strike": ("strike", "airstrike", "attack", "תקיפה", "תקף", "הותקף"),
    "missile": ("missile", "rocket", "טיל", "טילים", "רקטה", "רקטות"),
    "drone": ("drone", "uav", 'כטב"ם', "כטב״ם", "מל״ט", 'מל"ט'),
    "hostage": ("hostage", "hostages", "חטוף", "חטופים", "חטופה", "חטופות"),
    "release": ("release", "released", "שחרור", "שוחרר", "שוחררו"),
    "military": ("military", "idf", "צבא", "צה״ל", 'צה"ל'),
    "cabinet": ("cabinet", "קבינט", "הממשלה"),
    "nuclear": ("nuclear", "uranium", "גרעין", "גרעיני", "אורניום"),
    "hormuz": ("hormuz", "הורמוז"),
    "united_states": ("united states", "u.s.", "usa", "ארה״ב", 'ארה"ב'),
    "trump": ("trump", "טראמפ"),
    "saudi_arabia": ("saudi", "saudi arabia", "סעודיה", "הסעודית"),
    "normalization": ("normalization", "נורמליזציה"),
}

STOPWORDS = {
    "will", "the", "a", "an", "be", "by", "in", "on", "at", "to", "of",
    "for", "is", "it", "this", "that", "and", "or", "not", "before", "after",
    "end", "yes", "no", "any", "has", "have", "does", "do", "than", "more",
    "less", "over", "under", "above", "below", "through", "during", "between",
    "עם", "של", "על", "את", "אל", "כי", "לא", "כן", "גם", "הוא", "היא",
    "הם", "הן", "זה", "זו", "אשר", "לפי", "עוד", "כך", "בין", "לאחר",
}


@dataclass(frozen=True)
class MarketMatch:
    market: Market
    score: float
    shared_concepts: tuple[str, ...]


def normalize_text(text: str) -> str:
    """Normalize Hebrew punctuation/diacritics and Latin case for matching."""
    text = unicodedata.normalize("NFKC", text or "")
    text = HEBREW_DIACRITICS_RE.sub("", text)
    text = text.replace("״", '"').replace("׳", "'")
    return " ".join(text.lower().split())


def _contains_alias(normalized_text: str, alias: str) -> bool:
    normalized_alias = normalize_text(alias)
    if re.search(r"[a-z]", normalized_alias):
        return bool(
            re.search(
                rf"(?<![a-z0-9_]){re.escape(normalized_alias)}(?![a-z0-9_])",
                normalized_text,
            )
        )
    # Hebrew commonly attaches single-letter prepositions to names (e.g. באיראן).
    return normalized_alias in normalized_text


def extract_concepts(text: str) -> set[str]:
    normalized = normalize_text(text)
    concepts = set()
    for concept, aliases in CONCEPT_ALIASES.items():
        if any(_contains_alias(normalized, alias) for alias in aliases):
            concepts.add(concept)
    if {"netanyahu", "knesset"} & concepts or any(
        marker in normalized for marker in ('צה"ל', "צה״ל", "idf")
    ):
        concepts.add("israel")
    return concepts


def extract_keywords(question: str) -> list[str]:
    """Extract useful English/Hebrew tokens while preserving Hebrew words."""
    return [
        token
        for token in TOKEN_RE.findall(normalize_text(question))
        if token not in STOPWORDS and len(token) > 2
    ]


def event_fingerprint(headline: str, summary: str = "") -> str:
    """Build a language-independent, coarse key for corroboration tracking."""
    combined = f"{headline} {summary}"
    concepts = sorted(extract_concepts(combined))
    numbers = sorted(set(re.findall(r"\b\d{1,4}\b", combined)))[:3]
    if concepts:
        return "|".join(concepts[:8] + numbers)
    return "|".join(extract_keywords(headline)[:8])


def rank_news_to_markets(
    headline: str,
    summary: str,
    markets: list[Market],
    *,
    source_relevance: float = 1.0,
    max_matches: int = 5,
) -> list[MarketMatch]:
    """Rank candidate markets using bilingual concepts, rules, and keywords."""
    event_text = f"{headline} {summary}"
    event_concepts = extract_concepts(event_text)
    event_tokens = set(extract_keywords(event_text))
    ranked: list[MarketMatch] = []

    for market in markets:
        market_text = " ".join(
            [market.question, market.rules, market.resolution_source, market.category]
        )
        market_concepts = extract_concepts(market_text)
        shared = event_concepts & market_concepts

        market_tokens = set(extract_keywords(market.question))
        token_overlap = event_tokens & market_tokens
        concept_score = len(shared) / max(2, len(market_concepts))
        token_score = len(token_overlap) / max(3, len(market_tokens))

        # Entity overlap is necessary for an actionable candidate. Generic action
        # words such as "strike" or "agreement" cannot create a match by themselves.
        entity_shared = shared - {
            "strike", "missile", "drone", "agreement", "release", "military",
            "cabinet", "resignation", "arrest", "ceasefire", "election",
        }
        if not entity_shared and not token_overlap:
            continue

        score = source_relevance * (0.75 * concept_score + 0.25 * token_score)
        if score >= config.MARKET_MATCH_THRESHOLD:
            ranked.append(
                MarketMatch(
                    market=market,
                    score=score,
                    shared_concepts=tuple(sorted(shared)),
                )
            )

    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[:max_matches]


def match_news_to_markets(
    headline: str,
    markets: list[Market],
    max_matches: int = 5,
    summary: str = "",
    source_relevance: float = 1.0,
) -> list[Market]:
    """Compatibility wrapper returning only matched markets."""
    return [
        match.market
        for match in rank_news_to_markets(
            headline,
            summary,
            markets,
            source_relevance=source_relevance,
            max_matches=max_matches,
        )
    ]


def match_news_to_markets_broad(
    headline: str,
    summary: str,
    markets: list[Market],
    max_matches: int = 5,
) -> list[Market]:
    return match_news_to_markets(
        headline,
        markets,
        max_matches=max_matches,
        summary=summary,
    )
