"""
Nettiauto.com Car Listing Scraper & Price History Tracker
Tracks Toyota Corolla & Avensis listings matching specific search criteria.
"""

from curl_cffi import requests  # impersonates Chrome TLS to bypass Cloudflare
from bs4 import BeautifulSoup
import sqlite3
import time
import re
import json
import math
import tomllib
import logging
from datetime import date, datetime
from dataclasses import dataclass, field
from typing import Optional
import random
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

SEARCHES_FILE = Path(__file__).parent / "searches.toml"
BASE_URL = "https://www.nettiauto.com"
DB_PATH = "nettiauto_listings.db"


def load_search_configs() -> list[dict]:
    """Load search criteria from searches.toml and convert to nettiauto API params."""
    with open(SEARCHES_FILE, "rb") as f:
        data = tomllib.load(f)

    configs = []
    for s in data.get("searches", []):
        params = {}
        if "min_price" in s:
            params["HintaMin"] = s["min_price"]
        if "max_price" in s:
            params["HintaMax"] = s["max_price"]
        if "max_mileage" in s:
            params["MittarilukemaMax"] = s["max_mileage"]
        if s.get("tow_hitch"):
            params["Vetokoukku"] = 1
        if s.get("ac"):
            params["Ilmastointi"] = 1
        if "max_engine_cc" in s:
            params["MoottoritilavuusMax"] = s["max_engine_cc"]
        configs.append({"make": s["make"], "model": s["model"], "params": params, "_raw": s})
    return configs

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
        logging.StreamHandler(open(__import__("sys").stdout.fileno(), mode="w", encoding="utf-8", closefd=False)),
        logging.FileHandler("scraper.log", encoding="utf-8"),
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filtered_listings (
            listing_id      INTEGER,
            search_label    TEXT,
            matched_date    TEXT,
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
            date_last_seen  TEXT,
            PRIMARY KEY (listing_id, search_label)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS city_coords (
            city_name   TEXT PRIMARY KEY,
            lat         REAL,
            lon         REAL
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

MIN_LISTING_LINKS = 3  # fewer than this on a page = likely blocked

def get_page(url: str, params: dict = None, retries: int = 3) -> Optional[BeautifulSoup]:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                url, headers=HEADERS, params=params, timeout=20,
                impersonate="chrome124",
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            # Guard against Cloudflare / CAPTCHA pages with no real content
            listing_links = soup.find_all("a", href=re.compile(r"/\d{6,10}"))
            if len(listing_links) < MIN_LISTING_LINKS and "Just a moment" in resp.text:
                log.warning(f"  Cloudflare challenge detected on {url}. Stopping.")
                return None
            return soup
        except Exception as e:
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
        # Primary data source: structured JSON in data-datalayer attribute
        dl_raw = card.get("data-datalayer")
        if not dl_raw:
            return None
        data = json.loads(dl_raw)

        listing_id = data.get("item_id")
        if not listing_id:
            return None

        price   = data.get("item_vehicle_price")
        year    = data.get("item_year_model")
        mileage = data.get("item_mileage")
        fuel_type = data.get("item_power_type")  # e.g. "Bensiini", "Diesel", "Hybridi (bensiini/sähkö)"

        # --- URL ---
        link_tag = card.find("a", href=re.compile(rf"/{make}/{model}/\d{{6,10}}", re.I))
        if not link_tag:
            link_tag = card.find("a", href=re.compile(r"/\d{6,10}"))
        if not link_tag:
            return None
        href = link_tag.get("href", "")
        if not href.startswith("http"):
            href = BASE_URL + href

        # --- Location ---
        # The city text is the first non-empty string inside the location div
        location = None
        loc_tag = card.find(class_=re.compile(r"product-card__location-info", re.I))
        if loc_tag:
            raw = next((s for s in loc_tag.strings if s.strip()), "")
            location = raw.strip().split(",")[0].strip()

        # --- Engine CC from card text (not in datalayer) ---
        engine_cc = None
        text_content = card.get_text(" ", strip=True)
        cc_m = re.search(r"(\d[\d\s]*)\s*(?:cm³|cc)", text_content, re.I)
        if cc_m:
            engine_cc = parse_int(cc_m.group(1))
        else:
            eng_m = re.search(r"\b(\d)[.,](\d)\b", text_content)
            if eng_m:
                engine_cc = int(eng_m.group(1)) * 1000 + int(eng_m.group(2)) * 100

        # --- Transmission from card text ---
        transmission = None
        for trans in ["Automaatti", "Manuaali", "Automatic", "Manual"]:
            if trans.lower() in text_content.lower():
                transmission = trans
                break

        # --- Body type from card text ---
        body_type = None
        for body in ["Farmari", "Sedan", "Hatchback", "Viistoperä", "Coupe",
                     "Tila-auto", "Wagon", "Station wagon"]:
            if body.lower() in text_content.lower():
                body_type = body
                break

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
            fuel_type=fuel_type,
            transmission=transmission,
            body_type=body_type,
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

        # nettiauto listing cards are div.product-card with a data-datalayer JSON attribute
        cards = soup.find_all("div", class_="product-card")

        if not cards:
            log.info(f"  No listing cards found on page {page}. Stopping.")
            break

        new_on_page = 0
        for card in cards:
            listing = parse_search_result_card(card, make, model)
            if listing:
                listings.append(listing)
                new_on_page += 1
        log.info(f"  Found {new_on_page} listings on page {page}.")
        if new_on_page == 0:
            break

        # Check for next page — nettiauto uses class "pagination__next"
        next_btn = soup.find("a", class_=re.compile(r"pagination__next", re.I))
        if not next_btn:
            break
        page += 1

    return listings


# ─────────────────────────────────────────────
# GEOCODING
# ─────────────────────────────────────────────

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "nettiauto_tracker/1.0 (personal project)"}
NOMINATIM_DELAY = 1.1  # seconds — Nominatim policy: max 1 request/sec


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def geocode_city(city_name: str) -> Optional[tuple[float, float]]:
    """Return (lat, lon) for a Finnish city via Nominatim, or None if not found."""
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": f"{city_name}, Finland", "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        log.warning(f"  Geocoding failed for '{city_name}': {e}")
    return None


def geocode_cities(conn: sqlite3.Connection):
    """Geocode all unique cities in listings not yet in the city_coords cache."""
    all_cities = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT location FROM listings WHERE location IS NOT NULL AND location != ''"
        ).fetchall()
    }
    cached = {
        row[0] for row in conn.execute("SELECT city_name FROM city_coords").fetchall()
    }
    to_geocode = sorted(all_cities - cached)

    if not to_geocode:
        log.info("  City geocode cache is up to date.")
        return

    log.info(f"  Geocoding {len(to_geocode)} new cities (this may take a few minutes)…")
    for city in to_geocode:
        coords = geocode_city(city)
        if coords:
            lat, lon = coords
            conn.execute(
                "INSERT OR REPLACE INTO city_coords (city_name, lat, lon) VALUES (?,?,?)",
                (city, lat, lon),
            )
            log.info(f"    {city}: {lat:.4f}, {lon:.4f}")
        else:
            conn.execute(
                "INSERT OR REPLACE INTO city_coords (city_name, lat, lon) VALUES (?,NULL,NULL)",
                (city,),
            )
            log.warning(f"    {city}: not found")
        conn.commit()
        time.sleep(NOMINATIM_DELAY)


