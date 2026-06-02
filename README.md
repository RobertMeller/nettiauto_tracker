# Nettiauto.com Car Tracker

Scrapes nettiauto.com for car listings matching your criteria and stores a full
price/availability history in a local SQLite database.

**Live report:** https://robertmeller.github.io/nettiauto_tracker/

---

## Search Criteria (pre-configured)

| Filter              | Value           |
|---------------------|-----------------|
| Make                | Toyota          |
| Models              | Corolla, Avensis |
| Max mileage         | 200 000 km      |
| Price range         | 5 000–12 000 €  |
| Tow hitch (Vetokoukku) | Kyllä (Yes)  |
| Air conditioning    | Kyllä (Yes)     |
| Max engine size     | < 1 800 cc      |

---

## Setup

### 1. Install Python 3.10+
Download from https://python.org if needed.

### 2. Install dependencies
```bash
cd nettiauto_tracker
pip install -r requirements.txt
```

---

## Usage

### Run the scraper (fetch new data)
```bash
python scraper.py
```
First run populates the database.  
Subsequent runs update `date_last_seen` and record any price changes.

### View all tracked listings
```bash
python report.py
```

### View only new listings seen today
```bash
python report.py --new
```

### Export everything to CSV
```bash
python report.py --export
```

### View price history for a specific listing
```bash
python report.py --history 15521636
```
(replace `15521636` with the actual listing ID)

### Sort by a different column
```bash
python report.py --sort year
python report.py --sort mileage
python report.py --sort date_first_seen
```

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
Create `.github/workflows/scrape.yml`:
```yaml
name: Nettiauto Scraper
on:
  schedule:
    - cron: '0 7 * * *'   # 07:00 UTC daily
  workflow_dispatch:        # also allow manual trigger

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - run: python scraper.py
      - uses: actions/upload-artifact@v4
        with:
          name: database
          path: nettiauto_listings.db
```
Note: GitHub Actions doesn't persist the SQLite file between runs unless you
commit it back or use a cloud database. For persistent history, run locally
or on a VPS.

---

## Database structure

**listings** — one row per car, updated in place
| Column          | Type    | Description                        |
|-----------------|---------|------------------------------------|
| listing_id      | INTEGER | Unique ID from the URL             |
| make            | TEXT    | toyota                             |
| model           | TEXT    | corolla / avensis                  |
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

Edit `SEARCH_CONFIGS` in `scraper.py`:

```python
SEARCH_CONFIGS = [
    {
        "make": "toyota",
        "model": "corolla",
        "params": {
            "MittarilukemaMax": 200000,   # max mileage km
            "HintaMin": 5000,             # min price EUR
            "HintaMax": 12000,            # max price EUR
            "Vetokoukku": 1,              # tow hitch: 1=yes
            "Ilmastointi": 1,             # A/C: 1=yes
            "MoottoritilavuusMax": 1800,  # max engine cc
        }
    },
    # add more makes/models here...
]
```

---

## Notes

- The scraper adds a 2–4 second random delay between requests to be polite to the server.
- nettiauto.com may change its HTML structure over time; if parsing breaks, open an
  issue or inspect the page source and update the CSS selectors in `scraper.py`.
- Check `nettiauto.com/robots.txt` to confirm scraping is permitted for your use case.
