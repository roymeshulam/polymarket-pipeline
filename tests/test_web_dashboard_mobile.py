"""Regression checks for the web dashboard's phone-width layout."""

from pathlib import Path


INDEX_HTML_SOURCE = (
    Path(__file__).resolve().parents[1] / "web_dashboard.py"
).read_text(encoding="utf-8")


def test_mobile_dashboard_is_contained_to_viewport():
    assert 'content="width=device-width, initial-scale=1"' in INDEX_HTML_SOURCE
    assert (
        "html, body { max-width: 100%; overflow-x: hidden; }"
        in INDEX_HTML_SOURCE
    )
    assert (
        "grid-template-columns: minmax(0, 1fr) minmax(0, 2fr)"
        in INDEX_HTML_SOURCE
    )
    assert (
        "min-width: 0; max-width: 100%; overflow: hidden;"
        in INDEX_HTML_SOURCE
    )


def test_mobile_tables_render_as_labeled_cards():
    assert "@media (max-width: 640px)" in INDEX_HTML_SOURCE
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in INDEX_HTML_SOURCE
    assert "content: attr(data-label)" in INDEX_HTML_SOURCE
    assert 'data-label="Market"' in INDEX_HTML_SOURCE
    assert 'data-label="Status"' in INDEX_HTML_SOURCE
