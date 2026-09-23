# Scholarly acquisition

Scout and deep-dive workers see five provider-neutral tools: `scholar_search`, `scholar_get`, `scholar_references`, `scholar_citations`, and `scholar_fetch`. They return bounded typed data. The existing serial graph steps record evidence; adapters do not write to `EvidenceLedger` or Postgres, call models, or change `research-graph-v1`.

Install `pip install -e '.[scholarly]'`. `research-diagnose` checks local construction; `research-diagnose --scholar-live` probes five public metadata endpoints with no model calls. The live probe is a reachability check, not a guarantee that every query or full text will be available.

| Backend | Current use |
| --- | --- |
| OpenAlex | Discovery, W-ID lookup, citation filter, referenced W-IDs, OA PDF location |
| Crossref | DOI lookup and optional bibliographic search, including license and update relations |
| arXiv | Search and ID lookup, with explicit preprint status and versioned IDs |
| ACL Anthology | Direct Anthology-ID BibTeX lookup (`acl:<id>`) |
| OpenCitations | DOI citations and references |
| OpenReview | Explicitly disabled; no authenticated adapter is configured |

OpenAlex and Crossref may describe the same title differently. Each provider record remains separate, including preprints and later publications. Source citations can preserve DOI, arXiv ID, OpenAlex ID, ACL ID, provider, status, full-text URL, and locator. A DOI hint on arXiv does not upgrade a preprint to peer-reviewed work. Journal and conference metadata alone do not prove peer review. ACL BibTeX records currently have `unknown` publication status pending venue verification.

The normalized web lane uses DuckDuckGo search and `web_fetch` with Trafilatura, then a Beautiful Soup fallback. It rejects local and non-HTTPS URLs, checks redirects, caps response size and returned text, and refuses unsupported content types. DNS is checked before each request and redirect; deployments facing hostile DNS should also enforce network egress restrictions. PDF text extraction in `scholar_fetch` uses pypdf by default. Set `GROBID_URL=http://127.0.0.1:8070` to try a local GROBID `/api/processFulltextDocument` service first; failure falls back to pypdf. The service must be local HTTP. Full text is returned in bounded tool output, while persisted tool telemetry keeps hashes, IDs, counts, and errors rather than article text.

`RESEARCH_SCHOLAR_CACHE_MODE` supports `live` (read/write), `record` (refresh/write), `replay` (cache only), and `off` (no cache). Regular runs default to `live`; benchmarks use `off` and record this in the experiment manifest. Cache entries are versioned, expire after one day in live mode (replay keeps its fixed snapshot), and have a 128 KB per-entry cap. Benchmark evaluations keep raw search arguments only in temporary process memory to audit blocked URLs and benchmark-aware queries; Postgres stores hashes and lengths.

The free campaign metadata pilot is `PYTHONPATH=src python scripts/scholar_pilot.py`. It writes only work IDs, titles, statuses, and endpoint errors to ignored `benchmark_outputs/scholar_pilot.json`. The campaign scope and source policy are in [campaign.toml](campaigns/long_horizon_agentic_se/campaign.toml); `research-campaign --dry-run` validates it without model calls.

Provider documentation: [OpenAlex API](https://help.openalex.org/api/), [Crossref REST](https://api.crossref.org/), [arXiv API](https://info.arxiv.org/help/api/user-manual.html), [ACL Anthology](https://aclanthology.org/), [OpenCitations Index v2](https://api.opencitations.net/index/v2), and [GROBID service](https://grobid.readthedocs.io/en/latest/Grobid-service/).
