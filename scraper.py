"""
Nettiauto.com Car Listing Scraper & Price History Tracker
Tracks Toyota Corolla & Avensis listings matching specific search criteria.
"""

import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re
import logging
from datetime import date, datetime
from dataclasses import dataclass, field
from typing import Optional
import random

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

SEARCH_CONFIGS = [
    {
        "make": "toyota",
        "model": "corolla",
        "params": {
            "MittarilukemaMax": 200000,
            "HintaMin": 5000,
            "HintaMax": 12000,
            "Vetokoukku": 1,          # 1 = Kyllä (Yes)
            "Ilmastointi": 1,          # 1 = Kyllä (Yes)
            "MoottoritilavuusMax": 1800,  # cc  (<1.8 L)
        }
    },
    {
        "make": "toyota",
        "model": "avensis",
        "params": {
            "MittarilukemaMax": 200000,
            "HintaMin": 5000,
            "HintaMax": 12000,
            "Vetokoukku": 1,
            "Ilmastointi": 1,
            "MoottoritilavuusMax": 1800,
        }
    },
]

BASE_URL = "https://www.nettiauto.com"
DB_PATH = "nettiauto_listings.db"

# Polite delay between requests (seconds)
REQUEST_DELAY_MIN = 2.0
REQUEST_DELAY_MAX = 4.0

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────

@dataclass
class Listing:
    listing_id: int
    make: str
    model: str
    year: Optional[int]
    mileage: Optional[int]       # km
    price: Optional[int]         # EUR
    engine_cc: Optional[int]     # cm³
    location: Optional[str]
    url: str
    date_first_seen: str
    date_last_seen: str
    # extra detail scraped from the listing page
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    body_type: Optional[str] = None

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            listing_id      INTEGER PRIMARY KEY,
            make            TEXT,
            model           TEXT,
            year            INTEGER,
            mileage         INTEGER,
            price           INTEGER,
            engine_cc       INTEGER,
            fuel_type       TEXT,
            transmission    TEXT,
            body_type       TEXT,
            location        TEXT,
            url             TEXT,
            date_first_seen TEXT,
            date_last_seen  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id      INTEGER,
            price           INTEGER,
            mileage         INTEGER,
            recorded_date   TEXT,
            FOREIGN KEY (listing_id) REFERENCES listings(listing_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date        TEXT,
            listings_found  INTEGER,
            listings_new    INTEGER,
            listings_updated INTEGER
        )
    """)
    conn.commit()
    return conn


def upsert_listing(conn: sqlite3.Connection, listing: Listing) -> dict:
    """Insert new listing or update last_seen + price. Returns stats dict."""
    today = date.today().isoformat()
    stats = {"new": False, "price_changed": False, "mileage_changed": False}

    existing = conn.execute(
        "SELECT * FROM listings WHERE listing_id = ?", (listing.listing_id,)
    ).fetchone()

    if existing is None:
        conn.execute("""
            INSERT INTO listings
              (listing_id, make, model, year, mileage, price, engine_cc,
               fuel_type, transmission, body_type, location, url,
               date_first_seen, date_last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            listing.listing_id, listing.make, listing.model,
            listing.year, listing.mileage, listing.price, listing.engine_cc,
            listing.fuel_type, listing.transmission, listing.body_type,
            listing.location, listing.url,
            today, today,
        ))
        # Record initial price
        conn.execute("""
            INSERT INTO price_history (listing_id, price, mileage, recorded_date)
            VALUES (?,?,?,?)
        """, (listing.listing_id, listing.price, listing.mileage, today))
        stats["new"] = True
        log.info(f"  ✚ NEW  [{listing.listing_id}] {listing.year} {listing.make.title()} "
                 f"{listing.model.title()} — {listing.price}€  {listing.mileage}km  {listing.location}")
    else:
        changed = False
        if existing["price"] != listing.price:
            log.info(f"  💶 PRICE CHANGE [{listing.listing_id}] "
                     f"{existing['price']}€ → {listing.price}€")
            stats["price_changed"] = True
            changed = True
        if existing["mileage"] != listing.mileage and listing.mileage:
            stats["mileage_changed"] = True
            changed = True

        conn.execute("""
            UPDATE listings
            SET date_last_seen=?, price=?, mileage=?, location=?,
                engine_cc=COALESCE(?, engine_cc),
                fuel_type=COALESCE(?, fuel_type),
                transmission=COALESCE(?, transmission),
                body_type=COALESCE(?, body_type)
            WHERE listing_id=?
        """, (
            today, listing.price, listing.mileage, listing.location,
            listing.engine_cc, listing.fuel_type,
            listing.transmission, listing.body_type,
            listing.listing_id,
        ))

        if changed:
            conn.execute("""
                INSERT INTO price_history (listing_id, price, mileage, recorded_date)
                VALUES (?,?,?,?)
            """, (listing.listing_id, listing.price, listing.mileage, today))

    conn.commit()
    return stats


