from __future__ import annotations

from dataclasses import dataclass
import re

import httpx

import config

GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class Market:
    condition_id: str
    question: str
    category: str
    yes_price: float
    no_price: float
    volume: float
    end_date: str
    active: bool
    tokens: list[dict]
    url: str = ""
    rules: str = ""
    resolution_source: str = ""
    tick_size: str = "0.01"
    neg_risk: bool = False

    @property
    def implied_probability(self) -> float:
        return self.yes_price


def _build_market_url(data: dict) -> str:
    """Build the public Polymarket URL from Gamma event and market slugs."""
    market_slug = str(data.get("slug", "")).strip()
    events = data.get("events") or []
    event_slug = ""
    if isinstance(events, list) and events and isinstance(events[0], dict):
        event_slug = str(events[0].get("slug", "")).strip()

    if event_slug and market_slug:
        return f"https://polymarket.com/event/{event_slug}/{market_slug}"
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if market_slug:
        return f"https://polymarket.com/event/{market_slug}"
    return ""


def fetch_active_markets(limit: int = 50) -> list[Market]:
    """Fetch active, liquid markets from Polymarket's Gamma API."""
    markets = []

    try:
        resp = httpx.get(
            f"{GAMMA_API}/markets",
            params={
                "limit": limit,
                "active": True,
                "closed": False,
                "order": "volume",
                "ascending": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[markets] Gamma API error: {e}, falling back to CLOB...")
        return _fetch_from_clob(limit)

    items = data if isinstance(data, list) else data.get("data", [])

    for item in items:
        market = _market_from_gamma(item)
        if market is not None:
            markets.append(market)

    # Sort by volume descending
    markets.sort(key=lambda x: x.volume, reverse=True)
    return markets


def fetch_target_markets(
    queries: list[str] | None = None,
    limit_per_query: int = 50,
) -> list[Market]:
    """Discover active Israel-related markets through Gamma public search."""
    found: dict[str, Market] = {}
    for query in queries or config.MARKET_SEARCH_QUERIES:
        try:
            response = httpx.get(
                f"{GAMMA_API}/public-search",
                params={
                    "q": query,
                    "events_status": "active",
                    "limit_per_type": limit_per_query,
                    "keep_closed_markets": 0,
                    "search_profiles": False,
                },
                timeout=15,
            )
            response.raise_for_status()
            events = response.json().get("events", [])
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            print(f"[markets] Search failed for {query!r}: {type(exc).__name__}")
            continue

        for event in events or []:
            if not isinstance(event, dict) or event.get("closed"):
                continue
            event_slug = str(event.get("slug", "")).strip()
            for raw_market in event.get("markets") or []:
                if not isinstance(raw_market, dict):
                    continue
                if raw_market.get("closed") or not raw_market.get("active", True):
                    continue
                if raw_market.get("enableOrderBook") is False:
                    continue
                if raw_market.get("acceptingOrders") is False:
                    continue
                enriched = dict(raw_market)
                enriched.setdefault("events", [{"slug": event_slug}])
                enriched.setdefault(
                    "resolutionSource",
                    event.get("resolutionSource", ""),
                )
                market = _market_from_gamma(enriched)
                if (
                    market is not None
                    and market.category == "israel"
                    and market.tokens
                ):
                    found[market.condition_id] = market

    return sorted(found.values(), key=lambda market: market.volume, reverse=True)


def _market_from_gamma(data: dict) -> Market | None:
    """Convert one Gamma market response into the internal model."""
    import json

    try:
        outcome_prices = data.get("outcomePrices", "")
        yes_price = 0.5
        no_price = 0.5
        if outcome_prices:
            try:
                prices = (
                    json.loads(outcome_prices)
                    if isinstance(outcome_prices, str)
                    else outcome_prices
                )
                if len(prices) >= 2:
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        clob_token_ids = data.get("clobTokenIds", "")
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except json.JSONDecodeError:
                clob_token_ids = []

        token_list = []
        outcomes = ["Yes", "No"]
        for index, token_id in enumerate(
            clob_token_ids if isinstance(clob_token_ids, list) else []
        ):
            token_list.append(
                {
                    "token_id": token_id,
                    "outcome": (
                        outcomes[index]
                        if index < len(outcomes)
                        else f"Outcome_{index}"
                    ),
                    "price": yes_price if index == 0 else no_price,
                }
            )

        volume = float(data.get("volume", data.get("volumeNum", 0)) or 0)
        question = str(data.get("question", "") or "")
        condition_id = str(
            data.get("conditionId", data.get("condition_id", data.get("id", "")))
            or ""
        )
        if not question or not condition_id:
            return None
        if yes_price in (0.0, 1.0) and volume == 0:
            return None

        return Market(
            condition_id=condition_id,
            question=question,
            category=_infer_category(question, data.get("tags", None) or []),
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            end_date=data.get("endDate", data.get("end_date_iso", "")),
            active=bool(data.get("active", True)),
            tokens=token_list,
            url=_build_market_url(data),
            rules=str(data.get("description", data.get("rules", "")) or ""),
            resolution_source=str(data.get("resolutionSource", "") or ""),
            tick_size=str(
                data.get(
                    "orderPriceMinTickSize",
                    data.get("orderMinPriceTickSize", "0.01"),
                )
                or "0.01"
            ),
            neg_risk=bool(data.get("negRisk", False)),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _fetch_from_clob(limit: int) -> list[Market]:
    """Fallback: fetch from CLOB API directly."""
    markets = []
    try:
        resp = httpx.get(
            f"{config.POLYMARKET_HOST}/markets",
            params={"limit": limit, "active": True},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[markets] CLOB API error: {e}")
        return markets

    items = data if isinstance(data, list) else data.get("data", data.get("markets", []))

    for m in items:
        try:
            tokens = m.get("tokens", [])
            yes_price = 0.5
            no_price = 0.5
            for t in tokens:
                outcome = t.get("outcome", "").lower()
                price = float(t.get("price", 0.5))
                if outcome == "yes":
                    yes_price = price
                elif outcome == "no":
                    no_price = price

            markets.append(Market(
                condition_id=m.get("condition_id", m.get("id", "")),
                question=m.get("question", ""),
                category=_infer_category(m.get("question", ""), m.get("tags") or []),
                yes_price=yes_price,
                no_price=no_price,
                volume=float(m.get("volume", 0)),
                end_date=m.get("end_date_iso", m.get("end_date", "")),
                active=m.get("active", True),
                tokens=tokens,
                url=_build_market_url(m),
                rules=str(m.get("description", m.get("rules", "")) or ""),
                resolution_source=str(
                    m.get("resolution_source", m.get("resolutionSource", "")) or ""
                ),
                tick_size=str(m.get("minimum_tick_size", "0.01") or "0.01"),
                neg_risk=bool(m.get("neg_risk", m.get("negRisk", False))),
            ))
        except (KeyError, ValueError):
            continue

    return markets


def _infer_category(question: str, tags: list) -> str:
    """Infer category from question text and tags."""
    q = question.lower()
    tag_str = " ".join(str(t).lower() for t in tags)
    combined = f"{q} {tag_str}"

    def contains_any(keywords: list[str]) -> bool:
        return any(
            re.search(
                rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])",
                combined,
            )
            for keyword in keywords
        )

    if contains_any([
        "israel", "israeli", "netanyahu", "gaza", "hamas", "hezbollah",
        "west bank", "jerusalem", "knesset", "idf", "iran",
    ]):
        return "israel"
    if contains_any(["ai", "artificial intelligence", "openai", "chatgpt", "llm", "google ai", "anthropic"]):
        return "ai"
    if contains_any(["bitcoin", "ethereum", "crypto", "blockchain", "defi", "token"]):
        return "crypto"
    if contains_any([
        "federal reserve", "fed rate", "interest rate", "inflation", "cpi",
        "gdp", "unemployment", "recession", "jobs report", "central bank",
        "tariff",
    ]):
        return "economics"
    if contains_any([
        "war", "ceasefire", "sanction", "nato", "taiwan", "ukraine",
        "russia", "china", "israel", "iran", "gaza",
    ]):
        return "geopolitics"
    if contains_any([
        "fda", "vaccine", "pandemic", "disease", "drug approval",
        "clinical trial", "world health organization",
    ]):
        return "health"
    if contains_any(["election", "president", "congress", "senate", "trump", "biden", "political"]):
        return "politics"
    if contains_any(["spacex", "nasa", "climate", "research", "study", "discovery"]):
        return "science"
    if contains_any(["tech", "apple", "google", "microsoft", "software", "startup"]):
        return "technology"
    return "other"


def filter_by_categories(markets: list[Market], categories: list[str] | None = None) -> list[Market]:
    """Filter markets to only target categories."""
    cats = categories or config.MARKET_CATEGORIES
    return [m for m in markets if m.category in cats]


def get_token_id(market: Market, side: str) -> str | None:
    """Get the token ID for a given side (YES/NO)."""
    for t in market.tokens:
        if t.get("outcome", "").upper() == side.upper():
            return t.get("token_id")
    return None


if __name__ == "__main__":
    targeted_markets = fetch_target_markets()
    filtered = filter_by_categories(targeted_markets)
    print(f"\n--- {len(filtered)} Israel-focused markets ---\n")
    for m in filtered[:15]:
        print(f"  [{m.category}] {m.question}")
        print(f"    YES: {m.yes_price:.2f} | NO: {m.no_price:.2f} | Vol: ${m.volume:,.0f}")
        print()
