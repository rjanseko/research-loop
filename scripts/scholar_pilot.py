"""Free, bounded scholarly metadata probe for the first research campaign."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from research_loop.scholar import AcquisitionCache, ScholarClient
from research_loop.settings import ResearchSettings


async def main() -> None:
    settings = ResearchSettings.from_env()
    client = ScholarClient(
        cache=AcquisitionCache(settings.benchmark_cache / "scholarly", "record"),
        api_key=settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None,
        contact_email=settings.crossref_mailto,
    )
    search = await client.search("long horizon software engineering agents", 2024, 2026, 3, True, True)
    acl = await client.get("acl:2024.acl-long.1")
    doi = next((work.doi for work in search.works if work.doi), None)
    citations = await client.citations(doi, 3) if doi else None
    output = {
        "campaign": "long-horizon-agentic-se-2024-2026",
        "created_at": datetime.now(UTC).isoformat(),
        "query": "long horizon software engineering agents",
        "records": [
            {"provider": work.provider, "id": work.provider_id, "doi": work.doi,
             "title": work.title, "status": work.publication_status}
            for work in (search.works + acl.works)
        ],
        "citation_ids": [work.provider_id for work in citations.works] if citations else [],
        "errors": search.provider_errors + acl.provider_errors + (citations.provider_errors if citations else []),
    }
    path = settings.benchmark_output / "scholar_pilot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(f"Scholarly metadata pilot: {path} ({len(output['records'])} records, {len(output['errors'])} endpoint errors)")


if __name__ == "__main__":
    asyncio.run(main())
