# Acquisition: web and scholarly tools

Scouts and deep dives gather evidence with three tool groups: web search and fetch, scholarly metadata and full text, and [local attachments](attachments.md). Tools return bounded, typed data to the model. They never write to the evidence ledger or Postgres and never call a model; the graph's serial record steps own the ledger, and the run's repository records tool telemetry.

| Module | Contents |
|---|---|
| `tools.py` | Tool modes and the search capability |
| `web.py` | Resilient DuckDuckGo search and `web_fetch` |
| `scholar.py` | Provider adapters and the five scholarly tools |
| `acquisition.py` | What both share: disk cache, per-job document memo, fetch windows, rate slots, the blocked-source policy, and the guarded HTTPS download |

## Tool modes

`ResearchConfig.tool_mode` decides how web tools reach the model:

- **`normalized`**: every model gets the same local DuckDuckGo search and `web_fetch`, and provider-native search is disabled. Benchmarks and campaigns always use this mode, so a policy comparison does not also compare search stacks.
- **`adaptive`** (the library default): provider-native web search and fetch where the model supports them, with local fallbacks.

Scholarly tools are added in both modes unless `ResearchConfig.scholarly_tools` is off.

## Web search

`duckduckgo_search` keeps PydanticAI's tool name, description, and results. DuckDuckGo drops connections and rate-limits under load, so searches share a process-wide slot of one per second, and a failed search is retried once. A second failure returns a `SearchUnavailable` result to the model, which can switch to scholarly tools or another query; the run continues.

## Scholarly tools

| Tool | Does |
|---|---|
| `scholar_search` | Search OpenAlex and arXiv (optionally Crossref) with optional publication-year bounds |
| `scholar_get` | Resolve a DOI, OpenAlex W ID, arXiv ID, or `acl:<Anthology ID>` |
| `scholar_references` | Works referenced by a DOI or OpenAlex W ID |
| `scholar_citations` | Works citing a DOI or OpenAlex W ID |
| `scholar_fetch` | Extract text from an open-access HTML page or PDF |

| Backend | Used for |
|---|---|
| OpenAlex | Discovery, W-ID lookup, citation filter, referenced W-IDs, open-access PDF location |
| Crossref | DOI lookup and optional bibliographic search, including licenses and update relations |
| arXiv | Search and ID lookup, with explicit preprint status and versioned IDs |
| ACL Anthology | Direct BibTeX lookup by Anthology ID |
| OpenCitations | DOI citations and references |
| OpenReview | Disabled; no authenticated adapter is configured |

Provider records stay separate. OpenAlex and Crossref may describe the same work differently, and a preprint and its later publication remain distinct records. Source citations can carry DOI, arXiv ID, OpenAlex ID, ACL ID, provider, publication status, full-text URL, and locator. Status rules are conservative: a DOI hint on an arXiv record does not make a preprint peer-reviewed, journal or conference metadata alone does not prove peer review, and ACL BibTeX records stay `unknown` until the venue is verified. Year bounds currently filter OpenAlex results only.

## Fetching

`web_fetch` and `scholar_fetch` return at most 12,000 characters per call, starting at `start` (default 0):

