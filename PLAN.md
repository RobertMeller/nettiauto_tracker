# Nettiauto Tracker — Project Plan

## What This Is

A local Python tool that scrapes [nettiauto.com](https://www.nettiauto.com) daily for used Toyota Corolla and Avensis listings matching your filters (price, mileage, tow hitch, A/C, engine size), stores everything in a local SQLite database, and lets you view new listings, price drops, and full history from the command line.

**Current state of the code:** The skeleton is written and logically sound. Two scripts exist — `scraper.py` (fetches and stores listings) and `report.py` (queries and displays them). The database schema is designed. **Nothing has been run yet** — Python is not installed and the scraper's HTML selectors have not been tested against the live site.

---

## Phase 0 — Environment Setup (do this first)

The project uses `uv`, a fast Python package manager already installed at `C:\Users\Robert\.local\bin\uv.exe`.

### Steps

**0.1 — Create a virtual environment and install dependencies**

Open a terminal in the project folder and run:

```powershell
cd e:\Repositories\nettiauto_tracker
C:\Users\Robert\.local\bin\uv.exe venv
C:\Users\Robert\.local\bin\uv.exe pip install -r requirements.txt
```

This creates a `.venv` folder and installs `requests`, `beautifulsoup4`, and `lxml`.

**0.2 — Add a `.gitignore`**

Create a `.gitignore` file with at minimum:
```
.venv/
*.db
*.log
*.csv
__pycache__/
```

This keeps the database and logs out of version control (they are machine-local, not code).

**0.3 — Initialise git**

```powershell
git init
git add scraper.py report.py requirements.txt README.md PLAN.md .gitignore
git commit -m "Initial commit"
```

---

## Phase 1 — Verify the Scraper Against the Live Site

This is the most likely place where things will need fixing. The scraper was written without inspecting the actual HTML of nettiauto.com, so the CSS selectors used to find listing cards, prices, and locations may not match.

### Steps

**1.1 — Do a test fetch and inspect the raw HTML**

Add a temporary debug script (or just run this once interactively) to fetch one search page and print the HTML:

```python
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "fi-FI,fi;q=0.9",
}
resp = requests.get(
    "https://www.nettiauto.com/toyota/corolla",
    headers=HEADERS,
    params={"HintaMin": 5000, "HintaMax": 12000, "MittarilukemaMax": 200000}
)
print(resp.status_code)
with open("debug_page.html", "w", encoding="utf-8") as f:
    f.write(resp.text)
```

Open `debug_page.html` in a browser or editor and find:
- What CSS class or element wraps each car listing card?
- Where is the price? (class name, tag type)
- Where is the year, mileage, engine size?
- Where is the location/city?
- What does the pagination "next page" button look like?

**1.2 — Update selectors in `scraper.py`**

The key function is `parse_search_result_card()` (line 257 in `scraper.py`). Once you know the real class names from step 1.1, update:

- The `cards` selector in `scrape_search()` (line ~404) — the `re.compile(r"car-ad|listing-item|...")` pattern
- The `price_tag` selector in `parse_search_result_card()` (line ~274)
- The `loc_tag` selector (line ~306)

**1.3 — Run the scraper for the first time**

```powershell
.venv\Scripts\python.exe scraper.py
```

Watch the log output. Check that:
- Pages are being found (not "No more listings found on page 1")
- Listings are being parsed with real prices and years (not all `None`)
- The database file `nettiauto_listings.db` is created

**1.4 — Run the report to verify data**

```powershell
.venv\Scripts\python.exe report.py
```

If you see a table with real cars, prices, and years — Phase 1 is done.

---

## Phase 2 — Quality & Reliability

Once the scraper works, these improvements make it robust for daily use.

**2.1 — Add a `--dry-run` flag to `scraper.py`**

A dry run fetches and parses listings but does not write to the database. Useful for testing selector changes without polluting history.

**2.2 — Fix the SQL injection risk in `report.py`**

Line 116 in `report.py` uses an f-string to build a SQL query (`WHERE date_first_seen = '{today}'`). While not user-facing, it is bad practice. Replace with a parameterised query.

**2.3 — Handle the case where nettiauto blocks the scraper**

The site may return a CAPTCHA page or redirect. Add a check: if the parsed page has fewer than 3 links matching the listing URL pattern, log a warning and stop rather than silently recording zero results.

**2.4 — Test with `--new` and `--history`**

```powershell
.venv\Scripts\python.exe report.py --new
.venv\Scripts\python.exe report.py --history <some_listing_id>
.venv\Scripts\python.exe report.py --export
```

---

## Phase 3 — Automation (Daily Scraping)

**3.1 — Set up Windows Task Scheduler**

1. Open **Task Scheduler** → **Create Basic Task**
2. Name: `Nettiauto Scraper`
3. Trigger: **Daily** at 08:00
4. Action: **Start a program**
   - Program: `C:\Users\Robert\.local\bin\uv.exe` (or the full path to `.venv\Scripts\python.exe`)
   - Arguments: `run python scraper.py` (if using uv run) OR just `scraper.py`
   - Start in: `e:\Repositories\nettiauto_tracker`
5. Check "Open the Properties dialog" → check **"Run whether user is logged on or not"**

**3.2 — Verify automation**

After one scheduled run, check `scraper.log` for the run output and `report.py` for updated `date_last_seen` values.

---

## Phase 4 — Notifications (Optional but useful)

Right now you have to manually run `report.py --new` to see new listings. These make it more passive.

**Option A — Desktop toast notification (simplest)**

Add to the end of `scraper.py`'s `run()` function:

```python
if total_new > 0:
    # Windows toast via win10toast or plyer
    from plyer import notification
    notification.notify(
        title="Nettiauto: new listings",
        message=f"{total_new} new cars found",
        timeout=10,
    )
```

Add `plyer` to `requirements.txt`.

**Option B — Email digest**

Send yourself an email with new listings when `total_new > 0`. Requires an SMTP server or a free service like Mailgun/SendGrid. More setup but works even when you're away from the machine.

---

## Current File Map

| File | Purpose |
|------|---------|
| `scraper.py` | Fetches listings from nettiauto.com, stores in SQLite |
| `report.py` | CLI to query and display the database |
| `requirements.txt` | Python dependencies |
| `README.md` | Usage documentation |
| `PLAN.md` | This file |
| `Specs.xlsx` | (unknown — likely original search spec notes) |

---

## Immediate Next Steps (in order)

1. Run the `uv venv` and `uv pip install` commands from Phase 0.1
2. Create the `.gitignore` and do the initial git commit (Phase 0.2–0.3)
3. Fetch a debug HTML page and inspect the selectors (Phase 1.1)
4. Update `scraper.py` selectors if needed (Phase 1.2)
5. Run `scraper.py` for the first time and verify (Phase 1.3–1.4)
