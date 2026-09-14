# INPROFIC deployment

INPROFIC supports both deployment shapes without separate settings files:

- PythonAnywhere: SQLite or PostgreSQL, local persistent media, and its Web-tab static mappings.
- Render: Daphne/ASGI, Supabase PostgreSQL, WhiteNoise static files, and Cloudflare R2 media.

The behavior is selected entirely by environment variables. Never commit `.env`, `.env.prod`, database passwords, payment secrets, or R2 credentials.

If PythonAnywhere and Render should be interchangeable front doors to the **same live system**, give both deployments the same Supabase `DATABASE_URL`, R2 settings, and `SECRET_KEY`. If PythonAnywhere keeps SQLite/local media, it remains a separate fallback environment whose data will diverge from Render. PythonAnywhere currently requires a paid account for unrestricted access to an external PostgreSQL service such as Supabase; verify outbound R2 access there as well.

## Render + Supabase + Cloudflare R2

### 1. Supabase database

Create a Supabase project, open **Connect**, and copy the **Session pooler** connection string (port `5432`). [Supabase recommends session mode](https://supabase.com/docs/guides/database/connecting-to-postgres) for persistent backends on IPv4-only networks. Replace the password placeholder with the URL-encoded database password and keep `sslmode=require` in the URL when Supabase provides it.

Use the resulting value only for Render's `DATABASE_URL`. Do not replace the SQLite URL in PythonAnywhere unless you intentionally want PythonAnywhere to use the same Supabase database.

### 2. Cloudflare R2 media

Create an R2 bucket and an API token limited to **Object Read & Write** on that bucket. [Cloudflare's S3 guide](https://developers.cloudflare.com/r2/get-started/s3/) supplies the access-key ID, secret, and endpoint in this form:

```text
https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

For public storefront images, either attach a custom public domain such as `media.example.com`, or leave `R2_CUSTOM_DOMAIN` empty and INPROFIC will generate signed R2 URLs. A custom domain must be entered without `https://` in Render. If R2 is not desired on PythonAnywhere, leave all four required R2 credential variables empty and the existing local `MEDIA_ROOT` behavior remains active.

Existing database image fields contain object names, not image bytes. When moving an existing deployment to R2, copy the contents of the current local `media/` directory into the bucket's `media/` prefix while preserving all subdirectories.

### 3. Create the Render service

Push the repository, then in Render choose **Blueprints → New Blueprint Instance** and select this repository. Render reads `render.yaml` and creates a free Python web service.

The Blueprint already supplies the build and start commands:

```text
Build: ./scripts/production.sh build
Start: ./scripts/production.sh serve
Health check: /health/
```

The start command applies migrations and then starts Daphne against `storetrack.asgi:application`. HTTP and WebSocket traffic therefore use the same Render service. Migrations remain in startup because Render's pre-deploy command is a paid-service feature. Recurring jobs run only through the authenticated maintenance endpoint, so they do not delay every service restart.

Commerce notifications are durable database records. A WebSocket signal tells
connected, authorized staff browsers to fetch their tenant-scoped unread feed
immediately; automatic HTTP polling remains active only when the socket is
unavailable. Daphne sends keepalive pings and the browser reconnects with capped
exponential backoff after deploys, restarts, and network interruptions.

The free Render starter configuration runs one Daphne process and uses an
in-memory channel layer, so it requires no additional service. This is safe for
INPROFIC's starter mode because the database notification is authoritative and
polling recovers any missed transient signal. The Channels project recommends
Redis for production channel layers; configure it before adding processes or
instances.

If the web service is later scaled to multiple processes or instances, provision
a Redis-compatible service and set this environment variable on every instance:

```text
CHANNEL_REDIS_URL=rediss://<username>:<password>@<host>:<port>/0
```

When present, INPROFIC automatically switches to the Redis channel layer. Never
use the in-memory layer across multiple processes because messages cannot cross
process boundaries.

Enter the following secret values during the first Blueprint setup. For an existing Blueprint, add new `sync: false` values manually in **Service → Environment** because Render does not prompt again.

| Render variable         | Where the value comes from                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------- |
| `DATABASE_URL`          | Supabase Connect → Session pooler URI, including the password                               |
| `DB_CONN_MAX_AGE`       | `0` for ASGI; supplied by `render.yaml`                                                     |
| `DB_POOL_MAX_SIZE`      | `2`; keeps this web service's persistent pool small against Supabase's session-client limit |
| `DB_POOL_TIMEOUT`       | `10`; seconds to wait for a pooled connection                                               |
| `R2_ACCESS_KEY_ID`      | Cloudflare R2 API token result                                                              |
| `R2_SECRET_ACCESS_KEY`  | Cloudflare R2 API token result                                                              |
| `R2_BUCKET_NAME`        | The exact R2 bucket name                                                                    |
| `R2_ENDPOINT_URL`       | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`                                             |
| `R2_CUSTOM_DOMAIN`      | Optional public media domain without a scheme; leave blank for signed URLs                  |
| `PAYSTACK_PUBLIC_KEY`   | Paystack dashboard; optional until enabled                                                  |
| `PAYSTACK_SECRET_KEY`   | Paystack dashboard; secret                                                                  |
| `MONNIFY_API_KEY`       | Monnify dashboard; optional until enabled                                                   |
| `MONNIFY_SECRET_KEY`    | Monnify dashboard; secret                                                                   |
| `MONNIFY_CONTRACT_CODE` | Monnify dashboard                                                                           |

`SECRET_KEY` and `CRON_SECRET` are generated by Render. Copy the generated
`CRON_SECRET` from Render into the maintenance job's bearer header. If Render
and PythonAnywhere share one database and must also share login sessions,
replace Render's generated `SECRET_KEY` with the exact same strong value used
by PythonAnywhere. `RENDER_EXTERNAL_HOSTNAME` is supplied by Render and
INPROFIC automatically trusts that hostname and its HTTPS CSRF origin.

When adding a custom application domain, manually add:

```text
ALLOWED_HOSTS=your-domain.example,www.your-domain.example
CSRF_TRUSTED_ORIGINS=https://your-domain.example,https://www.your-domain.example
```

These two variables contain hostnames/origins only; they are not secrets.

The Render process uses Django's Psycopg pool with at most two open database
sessions. Keep `DB_CONN_MAX_AGE=0`; Django persistent connections and the
application pool must not be enabled together. The deliberately small pool
leaves more of Supabase's session-mode client allowance available during
deploy overlap, migrations, scheduled maintenance, and administrative access.

Local development, PythonAnywhere SQLite, and Render Supabase are separate
databases unless deliberately configured with the same `DATABASE_URL`. Users,
businesses, plan pricing, and founder lifetime grants created in local SQLite
do not automatically appear in Supabase. Create/grant them again on Render, or
perform an intentional data migration.

### 4. cron-job.org

[Render currently spins down](https://render.com/docs/free) a free web service after 15 minutes without inbound traffic. Create these jobs at cron-job.org:

1. **Keep awake** — `GET https://<service>.onrender.com/health/` every 10 minutes.
2. **INPROFIC maintenance** — `POST https://<service>.onrender.com/ops/run-jobs/` once daily, with the request header `Authorization: Bearer <CRON_SECRET>`.

The health endpoint performs no database query. The maintenance endpoint rejects requests when `CRON_SECRET` is missing or incorrect. [cron-job.org supports custom methods and headers](https://cron-job.org/en/faq/), and its execution history should show HTTP 200 responses. Keep in mind that an always-awake service consumes nearly all of Render's 750 free instance hours in a typical month, and free services remain unsuitable for business-critical production.

### 5. Commands in one place

All operational entry points are in `scripts/production.sh`:

```bash
./scripts/production.sh build    # dependencies, collectstatic, Django check
./scripts/production.sh jobs     # every registered scheduled job
./scripts/production.sh release  # apply migrations
./scripts/production.sh serve    # migrate, then start Daphne/ASGI
./scripts/production.sh deploy   # build and release together
```

Future idempotent recurring commands belong in `apps/core/jobs.py`. Both `python manage.py run_scheduled_jobs` and the authenticated HTTP endpoint use that same registry.

Tailwind is compiled into `apps/core/static/core/css/inprofic.css` and committed.
The app shell, login, signup, and public storefront pages all load that local
stylesheet rather than the Tailwind browser CDN, and their bundled brand fonts
come from `apps/core/static/core/fonts/`. After adding or changing Tailwind
utility classes in templates or Python form widgets, rebuild and commit the
stylesheet:

```bash
npm install
npm run build:css
```

## PythonAnywhere remains supported

Keep `.env.prod` on PythonAnywhere and retain the existing WSGI file. PythonAnywhere's normal Web tab remains HTTP-only, so the notification widget automatically falls back to polling there. Typical values are:

```text
DEBUG=False
ALLOWED_HOSTS=<username>.pythonanywhere.com
CSRF_TRUSTED_ORIGINS=https://<username>.pythonanywhere.com
DATABASE_URL=sqlite:////home/<username>/inprofic/db.sqlite3
MEDIA_URL=/media/
MEDIA_ROOT=/home/<username>/inprofic/media
```

In the PythonAnywhere Web tab, keep the static mappings:

```text
/static/  -> /home/<username>/inprofic/staticfiles
/media/   -> /home/<username>/inprofic/media
```

After each code update, run:

```bash
source venv/bin/activate
./scripts/production.sh deploy
```

Then reload the PythonAnywhere web app. If you enable R2 there later, remove the `/media/` static mapping after verifying uploads and public image URLs through R2.

To use PythonAnywhere as a live failover for Render rather than as a separate copy, use the same Supabase and R2 variables there instead of the SQLite/local-media values above. This requires PythonAnywhere outbound connectivity and careful coordination of payment callback URLs; use one canonical public domain when possible.

## Moving existing SQLite data to Supabase

Changing `DATABASE_URL` starts against a different database; it does not copy the existing SQLite records. Before accepting live traffic on Render, export the current database with `dumpdata` (excluding Django content types and permissions if appropriate), run migrations against Supabase, load the reviewed fixture, and compare tenant, stock, sales, finance, and order counts. Keep the old database as a rollback backup until the new deployment is verified.

### Web Push (Commerce background notifications)

INPROFIC keeps the existing WebSocket/in-app commerce notification path for
open pages and adds Web Push for installed/background PWAs. Push delivery is
stored in a durable outbox so checkout/payment requests never wait for the
browser push provider.

Generate the VAPID pair **once** from a trusted local/project shell after
installing the current requirements:

```bash
python manage.py generate_vapid_keys
```

Copy the three printed values into the Render service Environment:

```text
WEB_PUSH_VAPID_PUBLIC_KEY=...
WEB_PUSH_VAPID_PRIVATE_KEY=...
WEB_PUSH_VAPID_SUBJECT=mailto:your-admin-email@example.com
```

Keep the private key stable and secret. Rotating the pair invalidates existing
browser subscriptions and users must enable background alerts again.

Immediate delivery is best-effort in a short daemon thread after the commerce
transaction commits. For durable retries, create an additional cron-job.org
job every 5 minutes:

```text
POST https://<production-domain>/ops/dispatch-web-push/
Authorization: Bearer <CRON_SECRET>
```

The endpoint processes a bounded batch so it cannot monopolize the free Render
instance. The existing daily `/ops/run-jobs/` maintenance schedule remains
unchanged and does not run push delivery work.

Users enable/disable Web Push per device from **Commerce settings → Browser &
PWA alerts on this device**. Signing out deactivates that account's server-side
push subscriptions for privacy; the user can explicitly enable the device again
on a later session.

### Tenant backups and Founder Console backup restore

Reports → Backup is explicitly tenant-scoped. The exporter includes only the
active `Business` and operational rows proven to belong to it; child models
without their own `business_id` (for example recipe, order and sale items) are
scoped through their owning parent. Future backups also include the customer,
inventory-location and production dependency rows needed for a self-contained
operational restore.

The Founder Console recovery tool accepts either an INPROFIC Django JSON backup
or a raw PythonAnywhere SQLite database and never replaces the destination
Supabase/PostgreSQL database. Use **Dry run / compare only** first.

For JSON backups, the dry run re-establishes tenant ownership from the selected
Business and parent relations. This safely handles historical backups produced
before tenant-scoping was fixed: other tenant roots/rows in the same file are
ignored. Historical JSON exports that omitted customer masters or inventory
locations are repaired conservatively from customer-name snapshots and the
destination tenant's Main Store; nullable production-batch traceability links
that cannot be reconstructed are left empty and reported rather than guessed.

For SQLite backups, the dry run opens the database in query-only mode and runs
an integrity check. Both formats:

- list source tenants and require a source tenant ID when the backup has more than one;
- compare source values/fields with the current Django model schema;
- block when a current required field cannot be satisfied;
- block imports into a destination tenant that already contains operational data;
- preflight globally unique scalar values against the live database without displaying sensitive values;
- preserve the destination tenant's subscription/entitlement boundary.

The real import requires re-uploading the same backup and typing
`IMPORT <destination-slug>`. The importer repeats the dry run, maps backup record IDs
to new PostgreSQL IDs, and performs all writes plus a Founder audit entry inside
one database transaction. SQLite identity rows can be merged by username/email;
historical JSON backups that did not include user records clear nullable creator
attribution rather than guessing identities. Any import error rolls the whole
tenant merge back. Uploaded backup files are temporary and are not retained by
INPROFIC.

### Commerce gateway settlement, instant transfer, and in-premise POS

Public/headless commerce is payment-first. New checkouts expose only fully
configured gateway-confirmed methods: Paystack hosted checkout, Monnify hosted
checkout, and Instant bank transfer. Cash and physical POS are never returned
to public/headless clients.

Each tenant configures its own credentials and Finance settlement accounts under
**Commerce → Payment settings**. When Instant bank transfer is enabled, choose
Paystack or Monnify as the transfer provider. Monnify custom account display
also requires the bank code supported by that merchant account. The customer is
shown the provider-issued temporary account; there is no manual transfer-claim
step for new payments.

Configure each tenant merchant account to send gateway webhooks directly to:

```text
https://<production-domain>/api/v1/storefronts/<business-slug>/payments/paystack/webhook
https://<production-domain>/api/v1/storefronts/<business-slug>/payments/monnify/webhook
```

A signed webhook is only a trigger. INPROFIC independently verifies the tenant,
reference, expected amount/currency and payment metadata with the provider
before it posts Finance, generates the customer receipt, or materializes the
checkout into an operational commerce intake/order. Browser redirects never
settle a payment.

For walk-in sales, grant the staff member the supplemental **Commerce storefront
access** capability from User Management. This does not replace their primary
role or grant normal Commerce administration permissions. The in-premise POS
surface can accept configured cash after the staff member explicitly confirms
physical receipt. Card-on-terminal requires Paystack to be enabled plus a
Paystack Terminal ID, fallback walk-in email, and a tenant Finance settlement
account. Terminal settlement still waits for provider verification.

### PWA deployment updates

`PWA_BUILD_VERSION` defaults to Render's `RENDER_GIT_COMMIT`. A new deployment
therefore changes the service-worker script automatically. Installed clients
detect the waiting worker and show the controlled **Update app / Later** prompt;
the app is not force-refreshed while a user is working. If deploying somewhere
without `RENDER_GIT_COMMIT`, set `PWA_BUILD_VERSION` to a new release/build ID
for every PWA deployment so installed clients can detect it.
