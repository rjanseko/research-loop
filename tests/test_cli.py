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


@pytest.mark.parametrize(("command", "setting"), [
    ("audit", "RESEARCH_MODELS__AUDIT"),
    ("diagnose", "RESEARCH_MODELS__DIAGNOSE"),
])
def test_evaluation_commands_use_their_configured_model_without_a_flag(command, setting, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    monkeypatch.setenv(setting, "openai:gpt-6-luna@low")
    run_id = "00000000-0000-0000-0000-000000000000"
    assert _exit([command, run_id, "--max-usd", "0.10"]) == 2
    assert "judge: openai:gpt-6-luna needs OPENAI_API_KEY" in capsys.readouterr().err


def test_invalid_configuration_is_reported_before_anything_runs(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "glm-5.3-flash")
    assert _exit(["doctor"]) == 2
    assert "Configuration is invalid" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["rescout", "synthesize"])
def test_reruns_refuse_what_cannot_run_before_opening_the_database(command, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    run_id = "00000000-0000-0000-0000-000000000000"
    model = "openai:gpt-6-luna" if command == "rescout" else "anthropic:claude-opus-5-5"
    assert _exit([command, run_id, "--model", model, "--max-usd", "1"]) == 2
    assert "must name its effort" in capsys.readouterr().err
    assert _exit([command, run_id, "--model", f"{model}@high", "--max-usd", "1", "--study", "../x"]) == 2
    assert "study name" in capsys.readouterr().err


def test_a_synthesis_refuses_a_model_that_is_not_claude_before_opening_the_database(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    run_id = "00000000-0000-0000-0000-000000000000"
    assert _exit(["synthesize", run_id, "--model", "openai:gpt-6-luna@high", "--max-usd", "1"]) == 2
    assert "must be an anthropic: model" in capsys.readouterr().err


def test_sigterm_mid_run_records_the_run_and_its_scouts_as_cancelled(tmp_path) -> None:
    # A study check stopped with SIGTERM left its run and a scout marked running (7b7d351c). In a
    # subprocess, since the KeyboardInterrupt the handler raises would end this test session.
    import subprocess
    import sys

    script = """
import asyncio, os, signal
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from research_loop.agents import planner_agent, scout_agent
from research_loop.cli import _interrupt_on_sigterm
from research_loop.config import Settings
from research_loop.scout import scout
from research_loop.store import MemoryStore

def plan(messages, info):
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"questions": [{"id": "a", "question": "Q?"}]})])

async def stall(messages, info):
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(30)

store = MemoryStore()
_interrupt_on_sigterm()
try:
    with planner_agent.override(model=FunctionModel(plan)), scout_agent.override(model=FunctionModel(stall)):
        asyncio.run(scout("Q?", settings=Settings(cache_dir=os.environ["CACHE"], cache_mode="off"), store=store))
except KeyboardInterrupt:
    print(sorted(run["status"] for run in store.runs.values()),
          sorted((call["role"], call["status"]) for call in store.calls.values()))
"""
    env = {"PATH": "/usr/bin:/bin", "CACHE": str(tmp_path), "OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k",
           "RESEARCH_LOGFIRE": "false", "DATABASE_URL": ""}
    done = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60,
                          check=False)
    assert done.stdout.strip() == "['cancelled'] [('planner', 'succeeded'), ('scout', 'cancelled')]", done.stderr
