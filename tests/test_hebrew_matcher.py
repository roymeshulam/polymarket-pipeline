from __future__ import annotations

from matcher import event_fingerprint, extract_concepts, rank_news_to_markets
from markets import Market


def _market(question: str, rules: str = "") -> Market:
    return Market(
        "condition",
        question,
        "israel",
        0.5,
        0.5,
        10_000,
        "",
        True,
        [],
        rules=rules,
    )


def test_extracts_canonical_concepts_from_hebrew():
    concepts = extract_concepts('צה"ל תקף באיראן באמצעות כטב"ם')

    assert {"israel", "military", "strike", "iran", "drone"} <= concepts


def test_matches_hebrew_report_to_english_market(monkeypatch):
    monkeypatch.setattr("config.MARKET_MATCH_THRESHOLD", 0.1)
    iran = _market(
        "Will the Israel-Iran ceasefire continue through July 31?",
        "A qualifying Israeli or Iranian air strike that impacts the other country ends it.",
    )
    election = _market("Will Netanyahu drop out of the election?")

    matches = rank_news_to_markets(
        'דיווח: צה"ל תקף מטרות באיראן',
        "",
        [iran, election],
        source_relevance=0.9,
    )

    assert matches
    assert matches[0].market is iran
    assert "iran" in matches[0].shared_concepts


def test_fingerprint_bridges_hebrew_event_concepts():
    assert "iran" in event_fingerprint("איראן שיגרה טילים לעבר ישראל")
