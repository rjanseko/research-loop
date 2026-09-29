# Notes: ideas not yet measured

This file keeps ideas that came up during study work and that have some evidence behind them but no
decision yet. Each note says what was seen, what the idea would change, and what would have to be measured
before building it. When an idea is tested, its result goes in [study-log.md](study-log.md) and the note is
updated to point there. Nothing here is a plan; a new run role still needs the user's approval, as AGENTS.md
requires.

## Cache warming

"Cache" means three different things in this repository, and warming each one would help in a different
way.

### The provider's prompt cache

**Resolved 28 September 2026.** History trimming was turned off by default after `trim-history-rescout-task8` (untrimmed scouts read 59% of input from the cache against 36%), and its code was removed in scout-v15. The notes below are kept as they were written.

On 28 September 2026 the scouts of the three standard drb2-task8 runs `aea52be0`, `8f753940`, and
`27701a2f` (scout-v10) read only 41%, 56%, and 49% of their input tokens from OpenAI's prompt cache. A tool
loop resends its whole history on every request, so nearly all of it could be a cached prefix. The likely
cause is history trimming (`history.py`): the cached prefix survives only up to the oldest page the latest
request trimmed, so each newly trimmed page makes everything after it uncached again.

On Luna the loss is small, about $0.04 a run at $0.10 a million input tokens. It matters much more on
models whose uncached input costs many times their cached input. DeepSeek V4 Pro lists $1.32 a million
uncached and $0.044 cached, so the same hit rate could cost about $0.50 more a run than a well-cached loop.
A DeepSeek comparison should therefore report each arm's cache hit rate, because a cost difference may come
from trimming rather than from the model.

What might help, in order of cost:
- **Trim in steps.** Trim several old pages at once, less often, so the prefix stays stable for several
  requests between trims. This changes what a model sees, so it is a behavior change with a version bump,
  and it must be checked against the quote losses that trimming every page caused (`history.py` records
  46 of 66 quotes verified against 54 of 55).
- **Warm the shared prefix.** Parallel scouts start together, so none of them reads the instructions and
  plan that the others' first requests are writing to the cache. That prefix is a few thousand tokens,
  small next to a scout's 200,000, so warming it is probably not worth a sequential first request.

Across stored Luna@high scouts, untrimmed versions (scout-v6, v9, research-v3) read 67 to 74% of their
input from the cache, and trimmed ones (v10, followup-v11) 43 to 49%. Trimming now starts early: pages
past the newest 120,000 characters are trimmed, and since fetch version 16 a page is up to 40,000
characters, so from about the fourth page most requests trim one more. Trimming was added for throughput
when the pacer assumed 200,000 tokens a minute, ten times too low, so its main reason is gone. Dropping
it has one risk: every request then resends the whole history, so billed input grows faster and could
reach a scout's token limit sooner.

`RESEARCH_TRIM_HISTORY=false` turns trimming off, and `studies/trim-history-rescout-task8.toml` compares
the two on three fixed plans, with its decision rule in its header. It should run before the DeepSeek
rescout study.

### The study's search and page cache

Arms of a study share a cache of searches and pages, so whichever arm runs first pays the search latency
and DuckDuckGo's failures, and the arms after it get the same results faster. The runner now rotates the
arm order across targets as well as replicates, so each arm goes first equally often (`study.schedule`).

A study could go further and warm the cache before any arm runs, by replaying the source runs' own
searches and fetches (as `scripts/search_replay.py` does for searches). All arms would then start from the
same results, which removes DuckDuckGo's empty responses as a source of noise. It would only help
the queries arms share. Rescouts of one plan often send similar first queries, and their later queries
diverge.

### Page prefetching during a run

When a search returns, the scout usually fetches one of its top results next: in the drb2-task8 runs,
the first result was fetched 44 times and the fifth or lower 29 times. Fetching the top two or three
results in the background while the model thinks would make those later fetches instant. It would
download pages that are never read, it must use only our own fetcher and never a paid reader, and it must
honor the source policy. Whether it saves time depends on how much of a scout's time is spent waiting for
fetches, which `research breakdown` can show before anything is built.

## A source ranker

On 28 September 2026 a free check of six stored drb2-task8 runs (study log, "What scouts see and skip")
asked whether scouts leave good sources unread among what their searches return.

