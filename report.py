"""
report.py  —  View and export data from nettiauto_listings.db

Usage:
    python report.py              # print summary table
    python report.py --export     # also export to CSV
    python report.py --history 15521636   # price history for one listing
    python report.py --new        # only listings seen today
"""

import sqlite3
import csv
import argparse
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = "nettiauto_listings.db"


def connect():
    if not Path(DB_PATH).exists():
        print(f"Database not found: {DB_PATH}")
        print("Run scraper.py first.")
        raise SystemExit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def print_listings(rows, title=""):
    if title:
        print(f"\n{'='*90}")
        print(f"  {title}")
        print(f"{'='*90}")

    fmt = "{:<10} {:<6} {:<12} {:>6} {:>10} {:>8}  {:<20} {:<12} {:<12}"
    header = fmt.format(
        "ID", "Year", "Model", "Price€", "Mileage km",
        "Engine", "Location", "First seen", "Last seen"
    )
    print(header)
    print("-" * 90)

    for r in rows:
        engine = f"{r['engine_cc']}cc" if r["engine_cc"] else "–"
        location = (r["location"] or "–")[:20]
        model_str = f"{(r['make'] or '').title()} {(r['model'] or '').title()}"[:12]
        print(fmt.format(
            r["listing_id"],
            r["year"] or "–",
            model_str,
            f"{r['price']:,}" if r["price"] else "–",
            f"{r['mileage']:,}" if r["mileage"] else "–",
            engine,
            location,
            r["date_first_seen"],
            r["date_last_seen"],
        ))


def print_history(conn, listing_id: int):
    listing = conn.execute(
        "SELECT * FROM listings WHERE listing_id=?", (listing_id,)
    ).fetchone()
    if not listing:
        print(f"Listing {listing_id} not found.")
        return

    print(f"\nPrice history for [{listing_id}] "
          f"{listing['year']} {listing['make'].title()} {listing['model'].title()} — {listing['url']}")
    print(f"{'Date':<14} {'Price €':>10} {'Mileage km':>12}")
    print("-" * 40)

    rows = conn.execute("""
        SELECT recorded_date, price, mileage
        FROM price_history
        WHERE listing_id=?
        ORDER BY recorded_date
    """, (listing_id,)).fetchall()

    for r in rows:
        print(f"{r['recorded_date']:<14} "
              f"{r['price']:>10,} "
              f"{(r['mileage'] or 0):>12,}")


def export_csv(conn, filename="nettiauto_export.csv"):
    rows = conn.execute("SELECT * FROM listings ORDER BY price").fetchall()
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    print(f"\nExported {len(rows)} listings to {filename}")


def main():
    parser = argparse.ArgumentParser(description="Nettiauto listings report")
    parser.add_argument("--export", action="store_true", help="Export to CSV")
    parser.add_argument("--history", type=int, metavar="LISTING_ID",
                        help="Show price history for a listing")
    parser.add_argument("--new", action="store_true",
                        help="Show only listings seen today")
    parser.add_argument("--sort", default="price",
                        choices=["price", "year", "mileage", "date_first_seen"],
                        help="Sort column")
    args = parser.parse_args()

    conn = connect()

    if args.history:
        print_history(conn, args.history)
        conn.close()
        return

    today = date.today().isoformat()
    if args.new:
        rows = conn.execute(
            "SELECT * FROM listings WHERE date_first_seen = ? ORDER BY " + args.sort,
            (today,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM listings ORDER BY " + args.sort
        ).fetchall()

    title = f"{'New listings today' if args.new else 'All tracked listings'}  "
    title += f"({len(rows)} records)"
    print_listings(rows, title)

    # Run stats
    runs = conn.execute(
        "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 5"
    ).fetchall()
    if runs:
        print(f"\n{'Last 5 scrape runs':}")
        print(f"{'Date':<22} {'Found':>7} {'New':>6} {'Updated':>9}")
        print("-" * 50)
        for r in runs:
            print(f"{r['run_date']:<22} {r['listings_found']:>7} "
                  f"{r['listings_new']:>6} {r['listings_updated']:>9}")

    if args.export:
        export_csv(conn)

    conn.close()


if __name__ == "__main__":
    main()
