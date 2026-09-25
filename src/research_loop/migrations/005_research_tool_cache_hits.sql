-- Whether each search or fetch was served from the acquisition cache instead of the live web.
-- A replay's share of calls served from its recording says how far its comparison with the
-- recorded run is a comparison of models rather than of the web on two days. Null for tools
-- without a cache, and for scholarly calls, whose results carry their own cache_hits count.
alter table research_tool_events
    add column if not exists cache_hit boolean;
