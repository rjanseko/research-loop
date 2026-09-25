from __future__ import annotations

import pytest

from research_loop.cli import main


def _exit(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return exc.value.code


def test_reconcile_requires_an_age_threshold(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    assert _exit(["db", "reconcile"]) == 2
    assert "--older-than" in capsys.readouterr().err


def test_show_and_db_need_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit(["show", "00000000-0000-0000-0000-000000000000"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_scout_refuses_a_setup_that_cannot_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit(["scout", "Q?"]) == 2
    assert "needs OPENAI_API_KEY" in capsys.readouterr().err


def test_invalid_configuration_is_reported_before_anything_runs(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "glm-5.3-flash")
    assert _exit(["doctor"]) == 2
    assert "Configuration is invalid" in capsys.readouterr().err
