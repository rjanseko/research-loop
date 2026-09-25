from __future__ import annotations

from uuid import uuid4

import logfire

from research_loop.telemetry import run_span, trace_id


def test_every_span_in_a_run_carries_its_run_id(capfire) -> None:
    run_id = uuid4()
    with run_span(run_id, "scout", "scout-v1", question_count=2) as span:
        with logfire.span("scout {question_id}", question_id="q1"):
            pass
        recorded = trace_id(span)
    spans = {item["name"]: item["attributes"] for item in capfire.exporter.exported_spans_as_dict()}
    run, child = spans["research run {mode} {run_id}"], spans["scout {question_id}"]
    assert (run["run_id"], run["mode"], run["workflow_version"], run["question_count"]) == (
        str(run_id), "scout", "scout-v1", 2)
    assert child["run_id"] == str(run_id)  # baggage reaches spans inside the run
    assert recorded is not None and len(recorded) == 32


def test_no_span_means_no_trace_id() -> None:
    assert trace_id(None) is None
