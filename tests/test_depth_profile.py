"""scripts/depth_profile.py: first-seen requests and the model/tool time split of one tool loop."""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from research_loop.schemas import Claim, Evidence, ResearchResult, SourceRef

_SPEC = importlib.util.spec_from_file_location("depth_profile", Path(__file__).parents[1] / "scripts" / "depth_profile.py")
depth_profile = importlib.util.module_from_spec(_SPEC)
sys.modules["depth_profile"] = depth_profile  # its dataclasses look their module up while being defined
_SPEC.loader.exec_module(depth_profile)


def _at(second: int) -> datetime:
    return datetime(2026, 9, 25, 12, 0, second, tzinfo=UTC)


def _result(*sources: SourceRef) -> ResearchResult:
    evidence = [Evidence(source=source, excerpt="x", confidence=0.5) for source in sources]
    return ResearchResult(question_id="q1", question="q", conclusion="c", confidence=0.5,
                          claims=[Claim(id="c1", statement="s", evidence=evidence, confidence=0.5)])


def test_sources_are_dated_by_the_request_whose_tools_returned_them() -> None:
    def call(i: str, url: str) -> ToolCallPart:
        return ToolCallPart("web_fetch", {"url": url}, tool_call_id=i)

    messages = [
        ModelRequest(parts=[UserPromptPart("q")], timestamp=_at(0)),
        ModelResponse(parts=[call("a", "https://example.org/a")], timestamp=_at(5)),         # request 1: 5 s model
        ModelRequest(parts=[ToolReturnPart("web_fetch", {"url": "https://example.org/a", "text": "A"},
                                           tool_call_id="a")], timestamp=_at(7)),          # 2 s tools
        ModelResponse(parts=[call("b", "https://doi.org/10.1/xyz")], timestamp=_at(15)),     # request 2: 8 s
        ModelRequest(parts=[ToolReturnPart("web_fetch", {"text": "published as doi 10.1/XYZ"},
                                           tool_call_id="b")], timestamp=_at(20)),         # 5 s tools
        ModelResponse(parts=[TextPart("done")], timestamp=_at(26)),                          # request 3: 6 s
    ]
    cited = _result(SourceRef(url="https://www.example.org/a?utm=1", title="A"),   # matched ignoring www and query
                    SourceRef(url="https://doi.org/10.1/xyz", title="B"),          # matched by DOI in the text
                    SourceRef(url="https://example.org/never", title="C"))
    requests, first_seen, model_s, tool_s = depth_profile.profile_loop(messages, cited)
    assert requests == 3
    assert first_seen == [1, 2, None]
    assert (model_s, tool_s) == (19.0, 7.0)


def test_render_marks_unmatched_sources_and_draws_the_curve() -> None:
    profiles = [depth_profile.LoopProfile("scout", "q1", 3, True, [1, 2, None], 19.0, 7.0, 30.0, "tokens")]
    text = depth_profile.render(profiles)
    assert "1, 2, -" in text and "tokens" in text
    assert "k=1:0.33  k=2:0.67  k=3:0.67" in text and "(1 not matched)" in text


def test_a_stopped_loop_names_the_limit_it_was_closest_to() -> None:
    config = {"total_tokens_limit": 400_000, "max_requests": 24, "max_tool_calls": 48, "cost_limit": 0.8}
    # The first pilot's scout q3: 17 of 24 requests, 31 of 48 tool calls, $0.19 of $0.80, but 424k tokens.
    assert depth_profile.stopping_limit({"total_tokens": 424_351, "requests": 17, "tool_calls": 31,
                                         "cost": 0.19}, config) == "tokens"
    assert depth_profile.stopping_limit({"total_tokens": 900_000, "requests": 24, "tool_calls": 30},
                                        config | {"total_tokens_limit": 2_000_000}) == "requests"
    assert depth_profile.stopping_limit({}, {}) == "unknown"


def test_a_deep_dive_counts_the_cited_sources_its_scout_had_returned() -> None:
    scout = [
        ModelRequest(parts=[UserPromptPart("q")], timestamp=_at(0)),
        ModelResponse(parts=[ToolCallPart("web_fetch", {"url": "https://example.org/a"}, tool_call_id="a")], timestamp=_at(1)),
        ModelRequest(parts=[ToolReturnPart("web_fetch", {"url": "https://example.org/a", "text": "A"},
                                           tool_call_id="a")], timestamp=_at(2)),
    ]
    scouted = depth_profile.ToolOutputIndex(depth_profile.loop_texts(scout))
    assert scouted.observed(SourceRef(url="https://www.example.org/a", title="A"))
    assert not scouted.observed(SourceRef(url="https://example.org/new", title="N"))
    profiles = [depth_profile.LoopProfile("deep_dive", "q1", 4, False, [1, 1, 2], 9.0, 3.0, from_scout=2),
                depth_profile.LoopProfile("scout", "q1", 3, False, [1], 5.0, 1.0)]
    lines = depth_profile.render(profiles).splitlines()
    assert "scouted" in lines[0] and "  2/3  " in lines[1] and "/" not in lines[2].split()[5]
