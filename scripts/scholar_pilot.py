"""Free, bounded scholarly metadata probe for the first research campaign."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from research_loop.acquisition import AcquisitionCache
from research_loop.scholar import ScholarClient
from research_loop.settings import ResearchSettings

CAMPAIGN = "long-horizon-agentic-se-2024-2026"
QUERY = "long horizon software engineering agents"


async def main() -> None:
    settings = ResearchSettings.from_env()
    client = ScholarClient(
        cache=AcquisitionCache(settings.benchmark_cache / "scholarly", "record"),
        api_key=settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None,
        contact_email=settings.crossref_mailto,
    )
    search = await client.search(QUERY, year_from=2024, year_to=2026, limit=3, include_crossref=True)
    acl = await client.get("acl:2024.acl-long.1")
    doi = next((work.doi for work in search.works if work.doi), None)
    citations = await client.citations(doi, limit=3) if doi else None
    responses = [response for response in (search, acl, citations) if response]
    output = {
        "campaign": CAMPAIGN,
        "created_at": datetime.now(UTC).isoformat(),
        "query": QUERY,
        "records": [
            {"provider": work.provider, "id": work.provider_id, "doi": work.doi,
             "title": work.title, "status": work.publication_status}
            for work in search.works + acl.works
        ],
        "citation_ids": [work.provider_id for work in citations.works] if citations else [],
        "errors": [error for response in responses for error in response.provider_errors],
    }
    path = settings.benchmark_output / "scholar_pilot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(f"Scholarly metadata pilot: {path} ({len(output['records'])} records, {len(output['errors'])} endpoint errors)")


if __name__ == "__main__":
    asyncio.run(main())
