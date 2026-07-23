from markets import _build_market_url, _infer_category


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
