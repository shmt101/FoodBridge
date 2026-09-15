# FoodBridge — Django app setup

This replaces the old "static snapshot" approach (`generate_donations_page.py`)
with a real Django app: Django ORM, RBAC auth, per-role dashboards, a live
search/filter donations page, and a CSV export for auditors. The old static
files (`index.html`, `assets/`, `generate_donations_page.py`, etc.) are kept
in the repo root for reference but are no longer what serves the site —
`templates/index.html` and the Django views take over.

## 1. Local setup (test this first)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate          # uses SQLite locally — no DB_NAME needed
python manage.py seed_demo_data   # floods the DB with demo donors/recipients/drivers/donations
python manage.py runserver
```

Visit `http://127.0.0.1:8000/`. Log in with the seeded auditor account:
**username `auditor`, password `foodbridge123`** — or any of `donor1..donor6`,
`recipient1..recipient5`, `driver1..driver4` (same password) to see each
role's dashboard. Change/remove these before going live.

## 2. Deploying on the college server

1. Copy the whole project folder to the server (everything except `venv/`,
   `db.sqlite3`, `staticfiles/`, and `__pycache__/` — those get regenerated).
2. Create a venv and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Set up real credentials:
   ```bash
   cp .env.example .env
   nano .env   # fill in DB_PASSWORD, DJANGO_SECRET_KEY, set DJANGO_DEBUG=False
   ```
4. Create the MySQL tables via Django (this replaces
   `create_donations_table.sql` — Django manages the schema now):
   ```bash
   python manage.py migrate
   ```
   This creates the `accounts_user` and `donations_donation` tables (plus
   Django's own auth/session tables) using your existing `db-2026s2e` MySQL
   database — same server, same credentials as the old script.
5. (Optional) seed demo data so the site isn't empty:
   ```bash
   python manage.py seed_demo_data
   ```
6. Collect static files and create a real admin user:
   ```bash
   python manage.py collectstatic --noinput
   python manage.py createsuperuser
   ```
7. Update `deployment/foodbridge.service` to run Django instead of
   `http.server` — e.g. with gunicorn:
   ```ini
   ExecStart=/path/to/venv/bin/gunicorn foodbridge.wsgi:application --bind 0.0.0.0:8096
   ```
   (add `gunicorn` to `requirements.txt` if you go this route), then
   `sudo systemctl daemon-reload && sudo systemctl restart foodbridge`.

## 3. What each of your 5 use cases maps to

| Use case | Where |
|---|---|
| Live donation page linked to dashboards | `/donations/live/` (`donations/views.py::live_donations`) — real ORM query, not a snapshot |
| Auth / signup with RBAC | `/accounts/signup/`, `/accounts/login/` — role chosen at signup, enforced by `accounts/decorators.py::role_required` |
| Search and filter | Same live donations page — search box + status dropdown, also reachable from the homepage nav bar |
| CSV report for auditors | `/donations/reports/delivered.csv`, gated to the Admin/Auditor role |
| Donor / Recipient / Driver dashboards + profile manager | `/donations/dashboard/donor|recipient|driver/`, `/accounts/profile/` |

## 4. Next things worth doing before submission/demo

- Swap the seeded passwords and delete/rename the demo accounts.
- Add `django-crispy-forms` or similar if you want nicer form styling than
  the current Bootstrap classes.
- Consider pagination on the live donations table once it has real volume.
- Add automated tests (`donations/tests.py`, `accounts/tests.py` — currently
  empty stubs from `startapp`).