# ─────────────────────────────────────────────
# HTTP HELPERS
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fi-FI,fi;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def get_page(url: str, params: dict = None, retries: int = 3) -> Optional[BeautifulSoup]:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as e:
            wait = 5 * attempt
            log.warning(f"  Request failed (attempt {attempt}/{retries}): {e}. Retrying in {wait}s…")
            time.sleep(wait)
    log.error(f"  Giving up on URL: {url}")
    return None


def polite_sleep():
    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


# ─────────────────────────────────────────────
# PARSING HELPERS
# ─────────────────────────────────────────────

def parse_int(text: str) -> Optional[int]:
    """Extract first integer from a text string, stripping whitespace/units."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def extract_listing_id(url: str) -> Optional[int]:
    """Extract numeric listing ID from the URL path, e.g. /toyota/corolla/15521636"""
    m = re.search(r"/(\d{6,10})(?:[/?#]|$)", url)
    return int(m.group(1)) if m else None


def parse_search_result_card(card, make: str, model: str) -> Optional[Listing]:
    """Parse one listing card from the search results page."""
    try:
        # --- URL & ID ---
        link_tag = card.find("a", href=re.compile(r"/\d{6,10}"))
        if not link_tag:
            return None
        href = link_tag.get("href", "")
        if not href.startswith("http"):
            href = BASE_URL + href
        listing_id = extract_listing_id(href)
        if not listing_id:
            return None

        # --- Price ---
        price = None
        price_tag = (
            card.find("span", class_=re.compile(r"price", re.I))
            or card.find("div", class_=re.compile(r"price", re.I))
        )
        if price_tag:
            price = parse_int(price_tag.get_text())

        # --- Year, mileage, engine from the "spec" line ---
        year = mileage = engine_cc = None
        text_content = card.get_text(" ", strip=True)

        # Year: 4-digit number between 1990-2030
        year_m = re.search(r"\b(19[9]\d|20[0-3]\d)\b", text_content)
        if year_m:
            year = int(year_m.group(1))

        # Mileage: number followed by km
        km_m = re.search(r"(\d[\d\s]*)\s*km", text_content, re.I)
        if km_m:
            mileage = parse_int(km_m.group(1))

        # Engine: number followed by cc or cm or just a decimal like 1.6
        cc_m = re.search(r"(\d[\d\s]*)\s*(?:cm³|cc)", text_content, re.I)
        if cc_m:
            engine_cc = parse_int(cc_m.group(1))
        else:
            # Try decimal engine size like "1.6" or "1,6"
            eng_m = re.search(r"\b(\d)[.,](\d)\b", text_content)
            if eng_m:
                engine_cc = int(eng_m.group(1)) * 1000 + int(eng_m.group(2)) * 100

        # --- Location ---
        location = None
        loc_tag = card.find(class_=re.compile(r"location|city|seller", re.I))
        if loc_tag:
            location = loc_tag.get_text(strip=True)
        else:
            # Fallback: look for Finnish city pattern
            loc_m = re.search(r"([A-ZÄÖÅ][a-zäöå]+(?:\s[A-ZÄÖÅ][a-zäöå]+)*),\s*([A-Za-zÄÖÅäöå\s\-]+)", text_content)
            if loc_m:
                location = loc_m.group(0)

        today = date.today().isoformat()
        return Listing(
            listing_id=listing_id,
            make=make,
            model=model,
            year=year,
            mileage=mileage,
            price=price,
            engine_cc=engine_cc,
            location=location,
            url=href,
            date_first_seen=today,
            date_last_seen=today,
        )
    except Exception as e:
        log.debug(f"  Card parse error: {e}")
        return None


def enrich_from_listing_page(listing: Listing) -> Listing:
    """Fetch the individual listing page to fill in extra details."""
    soup = get_page(listing.url)
    if not soup:
        return listing
    polite_sleep()

    text = soup.get_text(" ", strip=True)

    # Engine CC (more reliable on detail page)
    if not listing.engine_cc:
        m = re.search(r"(\d{3,4})\s*(?:cm³|cc)", text, re.I)
        if m:
            listing.engine_cc = int(m.group(1))

    # Fuel type
    for fuel in ["Bensiini", "Diesel", "Hybridi", "Sähkö", "Kaasu",
                 "Petrol", "Hybrid", "Electric"]:
        if fuel.lower() in text.lower():
            listing.fuel_type = fuel
            break

    # Transmission
    for trans in ["Manuaali", "Automaatti", "Manual", "Automatic"]:
        if trans.lower() in text.lower():
            listing.transmission = trans
            break

    # Body type
    for body in ["Farmari", "Sedan", "Hatchback", "Viistoperä", "Coupe",
                 "Tila-auto", "Wagon", "Station wagon"]:
        if body.lower() in text.lower():
            listing.body_type = body
            break

    # Location (more reliable on detail page)
    if not listing.location:
        loc_tag = soup.find(class_=re.compile(r"location|city|seller-city", re.I))
        if loc_tag:
            listing.location = loc_tag.get_text(strip=True)

    # Price (fallback)
    if not listing.price:
        m = re.search(r"([\d\s]{4,6})\s*€", text)
        if m:
            listing.price = parse_int(m.group(1))

    return listing


# ─────────────────────────────────────────────
# SEARCH PAGE SCRAPER
# ─────────────────────────────────────────────

def scrape_search(make: str, model: str, params: dict) -> list[Listing]:
    """Scrape all pages of search results for a given make/model + filters."""
    listings = []
    page = 1

    while True:
        url = f"{BASE_URL}/{make}/{model}"
        page_params = {**params, "page": page}
        log.info(f"Fetching page {page}: {url} {page_params}")
        soup = get_page(url, params=page_params)
        if not soup:
            break
        polite_sleep()

        # Find listing cards — nettiauto uses various class names; try common ones
        cards = (
            soup.find_all("div", class_=re.compile(r"car-ad|listing-item|ad-item|result-item", re.I))
            or soup.find_all("article")
            or soup.find_all("li", class_=re.compile(r"car|auto|ad", re.I))
        )

        if not cards:
            # Fallback: find all links that look like listing URLs
            links = soup.find_all("a", href=re.compile(rf"/{make}/{model}/\d{{6,10}}"))
            seen_ids = set()
            for link in links:
                listing_id = extract_listing_id(link["href"])
                if listing_id and listing_id not in seen_ids:
                    seen_ids.add(listing_id)
                    href = link["href"]
                    if not href.startswith("http"):
                        href = BASE_URL + href
                    today = date.today().isoformat()
                    listings.append(Listing(
                        listing_id=listing_id, make=make, model=model,
                        year=None, mileage=None, price=None, engine_cc=None,
                        location=None, url=href,
                        date_first_seen=today, date_last_seen=today,
                    ))
            if not seen_ids:
                log.info(f"  No more listings found on page {page}.")
                break
        else:
            new_on_page = 0
            for card in cards:
                listing = parse_search_result_card(card, make, model)
                if listing:
                    listings.append(listing)
                    new_on_page += 1
            log.info(f"  Found {new_on_page} listings on page {page}.")
            if new_on_page == 0:
                break

        # Check for next page
        next_btn = soup.find("a", string=re.compile(r"Seuraava|Next|›|»", re.I))
        if not next_btn:
            break
        page += 1

    return listings


# ─────────────────────────────────────────────
# MAIN RUN
# ─────────────────────────────────────────────

def run():
    conn = init_db(DB_PATH)
    run_date = datetime.now().isoformat(timespec="seconds")
    total_found = total_new = total_updated = 0

    for config in SEARCH_CONFIGS:
        make = config["make"]
        model = config["model"]
        params = config["params"]

        log.info(f"\n{'='*60}")
        log.info(f"Searching: {make.title()} {model.title()}")
        log.info(f"Filters: {params}")
        log.info(f"{'='*60}")

        raw_listings = scrape_search(make, model, params)
        log.info(f"  Total raw listings: {len(raw_listings)}")
        total_found += len(raw_listings)

        for i, listing in enumerate(raw_listings, 1):
            # If we're missing key data, fetch the detail page
            if listing.price is None or listing.year is None:
                log.debug(f"  [{i}/{len(raw_listings)}] Enriching {listing.listing_id}…")
                listing = enrich_from_listing_page(listing)
                polite_sleep()

            stats = upsert_listing(conn, listing)
            if stats["new"]:
                total_new += 1
            elif stats["price_changed"] or stats["mileage_changed"]:
                total_updated += 1

    # Record run summary
    conn.execute("""
        INSERT INTO scrape_runs (run_date, listings_found, listings_new, listings_updated)
        VALUES (?,?,?,?)
    """, (run_date, total_found, total_new, total_updated))
    conn.commit()

    log.info(f"\n{'='*60}")
    log.info(f"Run complete: {total_found} found, {total_new} new, {total_updated} updated")
    log.info(f"{'='*60}\n")
    conn.close()


if __name__ == "__main__":
    run()
