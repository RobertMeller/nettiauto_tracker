"""
report.py  —  View and export data from nettiauto_listings.db

Usage:
    python report.py              # print summary table
    python report.py --export     # also export to CSV
    python report.py --history 15521636   # price history for one listing
    python report.py --new        # only listings seen today
    python report.py --html       # generate report.html
"""

import sqlite3
import csv
import argparse
import sys
import json
from datetime import date, datetime
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
        print(f"\n{'='*120}")
        print(f"  {title}")
        print(f"{'='*120}")

    fmt = "{:<10} {:<6} {:<12} {:>6} {:>10} {:>8}  {:<16} {:<14} {:<14}  {:<12} {:<12}"
    header = fmt.format(
        "ID", "Year", "Model", "Price€", "Mileage km",
        "Engine", "Location", "Fuel", "Transmission",
        "First seen", "Last seen"
    )
    print(header)
    print("-" * 120)

    for r in rows:
        engine = f"{r['engine_cc']}cc" if r["engine_cc"] else "–"
        location = (r["location"] or "–")[:16]
        model_str = f"{(r['make'] or '').title()} {(r['model'] or '').title()}"[:12]
        fuel = (r["fuel_type"] or "–")[:14]
        transmission = (r["transmission"] or "–")[:14]
        print(fmt.format(
            r["listing_id"],
            r["year"] or "–",
            model_str,
            f"{r['price']:,}" if r["price"] else "–",
            f"{r['mileage']:,}" if r["mileage"] else "–",
            engine,
            location,
            fuel,
            transmission,
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


def generate_html(conn, output_path="report.html"):
    today = date.today().isoformat()

    filtered = conn.execute(
        "SELECT * FROM filtered_listings ORDER BY price"
    ).fetchall()

    runs = conn.execute(
        "SELECT run_date, listings_new FROM scrape_runs ORDER BY run_date"
    ).fetchall()

    # Price history for filtered listings
    history_by_id = {}
    for row in filtered:
        lid = row["listing_id"]
        rows = conn.execute(
            "SELECT recorded_date, price FROM price_history WHERE listing_id=? ORDER BY recorded_date",
            (lid,)
        ).fetchall()
        if len(rows) > 1:
            history_by_id[lid] = [{"x": r["recorded_date"], "y": r["price"]} for r in rows]

    # Scatter data — one point per filtered listing
    scatter_datasets = {}
    for row in filtered:
        label = row["search_label"]
        if label not in scatter_datasets:
            scatter_datasets[label] = []
        if row["mileage"] and row["price"]:
            scatter_datasets[label].append({
                "x": row["mileage"],
                "y": row["price"],
                "label": f"{row['year']} {row['make'].title()} {row['model'].title()} — {row['location']}",
            })

    colors = ["#3b82f6", "#f59e0b", "#10b981", "#ef4444"]
    scatter_chart_datasets = [
        {
            "label": label,
            "data": points,
            "backgroundColor": colors[i % len(colors)],
            "pointRadius": 7,
            "pointHoverRadius": 9,
        }
        for i, (label, points) in enumerate(scatter_datasets.items())
    ]

    # Activity bar chart
    activity_labels = [r["run_date"][:10] for r in runs]
    activity_data = [r["listings_new"] for r in runs]

    # Price history chart datasets
    history_datasets = []
    for i, (lid, points) in enumerate(history_by_id.items()):
        row = next(r for r in filtered if r["listing_id"] == lid)
        history_datasets.append({
            "label": f"{row['year']} {row['make'].title()} {row['model'].title()} ({lid})",
            "data": points,
            "borderColor": colors[i % len(colors)],
            "backgroundColor": "transparent",
            "tension": 0.3,
        })

    # Table rows HTML
    def days_on_market(first, last):
        try:
            d = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days
            return f"{d}d"
        except Exception:
            return "–"

    table_rows = ""
    for r in filtered:
        dom = days_on_market(r["date_first_seen"], r["date_last_seen"])
        engine = f"{r['engine_cc']}cc" if r["engine_cc"] else "–"
        fuel = r["fuel_type"] or "–"
        table_rows += f"""
        <tr>
            <td>{r['year'] or '–'}</td>
            <td>{(r['make'] or '').title()} {(r['model'] or '').title()}</td>
            <td class="num">{f"{r['price']:,}" if r['price'] else '–'} €</td>
            <td class="num">{f"{r['mileage']:,}" if r['mileage'] else '–'} km</td>
            <td>{engine}</td>
            <td>{fuel}</td>
            <td>{r['location'] or '–'}</td>
            <td class="num">{dom}</td>
            <td><a href="{r['url']}" target="_blank">View →</a></td>
        </tr>"""

    show_history = "block" if history_datasets else "none"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nettiauto Report — {today}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; background: #f8fafc; color: #1e293b; padding: 2rem; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #64748b; font-size: 0.875rem; margin-bottom: 2rem; }}
  h2 {{ font-size: 1.1rem; margin: 2rem 0 0.75rem; color: #334155; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  th {{ background: #1e293b; color: white; text-align: left; padding: 0.6rem 0.8rem; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em; }}
  td {{ padding: 0.55rem 0.8rem; border-bottom: 1px solid #f1f5f9; font-size: 0.875rem; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  a {{ color: #3b82f6; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-top: 1rem; }}
  .chart-box {{ background: white; border-radius: 8px; padding: 1.25rem; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .chart-box.wide {{ grid-column: 1 / -1; }}
  canvas {{ max-height: 320px; }}
</style>
</head>
<body>
<h1>Nettiauto Tracker</h1>
<p class="meta">Generated {today} &nbsp;·&nbsp; {len(filtered)} filtered listings</p>

<h2>Matched Listings</h2>
<table>
  <thead><tr>
    <th>Year</th><th>Model</th><th>Price</th><th>Mileage</th>
    <th>Engine</th><th>Fuel</th><th>Location</th><th>On market</th><th>Link</th>
  </tr></thead>
  <tbody>{table_rows}</tbody>
</table>

<h2>Charts</h2>
<div class="charts">
  <div class="chart-box">
    <canvas id="scatter"></canvas>
  </div>
  <div class="chart-box">
    <canvas id="activity"></canvas>
  </div>
  <div class="chart-box wide" style="display:{show_history}" id="historyBox">
    <canvas id="history"></canvas>
  </div>
</div>

<script>
const scatter = new Chart(document.getElementById('scatter'), {{
  type: 'scatter',
  data: {{ datasets: {json.dumps(scatter_chart_datasets)} }},
  options: {{
    plugins: {{
      title: {{ display: true, text: 'Price vs Mileage' }},
      tooltip: {{ callbacks: {{ label: ctx => ctx.raw.label + ' — ' + ctx.raw.y.toLocaleString() + ' € / ' + ctx.raw.x.toLocaleString() + ' km' }} }}
    }},
    scales: {{
      x: {{ title: {{ display: true, text: 'Mileage (km)' }} }},
      y: {{ title: {{ display: true, text: 'Price (€)' }} }}
    }}
  }}
}});

const activity = new Chart(document.getElementById('activity'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(activity_labels)},
    datasets: [{{ label: 'New listings', data: {json.dumps(activity_data)}, backgroundColor: '#3b82f6' }}]
  }},
  options: {{
    plugins: {{ title: {{ display: true, text: 'New Listings per Scrape Run' }} }},
    scales: {{ y: {{ beginAtZero: true }} }}
  }}
}});

{"" if not history_datasets else f"""
const history = new Chart(document.getElementById('history'), {{
  type: 'line',
  data: {{ datasets: {json.dumps(history_datasets)} }},
  options: {{
    plugins: {{ title: {{ display: true, text: 'Price History' }} }},
    scales: {{
      x: {{ type: 'category', title: {{ display: true, text: 'Date' }} }},
      y: {{ title: {{ display: true, text: 'Price (€)' }} }}
    }}
  }}
}});
"""}
</script>
</body>
</html>"""

    Path(output_path).write_text(html, encoding="utf-8")
    print(f"Report written to {output_path}")


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
    parser.add_argument("--filtered", action="store_true",
                        help="Show only listings that matched your searches.toml criteria")
    parser.add_argument("--html", action="store_true",
                        help="Generate HTML report with charts")
    parser.add_argument("--output", default="report.html", metavar="PATH",
                        help="Output path for --html (default: report.html)")
    args = parser.parse_args()

    conn = connect()

    if args.history:
        print_history(conn, args.history)
        conn.close()
        return

    today = date.today().isoformat()

    if args.filtered:
        rows = conn.execute(
            "SELECT * FROM filtered_listings ORDER BY " + args.sort
        ).fetchall()
        title = f"Filtered listings  ({len(rows)} records)"
    elif args.new:
        rows = conn.execute(
            "SELECT * FROM listings WHERE date_first_seen = ? ORDER BY " + args.sort,
            (today,),
        ).fetchall()
        title = f"New listings today  ({len(rows)} records)"
    else:
        rows = conn.execute(
            "SELECT * FROM listings ORDER BY " + args.sort
        ).fetchall()
        title = f"All tracked listings  ({len(rows)} records)"

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

    if args.html:
        generate_html(conn, args.output)

    conn.close()


if __name__ == "__main__":
    main()
