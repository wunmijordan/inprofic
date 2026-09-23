# INPROFIC

A lightweight inventory, production, procurement, and sales system for bakery,
restaurant, general-production, wholesale, and retail businesses — Django backend and server-rendered frontend in
one codebase, structured as `apps/`-per-domain (borrowed from a larger
multitenant Django project; see `docs/ARCHITECTURE.md` and `CLAUDE.md` for
what was borrowed and what was deliberately left out).

## What it does

- **Inventory** — raw materials (with a real purchase→package→usage unit
  chain: buy in bags, track in kg, dispense in grams/spoons/caps — see
  "Units" below) and finished goods, each with a recipe (bill of materials)
  and an optional batch size.
- **Procurement** — purchase orders; receiving one converts through the
  unit chain and updates stock + cost per unit automatically.
- **Wholesale & retail** — stock-first workspaces procure sellable products
  directly, track every supplier arrival per product, and sell from warehouse
  or shop stock without forcing those products through Production. Their product
  forms omit recipe, batch-yield and base-material controls, while Supporting
  Supplies retain procurement, packaging/conversion, stock and reorder controls.
  Wholesale reuses customer pricing, credit, receivables and finance settlement.
- **Customer Orders & Sales** — one form, one model, split by type:
  - **Walk-in** — immediate, deducts from existing shelf stock, exactly
    like a normal point-of-sale transaction.
  - **Customer order** — saved as *pending*, touches no stock yet. Link
    each order line from a Production Request, then a Production Order;
    completing that order automatically flips the sale to *fulfilled* and
    hands the ordered quantity to the customer — any surplus from batch
    rounding stays as shelf stock. A multi-item order only clears once
    *every* line's production is done.
- **Production requests** — the store asks production to make more of
  something, either as a general restock or linked to a specific pending
  customer order line (which fills in the product and quantity for you).
- **Production orders** — the approval step: completing one deducts raw
  materials by recipe (per batch, rounded up to whole batches — you can't
  make a fraction of a batch) and adds finished stock, inside a database
  transaction, with a shortage warning you can override.
- **Reports** — CSV export for stock, procurement, production, sales, plus
  a full JSON backup.
- **Shared operational alert tray** — one movable, access-aware tray keeps Commerce/Delivery and Inventory attention together without merging their read/snooze state; Inventory has separate Raw Materials and Finished Goods tabs, and both channels can repeat audible reminders until staff acts.
- **Persistent inventory alerts** — separate warning/low conditions for raw materials and finished goods, with tenant-configurable repeat timing, repeating attention sounds, and per-user acknowledgement that never mutates stock.
- **Commerce payments** — Paystack/Monnify, provider-backed instant bank transfer, and a native no-gateway **Transfer** mode that shows the tenant's bank details, requires proof on public/headless checkout, and uses explicit staff confirmation on in-premise POS.
- **Delivery** — plan-gated delivery setup with mapped destination centres, radius/diagonal coverage guides,
  precise-address geocoding with map-pin fallback, in-house riders, true interchangeable Hybrid routing, external
  provider-neutral custom courier adapters plus an optional Founder-gated Glovo LaaS v2 plug-in, with live base-to-destination delivery-fee quoting before payment,
  customer tracking, proof-of-delivery records and delivery timelines across hosted storefront, POS and headless API.
- **Audit Workspace** — plan-gated read-only cross-module evidence review for
  external auditors, with auditor queries/flags and response tracking for
  admins or permitted audit reviewers.
- **In-Premise POS** — a dedicated cashier storefront role can land directly
  in the counter screen and log out without entering the wider workspace; POS
  can also be granted as a supplemental per-user permission without replacing a staff member's primary role.
