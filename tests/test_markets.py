from markets import _build_market_url, _infer_category, fetch_target_markets


def test_builds_specific_market_url():
    data = {
        "slug": "new-rhianna-album-before-gta-vi-926",
        "events": [{"slug": "what-will-happen-before-gta-vi"}],
    }

    assert _build_market_url(data) == (
        "https://polymarket.com/event/what-will-happen-before-gta-vi/"
        "new-rhianna-album-before-gta-vi-926"
    )


def test_builds_single_slug_fallback_url():
    assert _build_market_url({"slug": "will-x-happen"}) == (
        "https://polymarket.com/event/will-x-happen"
    )


def test_returns_empty_url_without_slugs():
    assert _build_market_url({}) == ""


def test_infers_new_market_categories():
    assert _infer_category("Will inflation fall below 2%?", []) == "economics"
    assert _infer_category("Will a ceasefire be signed this month?", []) == "geopolitics"
    assert _infer_category("Will the FDA approve this drug?", []) == "health"


def test_category_matching_respects_word_boundaries():
    assert _infer_category("Will he win the comeback player award?", []) == "other"
    assert _infer_category("Will the company raise capital?", []) == "other"


def test_target_market_search_flattens_active_events(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "events": [
                    {
                        "slug": "israel-iran-ceasefire",
                        "active": True,
                        "closed": False,
                        "resolutionSource": "Official announcements",
                        "markets": [
                            {
                                "id": "1",
                                "conditionId": "condition",
                                "slug": "through-july",
                                "question": "Will the Israel-Iran ceasefire last through July?",
                                "outcomePrices": '["0.6", "0.4"]',
                                "clobTokenIds": '["yes", "no"]',
                                "volume": "10000",
                                "active": True,
                                "closed": False,
                                "orderPriceMinTickSize": 0.005,
                            }
                        ],
                    }
                ]
            }

    monkeypatch.setattr("markets.httpx.get", lambda *args, **kwargs: Response())

    results = fetch_target_markets(queries=["Israel"], limit_per_query=10)

    assert len(results) == 1
    assert results[0].condition_id == "condition"
    assert results[0].tick_size == "0.005"
    assert results[0].resolution_source == "Official announcements"
    assert results[0].url.endswith("/israel-iran-ceasefire/through-july")
