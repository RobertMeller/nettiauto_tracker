# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Setup (first time):**
```powershell
C:\Users\Robert\.local\bin\uv.exe venv
C:\Users\Robert\.local\bin\uv.exe pip install -r requirements.txt
```

**Run the scraper:**
```powershell
.venv\Scripts\python.exe scraper.py
.venv\Scripts\python.exe scraper.py --dry-run       # parse without writing to DB
.venv\Scripts\python.exe scraper.py --filter-only   # re-apply filters without scraping
```

**Run reports:**
```powershell
.venv\Scripts\python.exe report.py                  # all listings
.venv\Scripts\python.exe report.py --new            # today's listings only
.venv\Scripts\python.exe report.py --filtered       # listings matching searches.toml
.venv\Scripts\python.exe report.py --html           # generate report.html with Chart.js
.venv\Scripts\python.exe report.py --history <ID>   # price history for one listing
.venv\Scripts\python.exe report.py --export         # CSV export
```

There are no automated tests.

## Architecture

The project has two entry points and one config file:

- **`scraper.py`** — fetches listings from nettiauto.com, writes to SQLite
- **`report.py`** — reads from SQLite, prints tables or generates HTML
- **`searches.toml`** — all search criteria (loaded by both scripts at runtime via `load_search_configs()`)

### Scraper data flow

1. `load_search_configs()` reads `searches.toml` and maps human-readable fields (e.g. `max_mileage`) to nettiauto URL query params (e.g. `MittarilukemaMax`)
2. `scrape_search()` paginates through `nettiauto.com/{make}/{model}?page=N`, stopping when `div.pagination__next` is absent
3. `parse_search_result_card()` extracts data primarily from the `data-datalayer` JSON attribute on each `div.product-card`. Price, year, mileage, and fuel type come from this JSON; location comes from `div.product-card__location-info`; engine cc and body type are parsed from card text
4. `enrich_from_listing_page()` is only called when `price` or `year` is `None` on a card — it fetches the individual listing URL and parses free text
5. `upsert_listing()` inserts new rows into `listings` and writes to `price_history` whenever price or mileage changes
6. After all searches, `geocode_cities()` calls Nominatim for any city not yet in `city_coords`, then `apply_filters()` rebuilds `filtered_listings` from scratch (DROP + CREATE + INSERT)

### Key design decisions

**`curl_cffi` instead of `requests`**: Uses Chrome TLS fingerprint impersonation (`impersonate="chrome124"`) to bypass Cloudflare. Don't swap this for the standard `requests` library.

**`filtered_listings` is fully rebuilt on every run**: It is derived data — `apply_filters()` drops and recreates the table each time. Distance filtering happens in Python (not SQL) using cached lat/lon from `city_coords` and the haversine formula.

**`price_history` is append-only**: A new row is inserted on first seen and on every price/mileage change. The `listings` table stores only the current state; history is always queried from `price_history`.

**`searches.toml` drives SQL and distance filters differently**: Numeric params (`HintaMin`, `HintaMax`, etc.) are passed as URL query params to the nettiauto search API *and* re-applied as SQL WHERE conditions in `apply_filters()`. `exclude_fuel_type`, `min_year`, `origin_city`, and `max_distance_km` are post-processing filters applied only in `apply_filters()` — they are not sent to the nettiauto API.

### Cloudflare detection

`get_page()` checks for `"Just a moment"` in the response body combined with fewer than `MIN_LISTING_LINKS` (3) matching links. If triggered, it logs a warning and returns `None`, causing the search to stop gracefully.

### Database location

`nettiauto_listings.db` is created in the working directory from which the script is run (hardcoded as `DB_PATH = "nettiauto_listings.db"`). Always run from the project root.
