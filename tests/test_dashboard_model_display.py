"""Regression checks for displaying the configured OpenAI model."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DASHBOARD_SOURCE = (ROOT / "web_dashboard.py").read_text(encoding="utf-8")
TERMINAL_DASHBOARD_SOURCE = (ROOT / "dashboard.py").read_text(encoding="utf-8")


def test_web_dashboard_shows_model_directly_below_pipeline():
    pipeline_row = "Pipeline${infoIcon('pipeline_status', 'Pipeline')}</span>"
    model_row = "Model${infoIcon('model', 'Model')}</span><span>${s.openai_model}</span>"

    assert WEB_DASHBOARD_SOURCE.index(model_row) > WEB_DASHBOARD_SOURCE.index(
        pipeline_row
    )
    assert WEB_DASHBOARD_SOURCE.count('"openai_model": config.OPENAI_MODEL') == 2


def test_terminal_dashboard_shows_model_directly_below_pipeline():
    pipeline_row = 'table.add_row("Pipeline", status_text)'
    model_row = 'table.add_row("Model", config.OPENAI_MODEL)'

    assert TERMINAL_DASHBOARD_SOURCE.index(model_row) > TERMINAL_DASHBOARD_SOURCE.index(
        pipeline_row
    )
