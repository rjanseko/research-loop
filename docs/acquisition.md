# Acquisition: web and scholarly tools

Scouts and deep dives gather evidence with three tool groups: web search and fetch, scholarly metadata and full text, and [local attachments](attachments.md). Tools return bounded, typed data to the model. They never write to the evidence ledger or Postgres and never call a model; the graph's serial record steps own the ledger, and the run's repository records tool telemetry.

| Module | Contents |
|---|---|
| `tools.py` | Tool modes and the search capability |
| `web.py` | Resilient DuckDuckGo search and `web_fetch` |
| `scholar.py` | Provider adapters and the five scholarly tools |
| `citations.py` | [Citation snowballing](#citation-snowballing-basis-papers) over a study's bibliography; not a research tool |
| `acquisition.py` | What both share: disk cache, per-job document memo, fetch windows, rate slots, the blocked-source policy, and the guarded HTTPS download |

## Tool modes

`ResearchConfig.tool_mode` decides how web tools reach the model:

- **`normalized`**: every model gets the same local DuckDuckGo search and `web_fetch`, and provider-native search is disabled. Benchmarks and long-horizon runs always use this mode, so a policy comparison does not also compare search stacks.
- **`adaptive`** (the library default): provider-native web search and fetch where the model supports them, with local fallbacks. The local fetch fallback is PydanticAI's `web_fetch`, which raises on any HTTP error instead of returning it; after its one retry the worker gives up (`UnexpectedModelBehavior`), which fails the run unless `salvage_exhausted_research` is on. The normalized `web_fetch` returns the error to the model.

Scholarly tools are added in both modes unless `ResearchConfig.scholarly_tools` is off.

## Web search

`duckduckgo_search` keeps PydanticAI's tool name, description, and results. DuckDuckGo drops connections and rate-limits under load, so searches share a process-wide slot of one per second, and a failed search is tried twice more, after 2 and 6 seconds. A third failure returns a `SearchUnavailable` result to the model, which can switch to scholarly tools or another query; the run continues. A query that finds nothing, which the search library raises as an error, returns no results and a hint to use fewer or broader terms, without a retry. In the settings-study pilots 279 of 697 searches failed, about a third of them for finding nothing; reported as failures, they sent scouts looking for other search engines. Successful searches are [cached](#caching) by exact query and served unchanged, so a replayed search gives the model the same results a live one did; failures are never cached.

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
| Semantic Scholar | [Citation snowballing](#citation-snowballing-basis-papers) only; not a research tool |

Provider records stay separate. OpenAlex and Crossref may describe the same work differently, and a preprint and its later publication remain distinct records. Source citations can carry DOI, arXiv ID, OpenAlex ID, ACL ID, provider, publication status, full-text URL, and locator. Status rules are conservative: a DOI hint on an arXiv record does not make a preprint peer-reviewed, journal or conference metadata alone does not prove peer review, and ACL BibTeX records stay `unknown` until the venue is verified. Year bounds filter every search provider: OpenAlex by publication date, Crossref with `from-pub-date` and `until-pub-date`, and arXiv by the first version's submission date. arXiv results are also checked against the bounds after they return; an undated record is kept.

## Fetching

`web_fetch` and `scholar_fetch` return at most 12,000 characters per call, starting at `start` (default 0):

- A result with `next_start` has more text; call again with `start=next_start`. `total_chars` is the extracted length.
- Each research job keeps the full documents it fetched in memory, shared by all of its agents. Paging, and a second agent fetching the same URL, reuse the download in every cache mode. No job sees another job's documents.
- Each research job also opens two HTTP clients on first use and closes them when it ends: one for scholarly metadata APIs and one for downloads, which connects only to [public addresses](#url-safety). Its agents share them, so requests reuse connections, and every client shares one TLS context. Provider rate slots apply to every request, whichever client sends it.
- pypdf extracts the first 30 pages of a PDF. PDF and HTML parsing run in worker threads, so a long document does not stall a job's other agents. `extraction_truncated` marks a longer document, whose last window is therefore not the end of the paper.
- Both fetches read PDFs. A PDF is known by its declared type or, since servers often send one as `application/octet-stream`, by its first bytes (`%PDF-`). Before version 5, `web_fetch` read HTML only and refused a PDF as an unsupported content type, which scouts and deep dives ran into ten times across three settings-study pilots.
- A site that answers `web_fetch` with 403 is asked once more as a browser, with a browser's User-Agent and Accept headers; the fetcher otherwise identifies itself, as Wikimedia requires. Of 12 sites that refused the pilots' fetches, 5 served the page to a browser and none to the fetcher's own User-Agent with browser Accept headers.
- A 404 or 410 result carries a hint to find the page by search instead of guessing its address: most of the pilots' 108 were addresses a model made up. After two connection failures (refused or timed out) to one host, a job's fetches return `HostUnreachable` for it at once, with a hint to use another source.
- Manifests record this behavior as `fetch_version` 6. Version 5 retried no 403, gave no hints, kept trying unreachable hosts, and tried a failed search only twice, reporting a search with no results as unavailable. Version 4 read PDFs only through `scholar_fetch` and only when declared as `application/pdf`; version 1 returned only the first window; version 2 added paging but did not refuse [blocked sources](#blocked-sources); version 3 applied `scholar_search` year bounds to OpenAlex only.

Pages are extracted with Trafilatura, falling back to Beautiful Soup. `scholar_fetch` extracts PDFs with pypdf; set `GROBID_URL` (for example `http://127.0.0.1:8070`, local HTTP only) to try a GROBID `/api/processFulltextDocument` service first, falling back to pypdf if it fails.

Fetches send a `research-loop` User-Agent, because sites such as Wikimedia reject default library agents, and read at most 5 MB of decoded content. Scholarly metadata responses are streamed too and refused past 2 MB, so an oversized response is never read whole. Errors report the HTTP status, so the model can tell a block (403) from a missing page (404). Some sites, openai.com among them, answer 403 from bot protection; the fetcher reports it and does not work around it.

## URL safety

Fetched URLs must be public HTTPS without credentials. The original request and every redirect (at most three) are checked by resolving the host and requiring all of its addresses to be globally routable. A response that exceeds the size cap while streaming is refused, and so are unsupported content types.

The connection is checked too, so DNS rebinding cannot slip past the first check. The fetch client resolves the host once when it connects, refuses unless every address is globally routable, and connects to one of those addresses; TLS still verifies the certificate against the URL's host name. When an HTTPS proxy is set in the environment (`HTTPS_PROXY` or `ALL_PROXY`), the proxy resolves and connects instead, so only the URL check applies and the proxy is the egress boundary. A caller-supplied `httpx` client is used as given.

## Blocked sources

A task can block sources: benchmark cases such as DeepResearch Bench II name URLs derived from the expert report behind their rubric, and `ResearchConstraints.blocked_urls` carries them. Each job turns them into a `SourcePolicy` (`acquisition.py`) with one explicit matching rule. A URL matches a blocked entry when, ignoring scheme, `www.`, letter case, fragment, and a trailing slash, it has the entry's host and the entry's path or a path beneath it. An entry with a query also needs that query, and an entry without a path blocks its whole host. An arXiv entry matches every form of the paper (abs, pdf, html, any version), and `doi.org` matches `dx.doi.org`. Matching errs toward blocking, since a missed block breaks a benchmark while an extra one only costs a page.

The policy is enforced at three points:

- **Fetches.** `web_fetch` and `scholar_fetch` refuse a blocked URL before the cache, the per-job memo, the DNS check, or any request, and the guarded download refuses a redirect to one before following it. The model receives a `BlockedSource` error naming the matched entry.
- **Evidence.** A scout or deep dive whose evidence cites a blocked source gets one retry asking it to drop that evidence, then fails the run.
- **Prompts.** Every role also receives the blocked URLs as constraints, as before.

Search results can still show blocked sources; seeing one is not a violation. The enforcement covers the normalized tool stack, which benchmarks and long-horizon runs always use. In `adaptive` mode, provider-native search and fetch run outside the application and cannot be refused; the benchmark audit then reports any fetch of a blocked source they made as completed.

## Caching

`AcquisitionCache` stores searches, metadata responses, and fetch windows on disk under `RESEARCH_BENCHMARK_CACHE`, all in the mode `RESEARCH_SCHOLAR_CACHE_MODE` or a study's `scholarly_cache_mode` names:

| Mode | Reads | Writes | Used by |
|---|---|---|---|
| `live` | Entries up to one day old | Yes | Library runs (the `RESEARCH_SCHOLAR_CACHE_MODE` default) |
| `record` | No | Yes | Long-horizon runs and the metadata pilot, so later runs can replay them |
| `replay` | Any age; a miss is an error | No | Offline reproduction |
| `reuse` | Any age | Yes | Replays that change a model or a limit: recorded calls are served, and a call the recording lacks, such as a new query, goes live and is recorded |
| `off` | No | No | Benchmarks, so no result depends on an earlier run |

Entries are versioned and capped at 128 KB. Searches are keyed by the query as search engines read it: Unicode-normalized, case-folded, with whitespace collapsed, so `SWE-bench  Verified` and `swe-bench verified` share an entry. Quotes, operators such as `site:`, punctuation, and word order are kept, because they change results. Nothing looser, such as matching similar wording, is used: that would give a model results for a query it never made. Each entry also keeps the query as the model wrote it. Fetch windows are keyed by URL, `max_chars`, and `start`; a window from the start keeps its original key, so older recordings still replay. Fetches check the cache after the blocked-source check and before the DNS check, so `replay` works offline and never serves a blocked source.

## Citation snowballing (basis papers)

`research-long-horizon --basis-papers` finds the works a study's literature builds on, and the later work built on it (`citations.py`). It is code, not a tool: no model is called, and research workers never see the result.

1. **Seeds.** Every bibliography entry of the study's completed questions with an arXiv ID or DOI, from its fields or its URL, becomes a Semantic Scholar lookup. An arXiv DOI (`10.48550/arXiv.…`) is looked up by its arXiv ID. Web pages without either are counted and skipped.
2. **Resolution.** Seeds are resolved in batches of 100 (`POST /graph/v1/paper/batch`) with their reference lists. A seed whose ID Semantic Scholar does not index, such as some ACL Anthology DOIs, is tried once by title and accepted only when the returned title is the same, ignoring case and punctuation. Two lookups for one paper, such as a preprint and its publication, count as one seed.
3. **Ranking.** Each referenced work is counted once per seed that cites it. Works cited by at least two seeds are ranked by that count, then by total citations. Each is marked `in_study` or not, since works the study never cited are what snowballing adds.
4. **Forward snowballing.** Each resolved seed's citing works are read, newest first, 500 per request, up to 1,000 per seed. Works citing at least two seeds are ranked by how many they cite, then by total citations, and marked `in_study` the same way. A seed cited more than 1,000 times has only its newest citing works read, so the list leans toward recent work; the report counts those seeds. Forward snowballing is best effort: a seed whose citations are still throttled after retries is counted and skipped, and a rerun fills it in.
5. **Output.** `<output>/synthesis/basis_papers.json` and `basis_papers.md`, with the counts behind the ranking: seeds without identifiers, seeds not found, seeds found by title, seeds with no references, and references that matched no paper.

Semantic Scholar is used because its records carry references for arXiv preprints. OpenAlex lists none for them, and most of this literature is on arXiv. Without a key, requests share a public rate limit and are often throttled; the client backs off and retries up to five times, waiting as long as 30 seconds. A free `SEMANTIC_SCHOLAR_API_KEY` gives a dedicated limit. Responses are cached per paper and per citation page. A study in `record` mode reads recent entries for this step, so a rerun after throttling resumes instead of starting over; `replay` works offline.

## Telemetry and privacy

A tool that cannot reach its source returns the error to the model and the run continues, so a blocked network does not fail a run on its own. Each job therefore counts its web and scholarly tool calls that reached no source: a fetch that failed on the network (an httpx transport error, such as `ProxyError` or `ConnectTimeout`), a search that failed twice on a timeout (`SearchUnavailable`), or a scholarly call (any `scholar_*` tool) that returned nothing because a provider failed on the network. HTTP statuses, blocked sources, and empty results reached their source and do not count. Nor does `SearchUnavailable (DDGSException)`: the search library raises it both when every engine failed and when a search found nothing, and the result does not say which, so a blocked search shows up in `research-diagnose --network` rather than here. When at least three calls, and at least half of them, reached no source, the job's `review_reasons` say so, for example `9 of 12 web and scholarly tool calls reached no source (ProxyError)`. Attachment tools are local and are not counted.

Each persisted tool event records when the model response that issued it arrived (`called_at`) and when its result returned (`returned_at`), so a task's time splits into model and tool time. It also records whether a search or fetch was served from the cache (`cache_hit`). A search reports that in PydanticAI metadata that is never sent to the model, so the model sees the same result whether it was served or live. Persisted tool events keep hashes, IDs, counts, and errors, not article text (a fetch's error code and HTTP status are kept; its content is hashed): scholarly results keep work IDs, result counts, cache hits, and content hashes, and fetch results keep hashes and sizes. Text in tool arguments is stored as hashes and lengths. Benchmark evaluation keeps raw arguments in process memory only long enough to audit blocked URLs and benchmark-aware queries. Runs that opt in to [full transcripts](setup.md#full-transcripts) also store the real arguments and results, in `research_task_messages`; benchmarks never do.

## Diagnostics

`research-diagnose` checks that the tools construct. `research-diagnose --scholar-live` probes five public metadata endpoints without model calls; it is a reachability check, not a guarantee that every query or full text is available. OpenAlex rate-limits anonymous search separately from other requests (HTTP 429 under load), so the probe runs a search, and a free `OPENALEX_API_KEY` makes search reliable. `CROSSREF_MAILTO` identifies the client to Crossref.

`research-diagnose --network` checks, without model calls, that the scholarly APIs, the search engines the web search tool uses, and ordinary public sites can be reached, separating a proxy's refusal from other failures; see [setup.md](setup.md#checking-readiness).

Provider documentation: [OpenAlex API](https://help.openalex.org/api/), [Crossref REST](https://api.crossref.org/), [arXiv API](https://info.arxiv.org/help/api/user-manual.html), [ACL Anthology](https://aclanthology.org/), [OpenCitations Index v2](https://api.opencitations.net/index/v2), and [GROBID service](https://grobid.readthedocs.io/en/latest/Grobid-service/).
