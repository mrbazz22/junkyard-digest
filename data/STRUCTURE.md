# research_archive.json Structure

The archive is a flat dict keyed by `{year}|{make}|{model}|{yard_row}|{arrival_date}`.

Each value is a dict with keys:
- `vehicle` — nested dict with `year`, `make`, `model`, `yard_row`, `arrival_date`, `yard`
- `parts` — dict of part names → `{active: {avg, median, min, max, count}, yard_cost, est_margin}`
- `best_margin`, `total_margin`, `parts_with_data`
- `is_stale` — bool, set when vehicle absent from current scrape
- `last_seen_date` — ISO date when vehicle was last seen

**Gotcha:** `arrival_date` is at `entry['vehicle']['arrival_date']`, NOT `entry['arrival_date']`.

**Eviction:** Vehicles with `is_stale=true` and `days_stale > 28` are silently removed on pipeline run.

**Created:** 2026-07-02 during v4 pipeline upgrade
