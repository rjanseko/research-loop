"""Whether the quotes behind a report's statements say what the statements say, judged by a model.

Code checks that a quote appears in the source it cites (evidence.py), not that it supports the claim,
and the synthesizer restates claims in its own words. This audit gives a model outside the workflow,
by default from a third vendor, each report statement with the verified quotes behind it, and asks
whether they state it. Each quoted source comes with the record the research tools returned for it
(tools.source_records), so details that identify a source, such as its authors, date, or address,
can be checked too; nothing a model wrote about a source is sent. A statement with no verified quote
is marked `no_quote` by code and not sent: there is no source text to judge it against. The audit reads stored runs and never changes them; every
audit, a failed one too, is recorded in the `support_audits` table with its usage, cost, and messages.
It calls a paid model, from `research audit`, never from the tests.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, capture_run_messages
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from .config import Settings, split_model
from .evidence import EvidenceLedger, source_identity
from .models import build_model
from .schemas import FinalReport
from .store import error_record, transcript, usage_record
from .study_budget import BUDGET_POLICY_VERSION, StudyBudget, StudyBudgetModel

# Recorded with every audit; bump when the instructions, the verdict schema, or what is sent changes.
# 2: each quoted source's record as the tools returned it; v1 sent only a model-written title, so author
# names, dates, and addresses a report took from a source's record counted as unsupported.
AUDIT_VERSION = 2
AUDIT_INSTRUCTIONS = """\
You check a research report against its evidence. For each statement you get the verified quotes
behind it: words copied exactly from the sources, which code has confirmed appear in the cited source.
Each quote names its source, and `sources` gives the record the research tools returned for each one,
such as its title, address, authors, date, and venue. Judge each statement only from its quotes and
the records of the sources they come from, never from what you know. A record supports only details
that identify its source, such as who wrote it, when, where it appeared, or its address; claims about
the world need a quote.

- supported: the quotes, read together, state what the statement says. Rewording is fine; numbers,
  dates, names, scope, and how certain the claim is must match.
- partial: the quotes support part of the statement, but it adds something they do not state, such
  as a number, a scope, a comparison, a cause, or more certainty than the quotes show.
- unsupported: the quotes do not state what the statement says, or they contradict it.

Give a one-sentence reason; for partial or unsupported, say what the quotes do not state. Return one
verdict for every statement ID, and no others.
"""
Verdict = Literal["supported", "partial", "unsupported"]
# Not a judgment: code found no verified quote behind the statement, so it was not sent.
NO_QUOTE = "no_quote"


class StatementVerdict(BaseModel):
    id: str
    verdict: Verdict
    reason: str = Field(description="One sentence; for partial or unsupported, what the quotes do not state")


class AuditOutput(BaseModel):
    verdicts: list[StatementVerdict]


def audit_items(report: FinalReport, ledger: EvidenceLedger) -> list[dict[str, Any]]:
    """Each report statement with the verified quotes of the supporting evidence behind its claims, IDs a1,
    a2, ...; each quote names its source by ledger ID, and `source_ref` keeps the source for `source_view`."""
    claims = ledger.claims_by_id()
    items = []
    for number, statement in enumerate(report.claims, 1):
        quotes: list[dict[str, Any]] = []
        for claim_id in statement.claim_ids:
            claim = claims.get(claim_id)
            for item in claim.evidence if claim else []:
                entry = {"claim": claim_id, "source": ledger.source_id(item.source), "quote": item.quote or "",
                         "source_ref": item.source}
                if (item.supports and item.quote_check == "verified" and item.quote
                        and not any((q["source"], q["quote"]) == (entry["source"], entry["quote"]) for q in quotes)):
                    quotes.append(entry)
        items.append({"id": f"a{number}", "statement": statement.statement, "claim_ids": list(statement.claim_ids),
                      "quotes": quotes})
    return items


def source_view(items: list[dict[str, Any]], records: list[tuple[frozenset[str], dict[str, Any]]]) -> dict[str, Any]:
    """Every quoted source's record as the tools returned it, merged across the tools that returned it,
    by ledger source ID; a source no tool record matches gets an empty record."""
    view: dict[str, dict[str, Any]] = {}
    for item in items:
        for quote in item["quotes"]:
            keys = source_identity(quote["source_ref"])
            merged: dict[str, Any] = {}
            for record_keys, record in records:
                if keys & record_keys:
                    merged |= {name: value for name, value in record.items() if name not in merged}
            view[quote["source"]] = merged
    return view


@dataclass
class AuditRecord:
    """One audit of one run's report: what the `support_audits` table stores."""

    run_id: UUID
    status: str
    judge_model: str
    judge_thinking: str
    evidence_version: int | None = None
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    usage: RunUsage | None = None
    messages: list[ModelMessage] = field(default_factory=list)
    error: BaseException | None = None
    budget_cap_usd: Decimal | None = None
    reserved_usd: Decimal | None = None
    id: UUID = field(default_factory=uuid4)

    @property
    def cost_usd(self) -> Decimal | None:
        return self.usage.cost if self.usage else None

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(verdict["verdict"] for verdict in self.verdicts))


