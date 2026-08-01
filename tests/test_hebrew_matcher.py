from __future__ import annotations

from matcher import event_fingerprint, extract_concepts, find_coverage_gaps, rank_news_to_markets
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


def test_extracts_nobel_prize_predicate_from_hebrew():
    concepts = extract_concepts("נתניהו יזכה בפרס נובל לשלום בשנת 2026")

    assert "nobel_prize" in concepts


def test_matches_hebrew_report_to_english_nobel_market(monkeypatch):
    monkeypatch.setattr("config.MARKET_MATCH_THRESHOLD", 0.1)
    market = _market("Will Benjamin Netanyahu win the Nobel Peace Prize in 2026?")

    matches = rank_news_to_markets(
        "נתניהו יזכה בפרס נובל לשלום בשנת 2026",
        "",
        [market],
        source_relevance=1.0,
    )

    assert matches
    assert "netanyahu" in matches[0].shared_entities
    assert matches[0].shared_predicates == ("nobel_prize",)


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


def test_airspace_market_rejects_generic_israel_security_news(monkeypatch):
    monkeypatch.setattr("config.MARKET_MATCH_THRESHOLD", 0.1)
    market = _market(
        "Will Israel close its airspace by July 31?",
        "A broad closure of commercial aviation across Israeli civilian airspace qualifies.",
    )

    matches = rank_news_to_markets(
        'צה"ל נערך לפעילות מבצעית בעקבות פיגוע בשומרון',
        "",
        [market],
        source_relevance=1.0,
        source_topics=("israel", "aviation"),
    )

    assert matches == []


def test_airspace_market_requires_aviation_capable_source(monkeypatch):
    monkeypatch.setattr("config.MARKET_MATCH_THRESHOLD", 0.1)
    market = _market(
        "Will Israel close its airspace by July 31?",
        "A broad closure of commercial aviation across Israeli civilian airspace qualifies.",
    )
    headline = (
        'רשות שדות התעופה: ישראל סגרה את המרחב האווירי לטיסות מסחריות'
    )

    rejected = rank_news_to_markets(
        headline,
        "",
        [market],
        source_relevance=1.0,
        source_topics=("israel", "security"),
    )
    accepted = rank_news_to_markets(
        headline,
        "",
        [market],
        source_relevance=1.0,
        source_topics=("israel", "aviation"),
    )

    assert rejected == []
    assert accepted
    assert accepted[0].shared_entities == ("israel",)
    assert {"aviation", "closure"} <= set(accepted[0].shared_predicates)


def test_find_coverage_gaps_flags_market_with_no_predicate():
    market = _market("Will Israel win the Eurovision Song Contest in 2026?")

    gaps = find_coverage_gaps([market])

    assert len(gaps) == 1
    assert gaps[0].missing == ("predicate",)
    assert gaps[0].concepts_found == ("israel",)


def test_find_coverage_gaps_flags_market_with_no_entity():
    # category deliberately not "israel" — _market()'s default category would
    # otherwise inject the "israel" entity concept via the market_text join.
    market = Market(
        "condition",
        "Will there be a major diplomatic normalization deal in 2026?",
        "geopolitics",
        0.5,
        0.5,
        10_000,
        "",
        True,
        [],
    )

    gaps = find_coverage_gaps([market])

    assert len(gaps) == 1
    assert gaps[0].missing == ("entity",)


def test_find_coverage_gaps_ignores_fully_covered_market():
    market = _market(
        "Will the Israel-Iran ceasefire continue through July 31?",
        "A qualifying Israeli or Iranian air strike that impacts the other country ends it.",
    )

    assert find_coverage_gaps([market]) == []