What it found:
- Scouts fetched 1 of 545 scholarly-search works shown to them. Most were off the topic, highly cited
  works such as AlphaFold, fairness surveys, and EEG reviews.
- Scouts fetched 118 of 988 web results, mostly the top few, and the review-titled results they skipped
  were mostly off the topic. A handful of relevant reviews were shown and not read.
- 52 of 177 fetches failed, and 35 of those were at two publishers (MDPI and RSC), which a ranker would not
  fix and fetch version 17 partly did.
- The earlier audit places drb2-task8's lost points in how scouts turn sources into claims, not in which
  sources they read.

**A learned ranker, such as XGBoost learning to rank, is not justified yet.** Its only labels would be
which results scouts fetched and cited. Scouts mostly fetch the top results, so it would largely learn the
search engine's own order back, and nothing records whether an unread result would have helped. The
data is also small: a few thousand results, all judged by the same scout.

Cheaper steps that need no learning:
- **Filter scholarly results by relevance** to the query, for example by term overlap between the query
  and each work's title and abstract, so off-topic citation-heavy works do not take context.
- **Add CORE to the open-access reader.** CORE (core.ac.uk) aggregates repository full text. On
  28 September 2026 it held full text for 6 of the 7 MDPI articles that Europe PMC does not, and answered
  without an API key; a free key raises its rate limit.
- **Remember hosts that refuse our fetcher** (MDPI's bot challenge, RSC's 403s) and send them straight to
  the open-access reader, saving a wasted fetch.
- **Mark reviews and surveys** in search results, since the scout prompt already asks scouts to read
  surveys first.

When a ranker would make sense: if, after the claim fix (scout-v13) is measured, `research diagnose` puts
many missed points at "not found", and the unread search results for those runs contain the sources that
would have met them. That can be checked for free by matching unread results against each missed point's
rubric text.

## Provider usage policies

Every external API this project calls has a usage policy: rate limits, daily quotas, terms about
automated access, and sometimes a request to identify the caller. A specification should record, for
each API, its current policy with the source and the date it was read, and how the code honors it. A new
integration should not be merged until its entry exists, and `research doctor` could report the entries
older than a few months. CORE shows why: without a key it allows 100 requests a day, and a few manual
probes on 28 September 2026 used up the afternoon's allowance.

What the code does today, as a starting inventory (policies themselves not yet re-checked):

| API | Used for | How the code limits its use |
|---|---|---|
| OpenAI, Anthropic, Z.ai, DeepSeek | Models | Timed 429s retried (`rate_limit.py`); Luna paced under the tokens-per-minute limit OpenAI reports; server errors sent again twice (scout-429-v5); hard dollar caps |
| DuckDuckGo | Default web search, by scraping its HTML | One request a second per process (`acquisition._RATE_INTERVAL`); its terms on automated access have not been reviewed |
| Serper, Brave, Exa | Paid web search | 0.1 s, 0.05 s, and 0.2 s between requests; paid per request |
| Exa contents, Firecrawl | Paid page reading | Reserved under the study budget before each request; Firecrawl's basic proxies only |
| OpenAlex | Scholarly search and records; open-access copies | 0.2 s between scholarly-search requests, and `OPENALEX_API_KEY` when set. The open-access reader's OpenAlex lookups are not paced |
| Crossref | Scholarly search | 0.2 s between requests; `CROSSREF_MAILTO` identifies us for its polite pool |
| arXiv | Scholarly search, PDFs | 3 s between API requests, as arXiv asks |
| Europe PMC | Open-access full text | Not paced |
| CORE | Repository full text | 6.5 s between requests; stops until CORE's reset time after a 429 or when none remain (fetch version 18); `CORE_API_KEY` raises the daily limit |
| Publisher and other web pages | Our own fetcher | Identifies itself (`FETCH_USER_AGENT`), retries a 403 once as a browser, and never tries to pass a bot challenge. It does not read `robots.txt` |

Known gaps to settle in the specification: the unpaced Europe PMC and OpenAlex calls in the open-access
reader; whether our fetcher should honor `robots.txt`; whether retrying a refused page as a browser is
acceptable under each site's terms; DuckDuckGo's terms for scraped search; and the pacing is per process,
so two studies at once can together exceed a limit that each respects alone.