async def audit(report: FinalReport, ledger: EvidenceLedger, question: str, run_id: UUID, settings: Settings, *,
                records: list[tuple[frozenset[str], dict[str, Any]]] = (), evidence_version: int | None = None,
                model: Model | None = None, budget: StudyBudget | None = None) -> AuditRecord:
    """Audit one report with `settings.models.judge`; a failure is returned, not raised. `records` are what the
    run's research tools returned about its sources (tools.source_records); `model` is for tests."""
    judge_model, judge_thinking = split_model(settings.models.judge)
    items = audit_items(report, ledger)
    sent = [item for item in items if item["quotes"]]
    record = AuditRecord(run_id, "succeeded", judge_model, judge_thinking, evidence_version,
                         budget_cap_usd=budget.cap_usd if budget else None)
    verdicts: dict[str, StatementVerdict] = {}
    if sent:
        expected = {item["id"] for item in sent}
        agent = Agent(output_type=AuditOutput, instructions=AUDIT_INSTRUCTIONS)

        @agent.output_validator
        def every_statement_once(output: AuditOutput) -> AuditOutput:
            given = [verdict.id for verdict in output.verdicts]
            missing, extra = sorted(expected - set(given)), sorted(set(given) - expected)
            if missing or extra or len(given) != len(set(given)):
                raise ModelRetry(f"Give exactly one verdict per statement ID. Missing: {missing}; unknown: {extra}.")
            return output

        prompt = json.dumps({"question": question, "sources": source_view(sent, list(records)), "statements": [
            {"id": item["id"], "statement": item["statement"],
             "quotes": [{"source": quote["source"], "quote": quote["quote"]} for quote in item["quotes"]]}
            for item in sent]}, ensure_ascii=False)
        chosen = model or build_model(settings.models.judge, "scout", settings, sdk_retries=0 if budget else None)
        if budget is not None:
            chosen = StudyBudgetModel(chosen, judge_model, budget)
        model_settings: dict[str, Any] = {"thinking": judge_thinking, "timeout": settings.model_calls.audit_timeout_seconds}
        if budget is not None:
            model_settings["max_tokens"] = settings.model_calls.audit_max_output_tokens
        usage = RunUsage()
        messages: list[ModelMessage] = []
        try:
            with capture_run_messages() as messages:
                result = await agent.run(prompt, model=chosen, usage=usage, model_settings=model_settings)
        except Exception as exc:  # noqa: BLE001 - a failed audit is recorded with what it cost, then reported
            record.status, record.error, record.usage, record.messages = "failed", exc, usage, list(messages)
            record.reserved_usd = budget.reserved_usd if budget else None
            return record
        verdicts = {verdict.id: verdict for verdict in result.output.verdicts}
        record.usage, record.messages = usage, result.all_messages()
    record.reserved_usd = budget.reserved_usd if budget else None
    record.verdicts = [
        {"id": item["id"], "statement": item["statement"], "claim_ids": item["claim_ids"], "quotes": len(item["quotes"]),
         "verdict": verdicts[item["id"]].verdict if item["id"] in verdicts else NO_QUOTE,
         "reason": verdicts[item["id"]].reason if item["id"] in verdicts else "no verified quote behind this statement"}
        for item in items]
    return record


def audit_row(record: AuditRecord) -> dict[str, Any]:
    """The `support_audits` row for `record`, in the shape Postgres stores it."""
    return {
        "id": record.id, "run_id": record.run_id, "audit_version": AUDIT_VERSION,
        "evidence_version": record.evidence_version, "judge_model": record.judge_model,
        "judge_thinking": record.judge_thinking, "status": record.status, "verdicts": record.verdicts,
        "counts": record.counts, "usage": usage_record(record.usage) if record.usage else None,
        "cost_usd": record.cost_usd, "messages": transcript(record.messages) if record.messages else None,
        "error": error_record(record.error) if record.error else None,
        "budget_cap_usd": record.budget_cap_usd, "reserved_usd": record.reserved_usd,
        "budget_policy": BUDGET_POLICY_VERSION if record.budget_cap_usd is not None else None,
    }
