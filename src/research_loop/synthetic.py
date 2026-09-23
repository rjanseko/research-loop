from __future__ import annotations

import json
from typing import Any

from .orchestrator import ResearchLoop
from .schemas import (
    Claim,
    ClaimCheck,
    Evidence,
    FinalReport,
    GapAnalysis,
    ReportClaim,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    ResearchRole,
    SourceRef,
    ToolEvent,
    VerificationReport,
)


class SyntheticResearchLoop(ResearchLoop):
    """Exercise the real graph and repository without provider or web calls."""

    async def _run_agent(self, **kwargs: Any) -> Any:
        role: ResearchRole = kwargs["role"]
        payload = json.loads(kwargs["prompt"])
        route = kwargs["route"]
        task_id = await self.repository.start_task(
            job_id=kwargs["job_id"],
            parent_task_id=kwargs.get("parent_task_id"),
            role=role,
            question_id=kwargs.get("question_id"),
            prompt=kwargs["prompt"],
            model_id=route.model,
            effective_config={**route.snapshot(), "synthetic": True},
            attempt=kwargs.get("attempt", 0),
        )
        if kwargs.get("task_ids") is not None:
            kwargs["task_ids"].append(task_id)
        try:
            if role is ResearchRole.PLANNER:
                output = ResearchPlan(
                    objective=payload["objective"],
                    questions=[ResearchQuestion(id="synthetic-q1", question="Synthetic evidence check")],
                )
            elif role in {ResearchRole.SCOUT, ResearchRole.DEEP_DIVE}:
                question = payload["question"]
                output = ResearchResult(
                    question_id=question["id"],
                    question=question["question"],
                    conclusion="Synthetic evidence is available.",
                    claims=[Claim(
                        id="synthetic-claim-1",
                        statement="Synthetic evidence is available.",
                        evidence=[Evidence(
                            source=SourceRef(url="https://example.com/synthetic", title="Synthetic source", source_type="primary"),
                            excerpt="Synthetic fixture only.",
                            confidence=1.0,
                        )],
                        confidence=1.0,
                    )],
                    confidence=1.0,
                )
                await self.repository.record_tool_events(task_id, [
                    ToolEvent(tool_name="synthetic_search", args={"query": "synthetic fixture"}, result={"count": 1}, outcome="ok")
                ])
            elif role is ResearchRole.GAP_ANALYST:
                output = GapAnalysis()
            elif role is ResearchRole.SYNTHESIZER:
                # Cite the ledger's claim IDs as given in the prompt; the ledger renames claims.
                # The prompt leaves out `claims` for a result that has none.
                claim_ids = [claim["id"] for result in payload["evidence"] for claim in result.get("claims", [])]
                output = FinalReport(
                    answer="Synthetic evidence is available.",
                    claims=[ReportClaim(statement="Synthetic evidence is available.", claim_ids=claim_ids)],
                )
            elif role is ResearchRole.VERIFIER:
                output = VerificationReport(checks=[ClaimCheck(
                    statement="Synthetic evidence is available.",
                    claim_ids=payload["report"]["claims"][0]["claim_ids"],
                    supported=True,
                    severity="none",
                    explanation="Supported by synthetic fixture.",
                )])
            else:
                raise ValueError(f"unsupported synthetic role {role}")
            await self.repository.finish_task(
                task_id,
                status="succeeded",
                output=output.model_dump(mode="json"),
                usage={"requests": 0, "total_tokens": 0, "tool_calls": 0},
                agent_run_id=None,
                conversation_id=None,
            )
            return output
        except Exception as exc:
            await self.repository.finish_task(
                task_id,
                status="failed",
                output=None,
                usage=None,
                agent_run_id=None,
                conversation_id=None,
                error={"type": type(exc).__name__},
            )
            raise
