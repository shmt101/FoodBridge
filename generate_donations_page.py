#!/usr/bin/env python3
"""
Generates a static donations.html page from the 'donations' MySQL table.

No Django, no ORM — this is plain SQL via PyMySQL. Run it whenever the
donations data changes, and it writes donations.html into this same
folder, right next to index.html, so your existing static file server
picks it up automatically.

Usage:
    python3 generate_donations_page.py

Requires (install once):
    pip install pymysql python-dotenv
"""

import os
import sys
from datetime import datetime

try:
    import pymysql
except ImportError:
    sys.exit("Missing dependency. Run: pip install pymysql python-dotenv")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    sys.exit("Missing dependency. Run: pip install pymysql python-dotenv")

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "db-2026s2e")
DB_USER = os.environ.get("DB_USER", "db-2026s2e")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donations.html")

STATUS_COLORS = {
    "Pending": ("#fff7dd", "#7a5c00"),
    "Assigned": ("#f1e9ff", "#6a3fc7"),
    "In transit": ("#fff0ec", "#c7502f"),
    "Delivered": ("#e6f7ec", "#073b2a"),
    "Cancelled": ("#f1f3f2", "#5b6b62"),
}


def fetch_donations():
    """Raw SQL via PyMySQL — no ORM involved. Returns a list of dict rows."""
    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT food_item, quantity_kg, donor_name, status, date_listed "
                "FROM donations ORDER BY date_listed DESC;"
            )
            rows = cur.fetchall()

            cur.execute(
                "SELECT COALESCE(SUM(quantity_kg), 0) AS total_kg FROM donations "
                "WHERE status != 'Cancelled';"
            )
            total_kg = cur.fetchone()["total_kg"]

            cur.execute(
                "SELECT COUNT(*) AS delivered_count FROM donations WHERE status = 'Delivered';"
            )
            delivered_count = cur.fetchone()["delivered_count"]
        return rows, float(total_kg), delivered_count
    finally:
        conn.close()


def build_html(rows, total_kg, delivered_count):
    """Pure templating — no DB access here, so this half is easy to test standalone."""
    row_html = ""
    for r in rows:
        bg, fg = STATUS_COLORS.get(r["status"], ("#f1f3f2", "#5b6b62"))
        date_str = r["date_listed"].strftime("%d %b, %I:%M %p") if isinstance(r["date_listed"], datetime) else str(r["date_listed"])
        row_html += f"""
        <tr>
          <td>{r['food_item']}</td>
          <td>{r['quantity_kg']:.2f} kg</td>
          <td>{r['donor_name']}</td>
          <td><span class="status" style="background:{bg};color:{fg};">{r['status']}</span></td>
          <td>{date_str}</td>
        </tr>"""

    if not rows:
        row_html = '<tr><td colspan="5" class="empty">No donations listed yet.</td></tr>'

    generated_at = datetime.now().strftime("%d %b %Y, %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Live Donations · FoodBridge</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {{ --forest: #073b2a; --fresh: #22c55e; --line: #dce8df; --muted: #5b6b62; --cream: #f7faf8; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: 'DM Sans', sans-serif; background: var(--cream); color: #17211b; }}
  h1 {{ font-family: 'Manrope', sans-serif; font-weight: 800; }}
  header {{ display: flex; align-items: center; justify-content: space-between; padding: 20px 32px; background: #fff; border-bottom: 1px solid var(--line); }}
  .logo {{ font-weight: 800; font-size: 18px; color: var(--forest); text-decoration: none; }}
  .logo span {{ color: var(--fresh); }}
  .back-link {{ font-size: 13px; color: var(--muted); text-decoration: none; }}
  main {{ max-width: 900px; margin: 0 auto; padding: 32px 24px 60px; }}
  .badge-live {{ display: inline-flex; align-items: center; gap: 6px; background: #e6f7ec; color: var(--forest); font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 20px; margin-bottom: 14px; }}
  .dot {{ width: 6px; height: 6px; border-radius: 50%; background: var(--fresh); }}
  h1 {{ font-size: 26px; margin: 0 0 6px; }}
  .sub {{ color: var(--muted); font-size: 14px; margin: 0 0 28px; }}
  .stats {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; margin-bottom: 32px; }}
  .stat-card {{ background: #fff; border: 1px solid var(--line); border-radius: 16px; padding: 18px; text-align: center; }}
  .stat-value {{ font-family: 'Manrope', sans-serif; font-weight: 800; font-size: 26px; color: var(--forest); }}
  .stat-label {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 16px; overflow: hidden; border: 1px solid var(--line); }}
  thead th {{ text-align: left; font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); padding: 12px 16px; background: var(--cream); border-bottom: 1px solid var(--line); }}
  tbody td {{ padding: 14px 16px; font-size: 13.5px; border-bottom: 1px solid #f0f4f1; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .status {{ display: inline-block; padding: 4px 11px; border-radius: 20px; font-size: 11.5px; font-weight: 700; }}
  .empty {{ text-align: center; padding: 40px 0; color: var(--muted); font-size: 14px; }}
  .note {{ font-size: 12px; color: var(--muted); margin-top: 18px; text-align: center; }}
  @media (max-width: 640px) {{
    .stats {{ grid-template-columns: 1fr; }}
    table, thead {{ display: none; }}
    tbody tr {{ display: block; border-bottom: 1px solid var(--line); padding: 12px 16px; }}
    tbody td {{ display: block; border: none; padding: 3px 0; }}
    tbody td:first-child {{ font-weight: 700; }}
  }}
</style>
</head>
<body>

<header>
  <a href="index.html" class="logo">Food<span>Bridge</span></a>
  <a href="index.html" class="back-link">&larr; Back to home</a>
</header>

<main>
  <div class="badge-live"><span class="dot"></span> Snapshot from the database &mdash; generated {generated_at}</div>
  <h1>Donations in motion</h1>
  <p class="sub">Pulled directly from MySQL via a Python script &mdash; no framework, just SQL.</p>

  <div class="stats">
    <div class="stat-card">
      <div class="stat-value">{total_kg:.0f} kg</div>
      <div class="stat-label">Rescued so far</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{delivered_count}</div>
      <div class="stat-label">Completed deliveries</div>
    </div>
  </div>

  <table>
    <thead>
      <tr><th>Food</th><th>Quantity</th><th>Donor</th><th>Status</th><th>Listed</th></tr>
    </thead>
    <tbody>{row_html}
    </tbody>
  </table>

  <p class="note">This page is a snapshot, not live &mdash; re-run generate_donations_page.py after adding new donations.</p>
</main>

</body>
</html>
"""


def main():
    print(f"Connecting to {DB_USER}@{DB_HOST}/{DB_NAME} ...")
    rows, total_kg, delivered_count = fetch_donations()
    html = build_html(rows, total_kg, delivered_count)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {len(rows)} donation(s), {total_kg:.0f} kg total, to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
