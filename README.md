# Nettiauto.com Car Tracker

Scrapes nettiauto.com for car listings matching your criteria and stores a full
price/availability history in a local SQLite database.

**Live report:** https://robertmeller.github.io/nettiauto_tracker/

---

## Search Criteria (pre-configured)

| Filter              | Value                                                        |
|---------------------|--------------------------------------------------------------|
| Makes / Models      | Toyota Corolla, Toyota Avensis, Kia Ceed, VW Passat, Skoda Octavia |
| Max mileage         | 200 000 km                                                   |
| Price range         | 5 000–15 000 €                                               |
| Tow hitch (Vetokoukku) | Kyllä (Yes)                                               |
| Air conditioning    | Kyllä (Yes)                                                  |
| Max engine size     | < 1 800 cc                                                   |
| Fuel type           | No diesel                                                    |
| Min year            | 2005                                                         |
| Origin city         | Turku                                                        |
| Max distance        | 200 km                                                       |

---

## Setup

### 1. Install Python 3.10+ and uv
Download Python from https://python.org if needed. Install [uv](https://github.com/astral-sh/uv) as the package manager.

### 2. Install dependencies
```powershell
uv venv
uv pip install -r requirements.txt
```

---

## Usage

### Run the scraper (fetch new data)
```powershell
uv run python scraper.py
```
First run populates the database.  
Subsequent runs update `date_last_seen` and record any price changes.

### View all tracked listings
```powershell
uv run python report.py
```

### View only new listings seen today
```powershell
uv run python report.py --new
```

### View listings matching searches.toml filters
```powershell
uv run python report.py --filtered
```

### Generate the HTML report
```powershell
uv run python report.py --html
```

### Export everything to CSV
```powershell
uv run python report.py --export
```

### View price history for a specific listing
```powershell
uv run python report.py --history 15521636
```
(replace `15521636` with the actual listing ID)

---

## Scheduling (run automatically every day)

### Linux / macOS — cron
Open crontab:
```bash
crontab -e
```
Add this line to run at 08:00 every morning:
```
0 8 * * * cd /full/path/to/nettiauto_tracker && python scraper.py >> scraper.log 2>&1
```

### Windows — Task Scheduler
1. Open **Task Scheduler** → Create Basic Task
2. Trigger: **Daily** at your chosen time
3. Action: **Start a program**
   - Program: `python`
   - Arguments: `scraper.py`
   - Start in: `C:\path\to\nettiauto_tracker`

### GitHub Actions (free cloud schedule, no computer needed)
Already configured in `.github/workflows/scrape.yml`. Runs daily at 06:00 UTC,
scrapes listings, generates `docs/index.html`, and commits the updated database
and report back to the repo. The live report is published via GitHub Pages at
https://robertmeller.github.io/nettiauto_tracker/.

---

## Database structure

**listings** — one row per car, updated in place
| Column          | Type    | Description                        |
|-----------------|---------|------------------------------------|
| listing_id      | INTEGER | Unique ID from the URL             |
| make            | TEXT    | toyota / kia / volkswagen / skoda  |
| model           | TEXT    | corolla / avensis / ceed / passat / octavia |
| year            | INTEGER | Registration year                  |
| mileage         | INTEGER | km                                 |
| price           | INTEGER | EUR                                |
| engine_cc       | INTEGER | Engine displacement in cc          |
| fuel_type       | TEXT    | Bensiini / Diesel / Hybridi        |
| transmission    | TEXT    | Manuaali / Automaatti              |
| body_type       | TEXT    | Farmari / Sedan / etc.             |
| location        | TEXT    | City / dealer name                 |
| url             | TEXT    | Full listing URL                   |
| date_first_seen | TEXT    | ISO date first scraped             |
| date_last_seen  | TEXT    | ISO date last confirmed active     |

**price_history** — one row per change
| Column        | Description                                 |
|---------------|---------------------------------------------|
| listing_id    | References listings.listing_id              |
| price         | Price on that date                          |
| mileage       | Mileage on that date                        |
| recorded_date | ISO date                                    |

**scrape_runs** — audit log of each run

---

## Adjusting search criteria

Edit `searches.toml` — add a new `[[searches]]` block for each make/model you want to track:

```toml
[[searches]]
make = "toyota"
model = "corolla"
min_price = 5000        # EUR
max_price = 15000       # EUR
max_mileage = 200000    # km
tow_hitch = true
ac = true
max_engine_cc = 1800
exclude_fuel_type = ["Diesel"]
min_year = 2005
origin_city = "Turku"
max_distance_km = 200
```

---

## Notes

- The scraper adds a 2–4 second random delay between requests to be polite to the server.
- nettiauto.com may change its HTML structure over time; if parsing breaks, open an
  issue or inspect the page source and update the CSS selectors in `scraper.py`.
- Check `nettiauto.com/robots.txt` to confirm scraping is permitted for your use case.
