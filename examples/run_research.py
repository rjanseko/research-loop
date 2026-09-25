"""Run one research objective and print the report.

The default synthetic policy runs the real graph without model or web calls; any
other policy calls paid model providers and needs --paid. With --persist, the job is
stored in Postgres (DATABASE_URL) like a benchmark run with --persist; --capture also
stores every agent's full messages there. With --report-dir, the report is also written
there as a document: PDF (through LaTeX) and Markdown by default, see --report-format.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

from research_loop import (
    POLICY_PRESETS,
    ResearchConfig,
    ResearchLoop,
    ResearchToolMode,
    get_policy,
)
from research_loop.db import open_migrated_pool
from research_loop.ledger import sources_markdown
from research_loop.observability import configure_logfire
from research_loop.render import FORMATS, LatexError, ReportDocument, write_report
from research_loop.repository import (
    InMemoryResearchRepository,
    PostgresResearchRepository,
)
from research_loop.settings import ResearchSettings
from research_loop.synthetic import SyntheticResearchLoop


async def run(objective: str, policy_name: str, tool_mode: str, settings: ResearchSettings,
              persist: bool, capture: bool = False, report_dir: Path | None = None,
              report_formats: tuple[str, ...] = ("pdf", "md")) -> None:
    configure_logfire(settings)
    async with AsyncExitStack() as stack:
        if persist:
            # Refuses before any model call if migrations are pending or changed.
            pool = await open_migrated_pool(stack, settings.database_dsn)
            repo = PostgresResearchRepository(pool, capture_transcripts=capture)
        else:
            repo = InMemoryResearchRepository()
        loop_class = SyntheticResearchLoop if policy_name == "synthetic" else ResearchLoop
        loop = loop_class(
            get_policy(policy_name, model_overrides=settings.model_overrides),
            ResearchConfig(
                tool_mode=ResearchToolMode(tool_mode),
                scholarly_cache_mode=settings.scholarly_cache_mode,
            ),
            repository=repo,
            settings=settings,
        )
        outcome = await loop.run(objective)

    print("\n=== FINAL REPORT ===\n")
    print(outcome.report.answer + sources_markdown(outcome.report, outcome.ledger))
    print("\n=== VERIFICATION ===")
    print(f"needs_research={outcome.verification.needs_research}")
    print(f"claims={len(outcome.verification.checks)}")
    if isinstance(repo, InMemoryResearchRepository):
        print(f"tasks={len(repo.tasks)} tool_events={len(repo.tool_events)} cost_usd={outcome.cost_usd}")
    else:
        print(f"job_id={outcome.job_id} cost_usd={outcome.cost_usd}")
    for reason in outcome.review_reasons:
        print(f"needs review: {reason}")
    if report_dir is not None:
        document = ReportDocument.from_outcome(outcome)
        # The JSON record first: `research-report` can render it again if the PDF fails.
        for name, path in write_report(document, report_dir, ["json"]).items():
            print(f"report {name}: {path}")
        try:
            written = write_report(document, report_dir, [f for f in report_formats if f != "json"])
        except LatexError as exc:
            print(f"report pdf not written: {exc}")
        else:
            for name, path in written.items():
                print(f"report {name}: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one research objective and print the report")
    parser.add_argument("objective")
    parser.add_argument("--policy", default="synthetic", choices=sorted(POLICY_PRESETS))
    parser.add_argument("--tool-mode", default="adaptive", choices=[mode.value for mode in ResearchToolMode])
    parser.add_argument("--paid", action="store_true", help="Allow policies that call paid model providers")
    parser.add_argument("--persist", action="store_true", help="Store the job in Postgres at DATABASE_URL")
    parser.add_argument("--capture", action="store_true",
                        help="With --persist, also store every agent's full messages (research_task_messages)")
    parser.add_argument("--report-dir", type=Path,
                        help="Also write the report, sources, and evidence ledger as documents here")
    parser.add_argument("--report-format", default="pdf,md",
                        help=f"Comma-separated formats for --report-dir: {', '.join(FORMATS)} (default: pdf,md)")
    args = parser.parse_args()
    report_formats = tuple(name.strip() for name in args.report_format.split(",") if name.strip())
    if unknown := [name for name in report_formats if name not in FORMATS]:
        parser.error(f"unknown report format(s) {', '.join(unknown)}; choose from {', '.join(FORMATS)}")
    if args.policy != "synthetic" and not args.paid:
        parser.error("real model policies require --paid")
    # Resolve settings first: it loads .env, which carries the model overrides and DATABASE_URL.
    settings = ResearchSettings.from_env()
    if args.persist and not settings.database_dsn:
        parser.error("--persist needs DATABASE_URL; see docs/setup.md#postgres")
    if args.capture and not args.persist:
        parser.error("--capture stores transcripts in Postgres; add --persist")
    asyncio.run(run(args.objective, args.policy, args.tool_mode, settings, args.persist, args.capture,
                    args.report_dir, report_formats))


if __name__ == "__main__":
    main()