- A result with `next_start` has more text; call again with `start=next_start`. `total_chars` is the extracted length.
- Each research job keeps the full documents it fetched in memory, shared by all of its agents. Paging, and a second agent fetching the same URL, reuse the download in every cache mode. No job sees another job's documents.
- pypdf extracts the first 30 pages of a PDF. `extraction_truncated` marks a longer document, whose last window is therefore not the end of the paper.
- Manifests record this behavior as `fetch_version` 3. Version 1 returned only the first window; version 2 added paging but did not refuse [blocked sources](#blocked-sources).

Pages are extracted with Trafilatura, falling back to Beautiful Soup. `scholar_fetch` extracts PDFs with pypdf; set `GROBID_URL` (for example `http://127.0.0.1:8070`, local HTTP only) to try a GROBID `/api/processFulltextDocument` service first, falling back to pypdf if it fails.

Fetches send a `research-loop` User-Agent, because sites such as Wikimedia reject default library agents, and read at most 5 MB of decoded content. Scholarly metadata responses are streamed too and refused past 2 MB, so an oversized response is never read whole. Errors report the HTTP status, so the model can tell a block (403) from a missing page (404). Some sites, openai.com among them, answer 403 from bot protection; the fetcher reports it and does not work around it.

## URL safety

Fetched URLs must be public HTTPS without credentials. The original request and every redirect (at most three) are checked by resolving the host and requiring all of its addresses to be globally routable. A response that exceeds the size cap while streaming is refused, and so are unsupported content types.

The DNS check and the connection resolve separately, so DNS rebinding can pass the check. The rebound connection still needs a TLS certificate valid for the requested host name, because httpx verifies certificates, so local plain-HTTP or non-HTTP services such as GROBID and Postgres fail the handshake. The residual risk is a private HTTPS service presenting a certificate for an attacker-chosen host name; deployments facing hostile DNS should also restrict network egress.

## Blocked sources

A task can block sources: benchmark cases such as DeepResearch Bench II name URLs derived from the expert report behind their rubric, and `ResearchConstraints.blocked_urls` carries them. Each job turns them into a `SourcePolicy` (`acquisition.py`) with one explicit matching rule. A URL matches a blocked entry when, ignoring scheme, `www.`, letter case, fragment, and a trailing slash, it has the entry's host and the entry's path or a path beneath it. An entry with a query also needs that query, and an entry without a path blocks its whole host. An arXiv entry matches every form of the paper (abs, pdf, html, any version), and `doi.org` matches `dx.doi.org`. Matching errs toward blocking, since a missed block breaks a benchmark while an extra one only costs a page.

The policy is enforced at three points:

- **Fetches.** `web_fetch` and `scholar_fetch` refuse a blocked URL before the cache, the per-job memo, the DNS check, or any request, and the guarded download refuses a redirect to one before following it. The model receives a `BlockedSource` error naming the matched entry.
- **Evidence.** A scout or deep dive whose evidence cites a blocked source gets one retry asking it to drop that evidence, then fails the run.
- **Prompts.** Every role also receives the blocked URLs as constraints, as before.

Search results can still show blocked sources; seeing one is not a violation. The enforcement covers the normalized tool stack, which benchmarks and campaigns always use. In `adaptive` mode, provider-native search and fetch run outside the application and cannot be refused; the benchmark audit then reports any fetch of a blocked source they made as completed.

## Caching

`AcquisitionCache` stores metadata responses and fetch windows on disk under `RESEARCH_BENCHMARK_CACHE`:

| Mode | Reads | Writes | Used by |
|---|---|---|---|
| `live` | Entries up to one day old | Yes | Library runs (the `RESEARCH_SCHOLAR_CACHE_MODE` default) |
| `record` | No | Yes | Campaigns and the metadata pilot, so later runs can replay them |
| `replay` | Any age; a miss is an error | No | Offline reproduction |
| `off` | No | No | Benchmarks, so no result depends on an earlier run |

Entries are versioned and capped at 128 KB. Fetch windows are keyed by URL, `max_chars`, and `start`; a window from the start keeps its original key, so older recordings still replay. Fetches check the cache after the blocked-source check and before the DNS check, so `replay` works offline and never serves a blocked source.

## Telemetry and privacy

Persisted tool events keep hashes, IDs, counts, and errors, not article text (a fetch's error code and HTTP status are kept; its content is hashed): scholarly results keep work IDs, result counts, cache hits, and content hashes, and fetch results keep hashes and sizes. Text in tool arguments is stored as hashes and lengths. Benchmark evaluation keeps raw arguments in process memory only long enough to audit blocked URLs and benchmark-aware queries.

## Diagnostics

`research-diagnose` checks that the tools construct. `research-diagnose --scholar-live` probes five public metadata endpoints without model calls; it is a reachability check, not a guarantee that every query or full text is available. OpenAlex rate-limits anonymous search separately from other requests (HTTP 429 under load), so the probe runs a search, and a free `OPENALEX_API_KEY` makes search reliable. `CROSSREF_MAILTO` identifies the client to Crossref.

Provider documentation: [OpenAlex API](https://help.openalex.org/api/), [Crossref REST](https://api.crossref.org/), [arXiv API](https://info.arxiv.org/help/api/user-manual.html), [ACL Anthology](https://aclanthology.org/), [OpenCitations Index v2](https://api.opencitations.net/index/v2), and [GROBID service](https://grobid.readthedocs.io/en/latest/Grobid-service/).
