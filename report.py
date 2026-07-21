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
import math
import tomllib
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


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _ols(data):
    """OLS: price ~ 1 + mileage + age. data = list of (mileage, age, price). Returns [a, b_m, b_a] or None."""
    n = len(data)
    if n < 4:
        return None
    sumM  = sum(d[0] for d in data)
    sumA  = sum(d[1] for d in data)
    sumM2 = sum(d[0] ** 2 for d in data)
    sumMA = sum(d[0] * d[1] for d in data)
    sumA2 = sum(d[1] ** 2 for d in data)
    sumY  = sum(d[2] for d in data)
    sumMY = sum(d[0] * d[2] for d in data)
    sumAY = sum(d[1] * d[2] for d in data)
    mat = [
        [float(n),    float(sumM),  float(sumA),  float(sumY)],
        [float(sumM), float(sumM2), float(sumMA), float(sumMY)],
        [float(sumA), float(sumMA), float(sumA2), float(sumAY)],
    ]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(mat[r][col]))
        mat[col], mat[pivot] = mat[pivot], mat[col]
        if abs(mat[col][col]) < 1e-10:
            return None
        for row in range(col + 1, 3):
            f = mat[row][col] / mat[col][col]
            for k in range(col, 4):
                mat[row][k] -= f * mat[col][k]
    coeffs = [0.0] * 3
    for row in range(2, -1, -1):
        coeffs[row] = mat[row][3]
        for col in range(row + 1, 3):
            coeffs[row] -= mat[row][col] * coeffs[col]
        coeffs[row] /= mat[row][row]
    return coeffs


