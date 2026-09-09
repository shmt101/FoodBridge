# FoodBridge — Static Donations Page (no Django, no ORM)

A lightweight alternative to the full Django deployment: this queries
MySQL directly with raw SQL (via PyMySQL) and writes a plain HTML file,
which sits alongside your existing index.html and gets served by the
same static file server you already have running. Nothing about your
current deployment changes.

## One-time setup (on the server, via SSH)

1. Copy these 4 files into your project folder, next to index.html:
   `generate_donations_page.py`, `create_donations_table.sql`,
   `requirements.txt`, `.env.example`

2. Install the two dependencies (this does NOT install Django):
   ```
   pip install -r requirements.txt --break-system-packages
   ```
   (or use a venv if you prefer: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`)

3. Create the table and load sample data:
   ```
   mysql -h localhost -u db-2026s2e -p db-2026s2e < create_donations_table.sql
   ```
   Enter your DB password when prompted. This creates one table
   (`donations`) with 5 columns and inserts 4 sample rows so the page
   isn't empty on first run.

4. Set up your credentials:
   ```
   cp .env.example .env
   nano .env   # fill in DB_PASSWORD with your real server password
   ```

## Generate the page

```
python3 generate_donations_page.py
```

This writes `donations.html` into the same folder. Since your existing
`foodbridge.service` already serves that whole folder as static files,
the new page is live immediately — no restart needed.

## Add a nav link

In `index.html`, find your nav bar and add (as a plain link, not a
Django template tag — this is a static site):

```html
<li class="nav-item ms-lg-2"><a class="btn btn-nav" href="donations.html">Live Donations</a></li>
```

## Adding real donations

For now, add rows directly via SQL:

```sql
INSERT INTO donations (food_item, quantity_kg, donor_name, status)
VALUES ('Fresh bread', 5.5, 'Local Bakery', 'Pending');
```

Then re-run `python3 generate_donations_page.py` to refresh the page.
This is manual by design (matches "static snapshot" over "live"), but if
you want it to update automatically later, this same script can be
scheduled with `cron` to re-run every few minutes.

## What this demonstrates for your tutor

- A real MySQL table (`donations`) exists on the college server
- A Python script connects to it and runs actual SQL (`SELECT ... FROM donations`)
- The results — sourced from the database, not hardcoded — appear on
  the live site

The honest caveat: this is a snapshot, not a live per-request query.
Each visitor sees whatever HTML was generated last, not a fresh DB hit.
If your tutor asks for genuinely live queries, that's what the full
Django deployment (already built, sitting in `foodbridge_full_project.zip`
from earlier) is for — this is the simpler stepping stone.
