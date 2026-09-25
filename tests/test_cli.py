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


def test_frozen_case_requires_one_case_and_a_hard_cap(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    assert _exit(["scout"]) == 2
    assert "exactly one" in capsys.readouterr().err
    assert _exit(["scout", "Q?", "--case", "drb2-task8"]) == 2
    assert "exactly one" in capsys.readouterr().err
    assert _exit(["scout", "--case", "drb2-task8"]) == 2
    assert "--max-usd" in capsys.readouterr().err
    assert _exit(["scout", "--case", "drb2-task8", "--max-usd", "2", "--no-persist"]) == 2
    assert "persistence" in capsys.readouterr().err
    assert _exit(["scout", "--case", "drb2-task8", "--max-usd", "2", "--note", "new context"]) == 2
    assert "frozen" in capsys.readouterr().err


def test_grading_requires_a_positive_hard_cap(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    run_id = "00000000-0000-0000-0000-000000000000"
    assert _exit(["grade", run_id, "--case", "drb2-task8"]) == 2
    assert "--max-usd" in capsys.readouterr().err
    assert _exit(["grade", run_id, "--case", "drb2-task8", "--max-usd", "0"]) == 2
    assert "positive" in capsys.readouterr().err


def test_invalid_configuration_is_reported_before_anything_runs(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "glm-5.3-flash")
    assert _exit(["doctor"]) == 2
    assert "Configuration is invalid" in capsys.readouterr().err