def generate_html(conn, output_path="report.html"):
    today = date.today().isoformat()

    filtered = conn.execute(
        "SELECT * FROM filtered_listings ORDER BY price"
    ).fetchall()

    runs = conn.execute(
        "SELECT run_date, listings_new FROM scrape_runs ORDER BY run_date"
    ).fetchall()

    latest_run_date = runs[-1]["run_date"][:10] if runs else today
    if latest_run_date:
        _lrd = datetime.strptime(latest_run_date, "%Y-%m-%d")
        latest_run_display = f"{_lrd.day} {_lrd.strftime('%b')} {_lrd.year}"
    else:
        latest_run_display = "–"

    # Origin city and per-listing distances for the distance slider
    searches_file = Path(__file__).parent / "searches.toml"
    with open(searches_file, "rb") as f:
        searches_data = tomllib.load(f)
    origin_city = next(
        (s["origin_city"] for s in searches_data.get("searches", []) if s.get("origin_city")),
        None,
    )
    max_slider_km = max(
        (s.get("max_distance_km", 0) for s in searches_data.get("searches", [])),
        default=200,
    )
    max_mileage_km = max(
        (s.get("max_mileage", 0) for s in searches_data.get("searches", [])),
        default=200000,
    )
    min_year_slider = min(
        (s["min_year"] for s in searches_data.get("searches", []) if s.get("min_year")),
        default=2005,
    )
    max_year_slider = date.today().year
    origin_lat = origin_lon = None
    if origin_city:
        row = conn.execute(
            "SELECT lat, lon FROM city_coords WHERE city_name = ?", (origin_city,)
        ).fetchone()
        if row and row["lat"]:
            origin_lat, origin_lon = row["lat"], row["lon"]

    # Known model issues — reference data for the "ⓘ" button per listing
    known_issues_by_key = {}
    known_issues_file = Path(__file__).parent / "known_issues.toml"
    if known_issues_file.exists():
        with open(known_issues_file, "rb") as f:
            known_issues_data = tomllib.load(f)
        for entry in known_issues_data.get("issues", []):
            key = f"{entry['make'].lower()}|{entry['model'].lower()}"
            known_issues_by_key.setdefault(key, []).append({
                "yearMin": entry.get("year_min"),
                "yearMax": entry.get("year_max"),
                "title": entry["title"],
                "description": entry["description"],
                "source": entry.get("source", "user"),
            })

    def _issue_matches_year(entry, year):
        if year is None:
            return True
        y_min, y_max = entry.get("yearMin"), entry.get("yearMax")
        if y_min is not None and year < y_min:
            return False
        if y_max is not None and year > y_max:
            return False
        return True

    city_coords_map = {}
    if filtered:
        locations = list({r["location"] for r in filtered if r["location"]})
        placeholders = ",".join("?" * len(locations))
        for cr in conn.execute(
            f"SELECT city_name, lat, lon FROM city_coords WHERE city_name IN ({placeholders})",
            locations,
        ).fetchall():
            if cr["lat"]:
                city_coords_map[cr["city_name"]] = (cr["lat"], cr["lon"])

    # First price per listing for price-change badges
    first_prices = {}
    if filtered:
        ids = [row["listing_id"] for row in filtered]
        placeholders = ",".join("?" * len(ids))
        for fp_row in conn.execute(
            f"SELECT listing_id, price FROM price_history "
            f"WHERE id IN (SELECT MIN(id) FROM price_history WHERE listing_id IN ({placeholders}) GROUP BY listing_id)",
            ids,
        ).fetchall():
            first_prices[fp_row["listing_id"]] = fp_row["price"]

    # Market price/mileage stats
    prices   = sorted([r["price"]   for r in filtered if r["price"]])
    mileages = sorted([r["mileage"] for r in filtered if r["mileage"]])
    def _avg(lst):    return round(sum(lst) / len(lst)) if lst else None
    def _median(lst): return lst[len(lst) // 2]         if lst else None
    stat_avg_price    = _avg(prices)
    stat_med_price    = _median(prices)
    stat_avg_mileage  = _avg(mileages)
    stat_med_mileage  = _median(mileages)

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

    model_labels = list(scatter_datasets.keys())
    model_toggle_buttons = "".join(
        f'<button class="btn-toggle active" data-model="{label}" onclick="toggleModel(this)">{label}</button>'
        for label in model_labels
    )
    model_toggle_buttons_small = "".join(
        f'<button class="btn-toggle btn-toggle-sm active" data-model="{label}" onclick="toggleModel(this)">{label}</button>'
        for label in model_labels
    )

    colors = ["#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4"]
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

    # Table rows HTML
    # Pareto frontier — not dominated on all of price, mileage, and year simultaneously
    _pc = [
        (r["listing_id"], r["price"], r["mileage"], r["year"])
        for r in filtered if r["price"] and r["mileage"] and r["year"]
    ]
    pareto_ids = set()
    for i, (lid, price, mileage, year) in enumerate(_pc):
        if not any(
            pj <= price and mj <= mileage and yj >= year and (pj < price or mj < mileage or yj > year)
            for j, (_, pj, mj, yj) in enumerate(_pc) if j != i
        ):
            pareto_ids.add(lid)

    def dom_days(first, last):
        try:
            return (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days
        except Exception:
            return None

    current_year = date.today().year
    reg_coeffs_by_model = {}
    for label in scatter_datasets:
        _model_data = [
            (r["mileage"], current_year - r["year"], r["price"])
            for r in filtered
            if r["search_label"] == label and r["price"] and r["mileage"] and r["year"] and r["year"] < current_year
        ]
        _c = _ols(_model_data)
        if _c:
            reg_coeffs_by_model[label] = _c
    reg_line_datasets = []
    for i, label in enumerate(scatter_datasets):
        _c = reg_coeffs_by_model.get(label)
        if not _c or not scatter_datasets[label]:
            continue
        _mileages = [p["x"] for p in scatter_datasets[label]]
        _ages = sorted(
            current_year - r["year"]
            for r in filtered
            if r["search_label"] == label and r["year"] and r["year"] < current_year
        )
        if not _ages:
            continue
        _med = _ages[len(_ages) // 2]
        m_min, m_max = min(_mileages), max(_mileages)
        reg_line_datasets.append({
            "label": f"{label} trend",
            "modelLabel": label,
            "data": [
                {"x": m_min, "y": round(_c[0] + _c[1] * m_min + _c[2] * _med)},
                {"x": m_max, "y": round(_c[0] + _c[1] * m_max + _c[2] * _med)},
            ],
            "color": colors[i % len(colors)],
        })
    table_rows = ""
    for r in filtered:
        days = dom_days(r["date_first_seen"], r["date_last_seen"])
        dom_str   = f"{days}d" if days is not None else "–"
        dom_class = "dom-fresh" if days is not None and days < 14 else \
                    "dom-aging" if days is not None and days < 30 else "dom-stale"
        engine = f"{r['engine_cc']}cc" if r["engine_cc"] else "–"
        fuel = r["fuel_type"] or "–"
        transmission = r["transmission"] or "–"
        _t = (r["transmission"] or "").lower()
        trans_norm = "auto" if _t.startswith("auto") else "manual" if _t.startswith("manu") else ""
        _coeffs = reg_coeffs_by_model.get(r["search_label"])
        if _coeffs and r["price"] and r["mileage"] and r["year"] and r["year"] < current_year:
            predicted = _coeffs[0] + _coeffs[1] * r["mileage"] + _coeffs[2] * (current_year - r["year"])
            deal = round(r["price"] - predicted)
            deal_val = str(deal)
            deal_str = f'−{abs(deal):,} €' if deal < 0 else f'+{deal:,} €'
            deal_css = "deal-good" if deal < 0 else "deal-bad"
        else:
            deal_val, deal_str, deal_css = "", "–", ""
        is_active = r["date_last_seen"] == latest_run_date
        is_pareto = r["listing_id"] in pareto_ids
        row_class = ("active" if is_active else "inactive") + (" pareto-row" if is_pareto else "")
        first_price = first_prices.get(r["listing_id"])
        price_badge = ""
        if first_price and r["price"] and first_price != r["price"]:
            diff = r["price"] - first_price
            if diff < 0:
                price_badge = f' <span class="badge-down">↓ {abs(diff):,} €</span>'
            else:
                price_badge = f' <span class="badge-up">↑ {diff:,} €</span>'
        dist_km = ""
        if origin_lat and r["location"] in city_coords_map:
            lat, lon = city_coords_map[r["location"]]
            dist_km = f"{_haversine(origin_lat, origin_lon, lat, lon):.0f}"
        km_yr_raw = round(r['mileage'] / (current_year - r['year'])) \
                    if r["year"] and r["mileage"] and r["year"] < current_year else None
        km_yr = f"{km_yr_raw:,}" if km_yr_raw else "–"
        pareto_badge = ' <span class="badge-pareto">★</span>' if is_pareto else ""
        issue_key = f"{(r['make'] or '').lower()}|{(r['model'] or '').lower()}"
        matching_issues = [e for e in known_issues_by_key.get(issue_key, []) if _issue_matches_year(e, r["year"])]
        info_btn = (
            f''' <button class="info-btn" onclick="showIssues('{issue_key}', {r['year'] if r['year'] else 'null'})" title="{len(matching_issues)} known issue(s) reported for this model/year">ⓘ</button>'''
            if matching_issues else ""
        )
        table_rows += f"""
        <tr class="{row_class}" data-pareto="{'1' if is_pareto else '0'}" data-dist="{dist_km}" data-mileage="{r['mileage'] or ''}" data-year="{r['year'] or ''}" data-price="{r['price'] or ''}" data-kmyr="{km_yr_raw or ''}" data-dom="{days if days is not None else ''}" data-label="{r['search_label']}" data-transmission="{trans_norm}" data-deal="{deal_val}" data-firstseen="{r['date_first_seen'] or ''}">
            <td>{r['year'] or '–'}</td>
            <td>{(r['make'] or '').title()} {(r['model'] or '').title()}{pareto_badge}{info_btn}</td>
            <td class="num">{f"{r['price']:,}" if r['price'] else '–'} €{price_badge}</td>
            <td class="num">{f"{r['mileage']:,}" if r['mileage'] else '–'} km</td>
            <td class="num">{km_yr}</td>
            <td class="num {deal_css}">{deal_str}</td>
            <td>{engine}</td>
            <td>{fuel}</td>
            <td>{transmission}</td>
            <td>{r['body_type'] or '–'}</td>
            <td>{r['location'] or '–'}</td>
            <td class="num {dom_class}">{dom_str}</td>
            <td><a href="{r['url']}" target="_blank">View →</a></td>
        </tr>"""

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
  .table-scroll {{ max-height: 780px; overflow-y: auto; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  table {{ width: 100%; border-collapse: collapse; background: white; }}
  th {{ position: sticky; top: 0; z-index: 1; background: #1e293b; color: white; text-align: center; padding: 0.6rem 0.8rem; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em; border-right: 1px solid #334155; }}
  th:last-child {{ border-right: none; }}
  td {{ padding: 0.55rem 0.8rem; border-bottom: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; font-size: 0.875rem; }}
  td:last-child {{ border-right: none; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}
  td {{ text-align: center; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  a {{ color: #3b82f6; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-top: 1rem; }}
  .chart-box {{ background: white; border-radius: 8px; padding: 1.25rem; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .chart-box.wide {{ grid-column: 1 / -1; }}
  canvas {{ max-height: 320px; }}
  tr.active td {{ background: #f0fdf4; }}
  tr.active:hover td {{ background: #dcfce7; }}
  tr.inactive td {{ color: #94a3b8; }}
  tr.inactive td a {{ color: #94a3b8; pointer-events: none; }}
  .badge-down {{ color: #16a34a; font-size: 0.75rem; font-weight: 600; margin-left: 0.3rem; }}
  .badge-up {{ color: #dc2626; font-size: 0.75rem; font-weight: 600; margin-left: 0.3rem; }}
  .filter-bar {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; background: white; padding: 0.75rem 1rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.08); flex-wrap: wrap; }}
  .filter-bar label {{ font-size: 0.875rem; color: #334155; white-space: nowrap; }}
  .filter-bar input[type=range] {{ width: 220px; accent-color: #3b82f6; }}
  .btn-toggle {{ margin-left: auto; padding: 0.4rem 0.9rem; font-size: 0.8rem; font-weight: 600; border: 1.5px solid #cbd5e1; border-radius: 6px; background: white; color: #334155; cursor: pointer; white-space: nowrap; }}
  .btn-toggle.active {{ background: #1e293b; color: white; border-color: #1e293b; }}
  .stats-bar {{ display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .stat {{ background: white; border-radius: 8px; padding: 0.75rem 1.25rem; box-shadow: 0 1px 3px rgba(0,0,0,.08); display: flex; flex-direction: column; gap: 0.2rem; min-width: 130px; }}
  .stat-label {{ font-size: 0.7rem; color: #64748b; text-transform: uppercase; letter-spacing: .05em; }}
  .stat-val {{ font-size: 1.1rem; font-weight: 600; color: #1e293b; font-variant-numeric: tabular-nums; }}
  .stat-last-run {{ border-left: 3px solid #3b82f6; margin-left: auto; }}
  .stat-last-run .stat-val {{ font-size: 1.25rem; color: #1d4ed8; }}
  .dom-fresh {{ color: #16a34a; font-weight: 600; }}
  .dom-aging {{ color: #d97706; font-weight: 600; }}
  .dom-stale {{ color: #dc2626; font-weight: 600; }}
  .badge-pareto {{ color: #d97706; font-size: 0.85rem; margin-left: 0.3rem; }}
  tr.pareto-row.active td {{ background: #fffbeb; }}
  tr.pareto-row.active:hover td {{ background: #fef3c7; }}
  th.sortable {{ cursor: pointer; user-select: none; }}
  th.sortable:hover {{ background: #2d3f55; }}
  th.sort-desc::after {{ content: ' ↓'; opacity: 0.8; }}
  th.sort-asc::after  {{ content: ' ↑'; opacity: 0.8; }}
  .deal-good {{ color: #16a34a; font-weight: 600; }}
  .deal-bad  {{ color: #dc2626; font-weight: 600; }}
  .filter-bar select {{ font-size: 0.875rem; color: #334155; border: 1.5px solid #cbd5e1; border-radius: 6px; padding: 0.3rem 0.5rem; background: white; cursor: pointer; }}
  .btn-toggle-sm {{ margin-left: 0 !important; padding: 0.2rem 0.6rem; font-size: 0.75rem; }}
  .model-bar-sm {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.75rem; }}
  .info-btn {{ border: none; background: none; color: #64748b; cursor: pointer; font-size: 0.95rem; vertical-align: middle; padding: 0 0.2rem; line-height: 1; }}
  .info-btn:hover {{ color: #1d4ed8; }}
  .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(15,23,42,.5); z-index: 50; align-items: center; justify-content: center; padding: 1rem; }}
  .modal-overlay.open {{ display: flex; }}
  .modal-box {{ background: white; border-radius: 10px; max-width: 560px; width: 100%; max-height: 80vh; overflow-y: auto; padding: 1.25rem 1.5rem; box-shadow: 0 10px 30px rgba(0,0,0,.2); }}
  .modal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }}
  .modal-header h3 {{ font-size: 1.05rem; color: #1e293b; }}
  .modal-close {{ border: none; background: none; font-size: 1rem; cursor: pointer; color: #64748b; }}
  .modal-close:hover {{ color: #1e293b; }}
  .issue-item {{ padding: 0.75rem 0; border-bottom: 1px solid #f1f5f9; }}
  .issue-item:last-child {{ border-bottom: none; }}
  .issue-title {{ font-weight: 600; font-size: 0.9rem; color: #1e293b; margin-bottom: 0.25rem; }}
  .issue-desc {{ font-size: 0.825rem; color: #475569; line-height: 1.4; }}
  .issue-source {{ display: inline-block; margin-top: 0.4rem; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; padding: 0.15rem 0.5rem; border-radius: 999px; }}
  .source-recall {{ background: #fee2e2; color: #b91c1c; }}
  .source-forum {{ background: #dbeafe; color: #1d4ed8; }}
  .source-user {{ background: #f1f5f9; color: #475569; }}
  .source-general-knowledge {{ background: #f1f5f9; color: #475569; }}
</style>
</head>
<body>
<h1>Nettiauto Tracker</h1>
<p class="meta">Generated {today} &nbsp;·&nbsp; {len(filtered)} filtered listings</p>

<h2>Matched Listings</h2>
<div class="stats-bar">
  <div class="stat"><span class="stat-label">Avg price</span><span class="stat-val">{f"{stat_avg_price:,} €" if stat_avg_price else "–"}</span></div>
  <div class="stat"><span class="stat-label">Median price</span><span class="stat-val">{f"{stat_med_price:,} €" if stat_med_price else "–"}</span></div>
  <div class="stat"><span class="stat-label">Avg mileage</span><span class="stat-val">{f"{stat_avg_mileage:,} km" if stat_avg_mileage else "–"}</span></div>
  <div class="stat"><span class="stat-label">Median mileage</span><span class="stat-val">{f"{stat_med_mileage:,} km" if stat_med_mileage else "–"}</span></div>
  <div class="stat stat-last-run"><span class="stat-label">Last run</span><span class="stat-val">{latest_run_display}</span></div>
</div>
<div class="filter-bar">
  <label for="distSlider">Max distance from {origin_city or "origin"}: <strong id="distVal">{max_slider_km}</strong> km</label>
  <input type="range" id="distSlider" min="10" max="{max_slider_km}" step="10" value="{max_slider_km}">
  <label for="mileageSlider" style="margin-left:1.5rem">Max mileage: <strong id="mileageVal">{max_mileage_km:,}</strong> km</label>
  <input type="range" id="mileageSlider" min="10000" max="{max_mileage_km}" step="5000" value="{max_mileage_km}">
  <label for="yearSlider" style="margin-left:1.5rem">Min year: <strong id="yearVal">{min_year_slider}</strong></label>
  <input type="range" id="yearSlider" min="{min_year_slider}" max="{max_year_slider}" step="1" value="{min_year_slider}">
  <label for="firstSeenFilter" style="margin-left:1.5rem">First seen:</label>
  <select id="firstSeenFilter" onchange="applyFilters()">
    <option value="0">All time</option>
    <option value="5">Last 5 days</option>
    <option value="10">Last 10 days</option>
    <option value="20">Last 20 days</option>
  </select>
  <button class="btn-toggle" id="hideSoldBtn" onclick="toggleHideSold()">Hide sold</button>
  <button class="btn-toggle" id="paretoBtn" onclick="toggleParetoOnly()">Best value only</button>
  <button class="btn-toggle" id="transBtn" onclick="toggleTrans()">All transmissions</button>
</div>
<div class="filter-bar">
  <span style="font-size:0.875rem;color:#334155;font-weight:600;white-space:nowrap">Models:</span>
  {model_toggle_buttons}
</div>
<div class="table-scroll">
<table>
  <thead><tr>
    <th class="sortable" data-sort="year">Year</th><th>Model</th><th class="sortable" data-sort="price">Price</th><th class="sortable" data-sort="mileage">Mileage</th><th class="sortable" data-sort="kmyr">km/yr</th><th class="sortable" data-sort="deal" title="Actual price minus model-predicted price. Negative (green) = cheaper than market expects.">vs market</th>
    <th>Engine</th><th>Fuel</th><th>Transmission</th><th>Body</th><th>Location</th><th class="sortable" data-sort="dom">On market</th><th>Link</th>
  </tr></thead>
  <tbody>{table_rows}</tbody>
</table>
</div>

<h2>Charts <button class="btn-toggle" id="trendBtn" onclick="toggleTrendLines()" style="margin-left:1rem;font-size:0.75rem;vertical-align:middle">Hide trend lines</button></h2>
<div class="model-bar-sm">{model_toggle_buttons_small}</div>
<div class="charts">
  <div class="chart-box wide">
    <canvas id="scatter"></canvas>
  </div>
</div>

<div class="modal-overlay" id="issuesModal" onclick="if(event.target===this) closeIssuesModal()">
  <div class="modal-box">
    <div class="modal-header">
      <h3 id="issuesModalTitle"></h3>
      <button class="modal-close" onclick="closeIssuesModal()">✕</button>
    </div>
    <div id="issuesModalBody"></div>
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
const regLineData = {json.dumps(reg_line_datasets)};
const regLineModelMap = Object.fromEntries(regLineData.map(d => [d.label, d.modelLabel]));
regLineData.forEach(d => {{
    scatter.data.datasets.push({{
        type: 'line', label: d.label, data: d.data,
        borderColor: d.color, borderWidth: 1.5, borderDash: [6, 4],
        pointRadius: 0, fill: false,
    }});
}});
scatter.update();

const enabledModels = new Set({json.dumps(model_labels)});
let showTrendLines = true;
function updateChartVisibility() {{
    scatter.data.datasets.forEach(ds => {{
        if (ds.type === 'line') {{
            ds.hidden = !showTrendLines || !enabledModels.has(regLineModelMap[ds.label]);
        }} else {{
            ds.hidden = !enabledModels.has(ds.label);
        }}
    }});
}}
function toggleModel(btn) {{
    const label = btn.dataset.model;
    const allBtns = document.querySelectorAll('[data-model="' + label + '"]');
    if (enabledModels.has(label)) {{
        enabledModels.delete(label);
        allBtns.forEach(b => b.classList.remove('active'));
    }} else {{
        enabledModels.add(label);
        allBtns.forEach(b => b.classList.add('active'));
    }}
    updateChartVisibility();
    scatter.update();
    applyFilters();
}}
function toggleTrendLines() {{
    showTrendLines = !showTrendLines;
    const btn = document.getElementById('trendBtn');
    btn.textContent = showTrendLines ? 'Hide trend lines' : 'Show trend lines';
    btn.classList.toggle('active', !showTrendLines);
    updateChartVisibility();
    scatter.update();
}}

let hideSold = false;
function toggleHideSold() {{
    hideSold = !hideSold;
    const btn = document.getElementById('hideSoldBtn');
    btn.textContent = hideSold ? 'Show sold' : 'Hide sold';
    btn.classList.toggle('active', hideSold);
    applyFilters();
}}
let paretoOnly = false;
function toggleParetoOnly() {{
    paretoOnly = !paretoOnly;
    document.getElementById('paretoBtn').classList.toggle('active', paretoOnly);
    applyFilters();
}}
let transFilter = 0;
const transLabels = ['All transmissions', 'Automatic only', 'Manual only'];
function toggleTrans() {{
    transFilter = (transFilter + 1) % 3;
    const btn = document.getElementById('transBtn');
    btn.textContent = transLabels[transFilter];
    btn.classList.toggle('active', transFilter !== 0);
    applyFilters();
}}
function applyFilters() {{
    const maxDist      = parseInt(document.getElementById('distSlider').value);
    const maxMileage   = parseInt(document.getElementById('mileageSlider').value);
    const minYear      = parseInt(document.getElementById('yearSlider').value);
    const firstSeenDays = parseInt(document.getElementById('firstSeenFilter').value);
    const cutoffStr    = firstSeenDays > 0
        ? new Date(Date.now() - firstSeenDays * 86400000).toISOString().slice(0, 10)
        : '';
    document.querySelectorAll('tbody tr').forEach(tr => {{
        const dist    = tr.dataset.dist;
        const mileage = tr.dataset.mileage;
        const year    = tr.dataset.year;
        const distOk      = dist === ''    || parseFloat(dist)    <= maxDist;
        const mileageOk   = mileage === '' || parseFloat(mileage) <= maxMileage;
        const yearOk      = year === ''    || parseInt(year)      >= minYear;
        const modelOk     = enabledModels.has(tr.dataset.label);
        const soldOk      = !hideSold  || tr.classList.contains('active');
        const paretoOk    = !paretoOnly || tr.dataset.pareto === '1';
        const transOk     = transFilter === 0 || (transFilter === 1 && tr.dataset.transmission === 'auto') || (transFilter === 2 && tr.dataset.transmission === 'manual');
        const firstSeenOk = !cutoffStr || (tr.dataset.firstseen && tr.dataset.firstseen >= cutoffStr);
        tr.style.display = (distOk && mileageOk && yearOk && modelOk && soldOk && paretoOk && transOk && firstSeenOk) ? '' : 'none';
    }});
}}

let sortCol = null, sortDir = 'desc';
document.querySelectorAll('th.sortable').forEach(th => {{
    th.addEventListener('click', () => {{
        const col = th.dataset.sort;
        sortDir = (sortCol === col && sortDir === 'desc') ? 'asc' : 'desc';
        sortCol = col;
        document.querySelectorAll('th.sortable').forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
        th.classList.add(sortDir === 'desc' ? 'sort-desc' : 'sort-asc');
        const tbody = document.querySelector('tbody');
        Array.from(tbody.querySelectorAll('tr'))
            .sort((a, b) => {{
                const av = parseFloat(a.dataset[col]);
                const bv = parseFloat(b.dataset[col]);
                const an = isNaN(av), bn = isNaN(bv);
                if (an && bn) return 0;
                if (an) return 1;
                if (bn) return -1;
                return sortDir === 'desc' ? bv - av : av - bv;
            }})
            .forEach(row => tbody.appendChild(row));
    }});
}});

const slider = document.getElementById('distSlider');
const distVal = document.getElementById('distVal');
slider.addEventListener('input', () => {{ distVal.textContent = parseInt(slider.value).toLocaleString(); applyFilters(); }});

const mileageSlider = document.getElementById('mileageSlider');
const mileageVal = document.getElementById('mileageVal');
mileageSlider.addEventListener('input', () => {{ mileageVal.textContent = parseInt(mileageSlider.value).toLocaleString(); applyFilters(); }});

const yearSlider = document.getElementById('yearSlider');
const yearVal = document.getElementById('yearVal');
yearSlider.addEventListener('input', () => {{ yearVal.textContent = yearSlider.value; applyFilters(); }});

const knownIssuesData = {json.dumps(known_issues_by_key)};
const sourceLabels = {{
    recall: 'Recall / TSB',
    forum: 'Owner forums',
    user: 'User-reported',
    'general-knowledge': 'General knowledge — unverified',
}};
function showIssues(key, year) {{
    const entries = (knownIssuesData[key] || []).filter(e => {{
        const yMin = e.yearMin ?? -Infinity, yMax = e.yearMax ?? Infinity;
        return year === null || (year >= yMin && year <= yMax);
    }});
    const [make, model] = key.split('|');
    const titleCase = s => s.charAt(0).toUpperCase() + s.slice(1);
    document.getElementById('issuesModalTitle').textContent =
        `${{titleCase(make)}} ${{titleCase(model)}}${{year ? ' (' + year + ')' : ''}} — known issues`;
    document.getElementById('issuesModalBody').innerHTML = entries.length
        ? entries.map(e => `
            <div class="issue-item">
                <div class="issue-title">${{e.title}}</div>
                <div class="issue-desc">${{e.description}}</div>
                <span class="issue-source source-${{e.source}}">${{sourceLabels[e.source] || e.source}}</span>
            </div>`).join('')
        : '<p style="font-size:0.85rem;color:#64748b">No reports specific to this year — see the model\\'s general entries.</p>';
    document.getElementById('issuesModal').classList.add('open');
}}
function closeIssuesModal() {{
    document.getElementById('issuesModal').classList.remove('open');
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeIssuesModal(); }});
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