# ─────────────────────────────────────────────
# FILTERING
# ─────────────────────────────────────────────

def apply_filters(conn: sqlite3.Connection):
    """Rebuild filtered_listings from listings using criteria in searches.toml."""
    today = date.today().isoformat()
    configs = load_search_configs()
    total_matched = 0

    # filtered_listings is always derived data — drop and recreate for a clean rebuild
    conn.execute("DROP TABLE IF EXISTS filtered_listings")
    conn.execute("""
        CREATE TABLE filtered_listings (
            listing_id      INTEGER,
            search_label    TEXT,
            matched_date    TEXT,
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
            date_last_seen  TEXT,
            PRIMARY KEY (listing_id, search_label)
        )
    """)

    for config in configs:
        s = config["_raw"]
        label = s.get("label") or f"{s['make'].title()} {s['model'].title()}"
        params = config["params"]

        conditions = ["make = ?", "model = ?"]
        values: list = [s["make"], s["model"]]

        if "HintaMin" in params:
            conditions.append("price >= ?")
            values.append(params["HintaMin"])
        if "HintaMax" in params:
            conditions.append("price <= ?")
            values.append(params["HintaMax"])
        if "MittarilukemaMax" in params:
            conditions.append("(mileage IS NULL OR mileage <= ?)")
            values.append(params["MittarilukemaMax"])
        if "MoottoritilavuusMax" in params:
            conditions.append("(engine_cc IS NULL OR engine_cc < ?)")
            values.append(params["MoottoritilavuusMax"])
        if "min_year" in s:
            conditions.append("(year IS NULL OR year >= ?)")
            values.append(s["min_year"])

        exclude_fuels = s.get("exclude_fuel_type", [])
        if exclude_fuels:
            placeholders = ",".join("?" * len(exclude_fuels))
            conditions.append(f"(fuel_type IS NULL OR fuel_type NOT IN ({placeholders}))")
            values.extend(exclude_fuels)

        where = " AND ".join(conditions)
        matches = list(conn.execute(
            f"SELECT * FROM listings WHERE {where}", values
        ).fetchall())

        # Distance filter
        origin_city = s.get("origin_city")
        max_dist_km = s.get("max_distance_km")
        if origin_city and max_dist_km:
            origin_row = conn.execute(
                "SELECT lat, lon FROM city_coords WHERE city_name = ?", (origin_city,)
            ).fetchone()
            if not origin_row or origin_row["lat"] is None:
                log.warning(f"  '{origin_city}' not in geocode cache — run --filter-only to geocode first. Skipping distance filter.")
            else:
                olat, olon = origin_row["lat"], origin_row["lon"]
                # Load coords for all cities at once
                city_cache = {
                    row["city_name"]: (row["lat"], row["lon"])
                    for row in conn.execute("SELECT city_name, lat, lon FROM city_coords").fetchall()
                    if row["lat"] is not None
                }
                before = len(matches)
                matches = [
                    row for row in matches
                    if row["location"] in city_cache
                    and haversine(olat, olon, *city_cache[row["location"]]) <= max_dist_km
                ]
                log.info(f"  Distance filter ({origin_city} ≤{max_dist_km}km): {before} → {len(matches)} listings")

        conn.executemany("""
            INSERT INTO filtered_listings
              (listing_id, search_label, matched_date,
               make, model, year, mileage, price, engine_cc,
               fuel_type, transmission, body_type, location, url,
               date_first_seen, date_last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            (row["listing_id"], label, today,
             row["make"], row["model"], row["year"], row["mileage"], row["price"],
             row["engine_cc"], row["fuel_type"], row["transmission"], row["body_type"],
             row["location"], row["url"], row["date_first_seen"], row["date_last_seen"])
            for row in matches
        ])
        conn.commit()
        log.info(f"  Filter '{label}': {len(matches)} matches")
        total_matched += len(matches)

    log.info(f"  Total filtered matches: {total_matched}")


# ─────────────────────────────────────────────
# MAIN RUN
# ─────────────────────────────────────────────

def run(dry_run: bool = False):
    if dry_run:
        log.info("DRY RUN — listings will be parsed but NOT written to the database.")
    conn = init_db(DB_PATH)
    run_date = datetime.now().isoformat(timespec="seconds")
    total_found = total_new = total_updated = 0

    for config in load_search_configs():
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
            if listing.price is None or listing.year is None:
                log.debug(f"  [{i}/{len(raw_listings)}] Enriching {listing.listing_id}…")
                listing = enrich_from_listing_page(listing)
                polite_sleep()

            if dry_run:
                log.info(f"  [DRY] [{listing.listing_id}] {listing.year} "
                         f"{listing.make.title()} {listing.model.title()} — "
                         f"{listing.price}€  {listing.mileage}km  {listing.location}")
                continue

            stats = upsert_listing(conn, listing)
            if stats["new"]:
                total_new += 1
            elif stats["price_changed"] or stats["mileage_changed"]:
                total_updated += 1

    if not dry_run:
        conn.execute("""
            INSERT INTO scrape_runs (run_date, listings_found, listings_new, listings_updated)
            VALUES (?,?,?,?)
        """, (run_date, total_found, total_new, total_updated))
        conn.commit()
        log.info(f"\n{'='*60}")
        log.info(f"Geocoding new cities…")
        geocode_cities(conn)
        log.info(f"Applying filters from searches.toml…")
        apply_filters(conn)

    log.info(f"\n{'='*60}")
    log.info(f"Run complete: {total_found} found, {total_new} new, {total_updated} updated")
    log.info(f"{'='*60}\n")
    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Nettiauto scraper")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse listings without writing to the database")
    parser.add_argument("--filter-only", action="store_true",
                        help="Re-apply searches.toml filters to existing data without scraping")
    args = parser.parse_args()
    if args.filter_only:
        conn = init_db(DB_PATH)
        log.info("Geocoding new cities…")
        geocode_cities(conn)
        log.info("Applying filters from searches.toml…")
        apply_filters(conn)
        conn.close()
    else:
        run(dry_run=args.dry_run)