- Login required on every protected page (Django's built-in auth), with a
  `created_by` trail on every record.
- **Delivery Rider workspace** — rider-only assignments, customer/contact details, allowed status actions, proof of delivery, issue reporting and targeted alerts without normal business-module access.
- **Storefront customer profiles** — optional, tenant-scoped accounts that save contact/address details and purchase history while guest checkout and public tracking remain available.

### Units — the three-layer chain

Raw materials separate **what you buy** (purchase unit — bag, carton),
**what's in the pack** (package qty + unit — e.g. 50 kg), and **what a
recipe actually consumes** (usage unit — kg, g, spoon, cap, with a
conversion factor you set). Stock and cost are tracked internally in the
fine usage unit; the Add/Edit form lets you enter both in the purchase
unit instead (e.g. "3 bags", "₦9,000/bag") and does the conversion for you.

### Product categories and measurement changes

Sellable products can be assigned to business-defined product categories such as
Meals, Drinks, Accessories or Bulk Packs. This category is separate from whether
a product is made in-house or purchased for resale, and it is exposed through the
hosted storefront, in-premise POS and product API.

Raw material measurement edits use a controlled conversion workflow. Current
stock, cost and live recipe/input definitions are converted atomically; completed
historical movements and frozen cost snapshots remain intact so audit evidence is
not rewritten.

### Batches

A finished good can have a batch size (`units_per_batch`) — e.g. 41 loaves
per batch of Family Loaf. Recipe quantities are **per batch**, not per
unit; reorder level stays in individual units. A Production Order's
quantity is still in units (inherited from wherever it came from), but
production always rounds up to whole batches — order 50 loaves at a
41-per-batch size, and 2 batches (82) get made, with the extra 32 landing
as shelf stock.

## Quick start — run it today, locally

Requirements: Python 3.10+

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8001 and log in.

The compiled application stylesheet is committed. When changing Tailwind
utility classes, install Node.js dependencies and refresh it before committing:

```bash
npm install
npm run build:css
```

### WiFi-only access for staff, right now

```bash
python manage.py runserver 0.0.0.0:8001
```

Then on their phone/tablet: `http://<your-computer's-local-IP>:8001`. Only
works while your machine is on and everyone's on the same network — see
"Deploying" below for anywhere-access.

## VSCode + WSL setup (Windows)

To get a real bash terminal inside VSCode on Windows:

1. Open PowerShell **as Administrator** and run:
   ```
   wsl --install
   ```
   This installs WSL2 and Ubuntu by default. Restart when prompted, then
   finish the Ubuntu setup (it'll ask you to create a Linux username/password
   — separate from your Windows login).
2. Install the **WSL** extension in VSCode (search "WSL" in the Extensions
   panel — it's published by Microsoft).
3. Open a WSL terminal (Ubuntu) and clone/place this project somewhere
   inside the Linux filesystem, e.g. `~/projects/inprofic` — not
   `/mnt/c/...` — WSL is noticeably faster and more reliable when the
   project lives inside the Linux filesystem rather than a Windows-mounted
   path.
4. From that WSL terminal: `code .` — this opens VSCode connected to WSL
   (you'll see "WSL: Ubuntu" in the bottom-left corner). VSCode's integrated
   terminal is now a real bash shell in Linux, so the Quick Start commands
   above work exactly as written.
5. Inside that WSL-connected VSCode window, install the Python extension if
   prompted, and point it at `venv/bin/python` once you've created the
   virtualenv (Cmd/Ctrl+Shift+P → "Python: Select Interpreter").

## Git & GitHub

From inside the project folder (in your WSL bash terminal):

```bash
git init
git add .
git commit -m "Initial commit: INPROFIC, apps/-per-domain structure"
```

Then on GitHub: create a new **empty** repository named `INPROFIC` (don't
initialize it with a README/license — you already have one, and that avoids
a merge conflict on first push). Then:

```bash
git remote add origin https://github.com/<your-username>/INPROFIC.git
git branch -M main
git push -u origin main
```

Already have the project under its former repository/directory name? Follow
[the rebrand rename guide](docs/RENAMING.md) to rename GitHub, update `origin`,
move the local folder safely, and recreate the path-bound virtual environment.

`.gitignore` already excludes `venv/`, `db.sqlite3`, and `__pycache__/`, so
your database and virtualenv won't get committed.

## Deploying for real remote access

Plain Django, so it runs on most hosts that support Python.

**PythonAnywhere (free tier)** — persistent storage (your SQLite file
survives restarts, unlike several free hosts that wipe the filesystem on
redeploy), reachable at `yourusername.pythonanywhere.com`. Custom domains and
unrestricted external services require an appropriate paid plan.

Steps: sign up → open a Bash console → clone this repo (or upload it) →
create a virtualenv and `pip install -r requirements.txt` → in the **Web**
tab, add a manually-configured web app pointing its WSGI file at
`storetrack.wsgi.application` → configure `.env.prod` with `SECRET_KEY`,
`DEBUG=False`, `ALLOWED_HOSTS=yourusername.pythonanywhere.com`, and
`CSRF_TRUSTED_ORIGINS=https://yourusername.pythonanywhere.com` → run the shared
deployment command from [the deployment guide](docs/DEPLOYMENT.md) → reload the
web app.

Other options (Render, Railway, Fly.io, a VPS) all run Django fine. On an
ephemeral service, use external PostgreSQL and object storage rather than
SQLite or local uploaded media.

For the supported Render ASGI + Supabase + Cloudflare R2 configuration,
WebSocket notifications, environment variable placement, cron-job.org setup,
and the shared production command, see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Backup & restore

- **Reports & Backup** page → "Download backup (JSON)".
- Restore on a server: `python manage.py loaddata your-backup-file.json`

## Security checklist before going live

- [ ] `SECRET_KEY` set to a fresh random value
- [ ] `DEBUG=False`
- [ ] `ALLOWED_HOSTS` set to your real domain
- [ ] Each staff member has their own login (Django admin → Users → Add user)

## What's next

See `CLAUDE.md` §7 and the docs folder for extension notes. New operational
references include `docs/DELIVERY_GLOVO_PROVIDER.md` and
`docs/AUDIT_WORKSPACE.md`.

### Guided onboarding and technical study guide

New tenant memberships receive a one-time, replayable dashboard tour with module-aware highlights, Previous/Next/Skip/Do-not-show-again controls and extensible animated illustrations. The original animated top artwork remains intact, while a separate lower preview shows a compact representation of the actual module using the active tenant vertical (production-led or stock-first). Existing memberships are not interrupted on rollout; use **Tour INPROFIC** to replay it. Optional per-step GIF/WebP/PNG/MP4/WebM media can replace only that lower preview and lives under `apps/core/static/core/tour/`; see [`docs/ONBOARDING_TOUR.md`](docs/ONBOARDING_TOUR.md).

For a detailed explanation of the stack, tenancy, access control, transaction flows, Commerce, Delivery, performance, deployment, security and common architecture-defense questions, read [`docs/INPROFIC_TECHNICAL_DEFENSE_GUIDE.md`](docs/INPROFIC_TECHNICAL_DEFENSE_GUIDE.md).


### Commerce surface safety

External storefronts/connectors/headless API expose Online and Distribution/Bulk pricing only; POS-only physical-store pricing never appears in those catalogue contracts and external checkout creation rejects that channel. Distribution/Bulk remains available in POS and external commerce with minimum quantity enforcement. Operational Commerce, Inventory and rider alerts use the durable Web Push outbox for background/closed-app reminders and configurable foreground alert tunes.

- **Portion & Bulk Pack selling layer:** keep production in its real base unit while storefronts sell standard portions or vertical-friendly bulk packs; composed-product contents are customer-safe, and finished/procured components can remain independently publishable without exposing raw/packaging inventory.


### Recent platform capabilities

- **Founder product analytics:** first-party registration, login/logout, subscription milestone and throttled module-usage events surfaced in Founder Console without capturing form bodies or secrets.
- **Unified form experience:** shared HTML5/Django validation states, animated contextual SVG field affordances and toggle switches across internal and hosted-storefront forms.
- **Individual/plain selling options:** expose channel-aware add-ons/standalone portions from the same Finished Good stock/recipe, alongside composed Standard Portions and Bulk Packs. External surfaces remain Online + Distribution/Bulk only.

- **Native interactive product demos:** the marketing page includes a service-aware click-through overview, flip-to-preview module cards, and a detailed storefront/POS-to-fulfilment flow without relying on third-party demo hosting.
