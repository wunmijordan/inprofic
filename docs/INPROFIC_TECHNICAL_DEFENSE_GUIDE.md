# INPROFIC Technical Defense Guide

This document is a study guide for explaining, defending, maintaining, and extending INPROFIC. It is deliberately more detailed than the product overview: the goal is to explain **what the system is, which technologies it uses, why the architecture is shaped this way, where business truth lives, and how the major workflows connect without sacrificing tenant isolation or auditability**.

It should be read together with `ARCHITECTURE.md` for the concise engineering map, `COMMERCE_INTEGRATION.md` for the commerce/API contract, `DELIVERY_GLOVO_PROVIDER.md` for provider integration, `AUDIT_WORKSPACE.md` for auditor isolation, `SUBSCRIPTIONS.md` for commercial entitlement rules, and `DEPLOYMENT.md` for environment/deployment operations.

---

## 1. The shortest defensible description

**INPROFIC is a multi-tenant, production-aware business operating system built with Django. It connects procurement, inventory, production/preparation, sales, commerce, payments, delivery, finance, audit, users, and reporting around one tenant-scoped source of operational truth.**

That wording is important. INPROFIC is not merely an inventory tracker, a point-of-sale screen, an online store, or an accounting page. Its value comes from connecting those concerns while preserving the boundaries between them.

Examples of those boundaries:

- a purchase order is not the same record as a supplier payment;
- having a receivable is not the same thing as having received cash;
- a paid checkout is not the same thing as a delivered order;
- an external courier status is not allowed to rewrite stock or finance directly;
- a product category is not the same thing as whether a product is manufactured or purchased for resale;
- a current unit conversion is not allowed to rewrite completed historical movements;
- an auditor query is not allowed to mutate the record being queried;
- a storefront customer account is separate from a staff user account;
- a role permission cannot exceed the module ceiling purchased by the tenant.

Those separations are some of the strongest architectural arguments to make when defending the build.

---

## 2. Core technology stack

### Application framework: Django

INPROFIC uses Django 5.x as the main web framework. Django provides:

- URL routing;
- request/response handling;
- server-rendered templates;
- forms and validation;
- ORM/database abstraction;
- authentication and sessions;
- migrations;
- CSRF protection;
- security middleware;
- test infrastructure.

Django is a sensible fit because INPROFIC is primarily a data-rich business application with many strongly related transactional models, authenticated workflows, forms, permissions, exports, and tenant-specific business rules. The ORM and migration system are particularly valuable because business consistency matters more than building a purely client-side application shell.

### ASGI and realtime delivery: Channels + Daphne

The deployment entry point is ASGI. `channels[daphne]` provides the ASGI server and realtime/WebSocket support. Open browser sessions can therefore receive commerce/delivery notification updates without polling every screen continuously.

For one application process, Django Channels can use the in-memory channel layer. When INPROFIC is scaled to multiple application processes or multiple Render instances, `CHANNEL_REDIS_URL` must be configured so Channels uses Redis and messages can cross process boundaries. This is an important scaling invariant: adding more web instances without a shared channel layer would make realtime delivery inconsistent between workers.

### Database

INPROFIC supports two deliberate database modes:

- **SQLite** for local development and supported small PythonAnywhere-style deployments;
- **PostgreSQL** for the Render/Supabase production flow.

PostgreSQL access uses `psycopg` and supports an application connection pool. On pooled PostgreSQL deployments, `DB_CONN_MAX_AGE` remains zero because a Psycopg pool already owns connection reuse; enabling both Django persistent connections and the Psycopg pool would create competing connection-lifetime strategies.

### Static and media storage

Static application assets are collected and served with WhiteNoise in the normal Render flow. Uploaded media can live locally or use an S3-compatible backend through `django-storages`; the production documentation supports Cloudflare R2.

This separation matters:

- static assets are application code/build artifacts;
- media assets are tenant/user uploaded data and must survive application redeploys.

### Styling and browser behavior

The application uses server-rendered Django templates with a compiled Tailwind-based design system plus focused vanilla JavaScript and Alpine.js where small reactive interactions are valuable. Chart.js is used for dashboard chart surfaces.

The guiding principle is progressive enhancement: business rules remain server-side and authoritative even where JavaScript improves speed or clarity. A browser-calculated amount, delivery quote display, product price preview, or form helper does not replace the server-side validation that commits the transaction.

### Supporting libraries

The current Python requirements also include:

- `dj-database-url` for environment-driven database configuration;
- `openpyxl` for Excel exports;
- `reportlab` for PDF/document output where used;
- `Pillow` for image handling;
- `pywebpush` for background web-push notifications;
- `python-dotenv` for environment loading in supported development/deployment flows.

---

## 3. Repository and application structure

The project package is `storetrack/`. Domain code lives under `apps/`, while shared HTML templates live under `templates/`.

The main runtime apps are:

### `accounts`

Owns identity and access/commercial-control concepts:

- `CustomUser`;
- tenant memberships (`UserBusiness`);
- roles;
- role module permissions;
- per-user permission overrides;
- business module access ceilings;
- subscription plans and plan-module entitlements;
- promotions and marketing campaigns;
- subscription payments/payment settings;
- founder-lifetime grants;
- paid-trial credential claims.

### `core`

Owns cross-domain platform concepts:

- `Business` tenant root;
- business-scoped base models/managers;
- tenant-routing middleware;
- dashboard and global search;
- cash accounts and the financial transaction ledger;
- audit log and audit queries;
- reports, backup/export surfaces;
- scheduled operational endpoints/jobs;
- performance diagnostic middleware;
- vertical/service configuration;
- onboarding tour state/version behavior.

### `inventory`

Owns:

- raw materials;
- finished goods;
- product categories;
- recipes;
- production materials/packaging inputs;
- stock adjustments/movements;
- inventory locations;
- operational supply dispensing;
- Distribution Market stock lots/movements/returns;
- measurement-change audit records;
- channel selling prices.

### `procurement`

Owns:

- purchase orders;
- purchase order lines;
- historical raw-material procurement cost snapshots;
- supplier payments.

### `production`

Owns:

- production/customer/physical-store orders;
- per-business production order numbering;
- order lines;
- material usage snapshots;
- shared production runs;
- production batches;
- wastage/offcut reconciliation;
- quality checks;
- frozen cost snapshots and cost lines.

### `sales`

Owns:

- reusable customer master records;
- customer/product/channel prices;
- sales;
- sale lines;
- customer payments.

### `expenses`

Owns operating expenses and expense-payment records.

### `commerce`

Owns the public/staff digital commerce boundary:

- commerce configuration/integrations;
- storefront product publication;
- normalized commerce intake/order records;
- checkout sessions/items;
- provider payment configurations and claims;
- immutable payment receipts/allocations/gateway events;
- commerce notifications and notification reads;
- web-push subscriptions/outbox records;
- optional tenant storefront customer accounts;
- delivery configuration, zones, providers, drivers, quotes, assignments, events and issues;
- provider-neutral custom courier adapters plus the optional Founder-gated Glovo LaaS v2 adapter;
- hosted storefront, in-premise POS and headless API flows.

This app division keeps domain responsibilities readable without pretending they are independent systems. They intentionally connect through service-layer functions and explicit foreign keys.

---

## 4. Request lifecycle and tenant routing

A normal request passes through standard Django security/session/authentication middleware plus INPROFIC-specific middleware.

The essential order is:

1. security/static middleware;
2. request performance diagnostics;
3. session handling;
4. common Django request processing;
5. CSRF protection;
6. authentication;
7. `BusinessMiddleware` tenant selection;
8. login/access routing;
9. view execution;
10. template rendering/response.

`BusinessMiddleware` resolves the active tenant and attaches it to `request.business`. Business-aware views and permission helpers then use that one active tenant context.

### Why tenant context is server-side

The browser must not be trusted to decide tenant ownership merely by posting a `business_id`. Tenant-aware queries are scoped from the authenticated membership plus `request.business`. Sensitive object lookup therefore follows the pattern:

```text
request.user + request.business + permission boundary + tenant-filtered queryset
```

rather than:

```text
object id supplied by browser → unrestricted global lookup
```

That distinction prevents insecure direct object reference across tenants.

### Domain/path behavior

INPROFIC supports a shared-domain application/marketing model while retaining tenant-aware storefront/business routes and optional domain/subdomain deployment strategies documented elsewhere. Authenticated users normally continue into their remembered workspace, while anonymous root traffic sees the marketing surface.

---

## 5. Multi-tenancy and data isolation

`Business` is the tenant root. Most operational records either carry a direct `business` foreign key or inherit their ownership through a parent that is itself business-owned.

INPROFIC uses several complementary mechanisms:

1. **Tenant-aware model patterns/managers** for business-owned models.
2. **Request-level tenant resolution** through `request.business`.
3. **Permission helpers** that always receive both user and business.
4. **Explicit tenant filtering** in operational views and services.
5. **Cross-tenant tests** for sensitive surfaces such as user editing, delivery assignments, audit records, storefront customer identity and business switching.
6. **Tenant-scoped exports/backups** that traverse only rows owned by the selected tenant.

### Staff identity versus tenant membership

A `CustomUser` can belong to more than one business through `UserBusiness`. Role and permission decisions are therefore membership-specific rather than attached globally to the user.

This allows the same authenticated person to be, for example, an Administrator in one tenant and a Manager in another without mixing permissions.

### Customer identity is separate

`StorefrontCustomer` is not a staff `CustomUser`. Storefront customer identity is scoped to the tenant and exists only to give a shopper optional profile/history convenience. Guest checkout remains valid. This avoids giving public customers access to staff authentication surfaces and allows the same email to maintain unrelated profiles with two different businesses.

---

## 6. Permission architecture: commercial ceiling first, role permission second

INPROFIC deliberately separates **what the tenant has purchased/enabled** from **what a particular employee may do**.

### Layer 1: plan/module entitlement

`BusinessModuleAccess` is the runtime commercial ceiling. A plan or Founder grant can enable/disable modules for a business. If the business does not have a module, a staff role cannot grant itself that module.

Conceptually:

```text
Effective permission = tenant module entitlement AND staff role/user permission
```

### Layer 2: role permissions

`RoleModulePermission` defines ordinary View/Edit capabilities for a role in a business.

### Layer 3: per-user override

`UserModulePermission` can make selected adjustments for one membership without redefining the underlying role.

### Purpose-built roles

Some workspaces intentionally stay narrow:

- POS Operator can go directly to in-premise POS;
- Delivery Rider sees only assigned deliveries;
- External Auditor uses Audit Workspace without normal operational edit access;
- Live Tester can navigate broadly but unsafe methods are server-blocked/read-only.

These roles demonstrate least-privilege design: users are given the workspace needed for their job rather than the entire ERP-style navigation tree.

---

## 7. Subscription architecture and the Starter free/paid switch

Subscription plans define commercial module ceilings, pricing and capacity. Plan entitlement rows are kept separate from role rows so commercial packaging can change without redefining staff roles.

### Starter invariant

Starter always retains its small capacity profile:

- one user;
- no additional service profile;
- Founder-configured module matrix.

Its **commercial mode** is now safely Founder-controlled:

- when Starter monthly price is zero, `SubscriptionPlan.is_free_forever` is true;
- free Starter is active without an expiry/payment requirement;
- when the Founder switches Starter to paid, it must have a positive monthly price;
- existing active free Starter subscribers receive the Founder-configured transition/trial instead of being cut off immediately;
- new workspaces created while Starter is paid begin on the Starter trial;
- switching Starter back to free turns non-founder Starter subscriptions back to active, non-expiring access;
- founder-lifetime grants remain protected from these mass transitions.

No second “free” database flag is stored. The free state is derived from Starter's persisted price, which reduces the risk of a boolean and a price disagreeing.

### Why this is safer than changing price only

If the Founder merely changed Starter from ₦0 to ₦X without transitioning existing subscriptions, previously free tenants would suddenly fail effective-access checks. The transition service updates subscription status/expiry coherently inside a database transaction so existing access is not abruptly revoked.

---

## 8. Inventory and measurement design

Raw materials are represented in a way that separates how a business **buys** something from how it **uses** it.

Example:

```text
Purchase unit: bag
Package quantity/unit: 50 kg
Usage unit: g
Usage conversion: 1000

1 bag = 50 kg = 50,000 g
```

INPROFIC stores/uses the fine operational quantity needed by recipes and stock deductions, while the form lets staff count stock and cost in natural purchase units.

### Why the converter is a helper rather than business truth

The raw-material form includes a unit/conversion helper, but the calculator is not allowed to invent a physical relationship. Standard conversions can be calculated automatically. Non-standard relationships such as “spoons per kilogram” or density-dependent mass/volume conversions require an explicitly measured ratio/density.

The current UI keeps the conceptual help as a hidden diagrammatic accordion and opens the detailed conversion/calculation utility in a modal so the primary material form stays readable.

### Controlled measurement changes

Changing a live material's measurement definition is dangerous because a naive update can relabel stock and recipes without changing the number, corrupting meaning.

`RawMaterialMeasurementChange` and the corresponding workflow require confirmation/reason, convert live operational quantities where required, and leave completed historical evidence frozen. This establishes an auditable boundary between the old and new measurement definitions.

---

## 9. Procurement and supplier truth

A purchase order represents an acquisition commitment/receipt workflow. Receiving is the moment inventory is updated.

Important separations:

- PO line cost tells INPROFIC what was acquired and at what price;
- receiving changes stock and creates the relevant procurement-cost evidence;
- supplier payment records tell Finance what cash has actually been paid;
- payable state is therefore not confused with cash state.

Historical cost snapshots allow production costing to explain the material price applicable at the relevant time rather than recalculating old production using today's supplier price.

---

## 10. Production model

Production combines planning, controlled stock release and completion evidence.

### Recipes/BOM-style definitions

Finished goods can have recipe items and other production materials such as packaging. Requirements are calculated proportionally to actual planned units rather than assuming every order uses an entire standard batch.

### Order lifecycle

A production order can represent different commercial channels while retaining product/customer/pricing snapshots. Approval performs the material-availability/release step. Completion records actual output.

### Production batches

Completion creates production batch evidence including:

- planned units;
- gross units produced;
- wastage/rejected units;
- saleable output;
- yield;
- batch/expiry information;
- quality evidence;
- frozen material cost evidence.

The architecture does not assume planned quantity equals actual output. That is essential for production integrity.

### Shared Production Runs

`ProductionRun` coordinates multiple ordinary orders into one manufacturing exercise while retaining each order's customer/channel/commercial identity. The run aggregates the exact proportional material requirements of its member order lines. It does not duplicate the recipe engine and does not pretend each member order requires a full batch.

---

## 11. Sales, customers, receivables and finance

`Customer` is reusable master data. Completed commercial records retain snapshots of important customer information so editing a customer later does not rewrite historical evidence.

### Price resolution

Where applicable, price resolution follows a hierarchy such as:

```text
Customer + Product + Channel agreement
        ↓ if unavailable
Product + Channel price
        ↓ if unavailable
FinishedGood default selling price
```

The resolved price is snapshotted into the line at transaction time.

### Finance truth

INPROFIC separates:

- revenue/commercial obligation;
- receivable/payable balances;
- actual cash movement.

`CashAccount` and `FinancialTransaction` provide the money-movement layer. Payment models in Sales, Procurement and Expenses bridge operational records into that financial ledger.

A key defense point is: **the system does not treat “an order exists” as “cash was received.”**

---

## 12. Commerce architecture

Commerce is the boundary between customer/staff checkout channels and internal operations.

Supported entry points include:

- hosted tenant storefront;
- Order Now links;
- in-premise POS;
- tenant-owned headless website/app through the API;
- normalized connector intake.

These channels converge on shared authoritative services so product publication, price, quantity, payment verification and delivery rules do not diverge by frontend.

### Payment-first public flow

The central sequence is:

```text
Catalogue/basket
   ↓
Server validates publication + quantity + price
   ↓
Optional authoritative delivery quote
   ↓
Checkout snapshot
   ↓
Payment provider/manual trusted confirmation
   ↓
Verified payment
   ↓
Operational order/sale materialized exactly once
   ↓
Inventory/production/delivery handoff
```

A browser redirect is not payment proof. Gateway callbacks/webhooks are verified server-side. Idempotent payment/order materialization prevents duplicate operational records when a provider sends repeated notifications.

### Why there are several commerce models

The system keeps distinct models for checkout, payment attempt/claim, immutable receipt/allocation, gateway event, normalized intake and final operational materialization because each answers a different question:

- What did the shopper intend to buy?
- What price/delivery fee was accepted?
- Which provider payment was attempted?
- Which payment was authoritatively verified?
- How was that payment allocated?
- Was the order materialized already?
- What external/provider events were received?

Collapsing these into one mutable “Order” row would make payment reconciliation and audit much weaker.

---

## 13. Hosted storefront, optional customer account and headless API

### Hosted storefront

The hosted storefront uses the same Commerce services as the API. Basket totals are not trusted merely because JavaScript displays them.

The final secure review carries the accepted delivery snapshot so the customer can see provider/method, distance, ETA, delivery area/address, validation source, delivery fee, Hybrid policy and final amount before payment.

### Optional tenant customer profiles

Registration is optional. A guest can still browse, pay and track. A registered customer gets convenience/history within that one tenant.

### Headless websites

The Headless API is designed so a business-owned website can implement its own frontend while INPROFIC remains the authoritative commercial backend.

The external site should:

1. get published product/category data;
2. use the delivery-location synchronization endpoint for address ↔ pin behavior;
3. request an authoritative delivery quote;
4. show the returned provider/distance/ETA/destination/policy in the final review;
5. create checkout using the accepted quote id;
6. redirect/use the configured payment flow;
7. query checkout/order/tracking endpoints after confirmation.

API keys must stay server-side. Browser JavaScript should call the external website's own backend/serverless proxy, which then calls INPROFIC with the secret integration key.

---

## 14. Delivery architecture

Delivery is provider-neutral and plan-gated.

### Configuration layers

- `DeliveryOrigin`: dispatch/office base;
- `DeliveryArea`: named destination area with centre, radius and optional diagonal extensions;
- `DeliveryRateBand`: fee/ETA/minimum-order rules;
- `DeliveryProviderAccount`: external provider configuration;
- `DeliveryDriver`: in-house or manual provider driver;
- `DeliverySettings`: tenant routing/switching policy.

### Geometry

For a mapped named area:

- the area radius/extensions answer **whether the destination can be served**;
- the actual base-to-customer distance answers **what distance is priced**.

The graphical editor lets staff move the centre, radius and diagonal extensions. Checkout maps draw those shapes as customer guidance.

### Address ↔ map synchronization

INPROFIC exposes a shared location-resolution service used by hosted storefront, POS and Headless API:

- typed address → geocode → precise map pin/zoom;
- map pin → reverse geocode → address text;
- either result is checked against selected tenant coverage;
- if typed address cannot be located, the user can correct it or use a pin;
- manual pin does not bypass coverage rules.

Geocoding candidates can be cached. Coverage remains an INPROFIC decision, not a third-party geocoder decision.

### In-house / external / Hybrid

A delivery assignment resolves to a real execution method:

- in-house;
- configured external delivery partner (including the optional Glovo adapter when Founder-enabled).

Hybrid means both are available rather than “one named courier with an emergency fallback.” Routing policies can include customer choice, dispatcher choice, lowest fee, fastest ETA, in-house first and provider first.

The tenant also controls post-payment switching rules. The customer-facing switch policy is snapshotted and shown before payment so a later administrative settings change does not silently alter the promise accepted by the customer.

### Rider workspace

A Delivery Rider is a dedicated staff access profile tied to an active driver. The lightweight dashboard only queries assignments for that rider/business. The rider can progress allowed statuses, supply POD information and lodge issues/complaints. Dispatcher/authorized staff respond through the operational delivery dashboard.

### Glovo adapter

Glovo LaaS v2 is a Founder-gated optional provider adapter. Tenant credentials remain tenant-owned, the tenant connection stays inactive until configured, and disabling platform availability preserves saved configuration while blocking runtime execution. Live quote, paid-order parcel dispatch, tracking-link retrieval, cancellation and webhook status sync attach to INPROFIC's own delivery quote/assignment/event model.

Provider callbacks update delivery state/timeline; they do not directly mutate stock, production or finance.

---

## 15. Notifications, realtime and Web Push

Commerce/Delivery notifications are durable database records. That matters because a realtime socket is transient: if a browser is closed, the event still needs to exist when the user returns.

The stack is layered:

1. durable notification row;
2. WebSocket/live update when an eligible page is open;
3. optional Web Push/background notification;
4. notification read state per user.

Delivery alerts can be targeted to the assigned rider while broader operational events are visible to staff with the relevant module access.

This is safer than relying on an ephemeral JavaScript toast as the only record of a critical action.

---

## 16. Audit Workspace

Audit Workspace is not a shortcut that grants an auditor every module.

A dedicated server-side audit surface gathers tenant-scoped evidence from inventory, procurement, production, sales, payments, expenses, financial transactions, measurement changes, delivery and the audit log.

The External Auditor role receives Audit Workspace view access without ordinary module editing. Audit queries store a reference to the evidence row plus question/status; they do not mutate the underlying record. Operational users can respond/close only if they have the explicit audit reviewer/edit permission.

This architecture supports an important audit principle: **review evidence without granting the reviewer the power to rewrite that evidence.**

---

## 17. Onboarding tour framework

New tenant memberships begin with onboarding tour version `0`. The current tour version is centrally declared in `core/onboarding.py`.

Existing memberships are marked as already introduced when the feature migration is first applied, so deploying the tour does not interrupt every existing employee. They can replay it manually from **Tour INPROFIC** in the workspace navigation.

The dashboard tour provides:

- Previous;
- Next;
- Skip (session-only);
- Do not show again (persisted to membership);
- Finish;
- focus/highlight positioning around visible workspace targets;
- animated mini flow diagrams;
- keyboard navigation;
- reduced-motion support.

The browser definition for each step includes a `media` slot. Future illustrations can therefore be switched from the built-in CSS animation to an image/GIF/WebP or looping MP4/WebM without changing the tour persistence model or endpoint.

The completion endpoint writes one small version integer. Normal navigation does not add a separate tour database query: the context processor reuses the already-cached membership/permission snapshot.

---

## 18. Performance design

Performance work in INPROFIC happens at several layers.

### Query shaping

Complex dashboard/list views use `select_related`, `prefetch_related`, aggregates and bulk operations where appropriate to avoid per-row N+1 query patterns.

### Request-scoped caches

Permission snapshots, business lists and selected entitlement data are reused inside the same request so context processors and views do not repeatedly ask the database the same question.

### Subscription/Founder bulk updates

Plan entitlement/pricing changes are designed to load the affected rows in bounded sets and use bulk create/update rather than hundreds of `get_or_create`/`save` calls inside loops.

### PostgreSQL connection pooling

The Render/Supabase path supports Psycopg pooling. This helps avoid paying for connection setup on each request while preserving bounded database sessions.

### Cached database sessions

Production can use cached DB sessions to reduce avoidable session-store database traffic.

### Deferred/lazy expensive UI

Dashboard financial breakdown and similar detail can be loaded separately rather than forcing every overview navigation to compute every deep analytical surface immediately.

### Performance diagnostics

`PerformanceDiagnosticMiddleware` can log total request time, SQL time and query count. This is what makes it possible to distinguish:

- database round-trip latency;
- N+1 queries;
- Python/template cost;
- non-SQL middleware/network delay.

### What a larger Render plan can and cannot solve

A larger instance can improve CPU availability, memory pressure, thread contention and cold-start behavior. It cannot automatically remove:

- 20–30 ms of network latency per database round trip;
- a slow external API/geocoder/payment provider;
- an inefficient query loop;
- a database in a distant region;
- single-process realtime limitations.

Therefore “paid compute” is an accelerator, not a substitute for application/database locality and query discipline.

For horizontal/multi-process scaling, configure Redis for Channels first. Keep PostgreSQL/Supabase geographically close to the Render service and size the database pool to the actual database connection budget.

No honest architecture can promise “completely lag free” under every network/load condition. The defensible goal is bounded queries, no avoidable synchronous external work, co-located infrastructure, measured slow-request diagnostics, and scale-out readiness.

---

## 19. Security and transactional integrity

### CSRF/session/authentication

Django's standard CSRF/session/auth mechanisms protect staff/browser workflows. Production cookies and HTTPS controls are environment-configurable.

### Secrets

Payment, R2, Glovo and integration secrets come from environment/database configuration and are not supposed to be embedded in client-side JavaScript.

### Atomic operations

Multi-row business transitions use `transaction.atomic()` where partial success would be dangerous, for example:

- plan commercial transitions;
- payment reconciliation;
- stock/production workflow updates;
- delivery assignment creation;
- measurement changes.

### Idempotency

Payment materialization and provider event handling are designed to tolerate repeated callbacks. This is crucial because payment providers and webhook senders can legitimately retry the same event.

### Historical snapshots

Price, cost, customer, delivery and production evidence is often snapshotted at the moment it becomes historical truth. Later changes to today's configuration should not retroactively rewrite yesterday's transaction.

---

## 20. Production testing without a staging environment

You can create a normal business in production to exercise real tenant flows and later remove it through the Founder Console, provided you accept that this is **live production data while it exists**.

The Founder deletion tool previews the cascade and deletes the tenant root inside a transaction. Tenant-owned records linked through cascading relations are removed with that business.

### Numbering nuance

The important operational sequences that were deliberately designed per tenant remain independent:

- Production `OrderNumberSequence` is per business;
- Commerce `CommerceOrderNumberSequence` is per business.

Creating/deleting one business therefore does not consume another tenant's production/commerce sequence.

However, some older human-visible references still display ordinary database primary keys (for example some procurement/sales/expense references). Database primary keys are global surrogate identifiers. Creating/deleting records in a temporary tenant can therefore leave harmless numeric gaps in those global IDs. It does **not** change another tenant's data, create a collision, or advance the explicit per-business sequences.

If perfectly gap-independent human-facing numbering is required for every domain, each of those remaining display identifiers would need its own tenant-scoped sequence. That is a separate schema/product decision and should not be confused with tenant isolation.

### Safer live-production test discipline

When testing without staging:

1. create an ordinary business with a clearly identifiable internal name that does not need a special “test” database flag;
2. do not reuse real customer/payment-provider credentials unless the test requires them;
3. use provider sandbox credentials where the provider supports sandboxing;
4. review the Founder deletion preview before confirming deletion;
5. export any evidence you need to keep before deleting;
6. verify payment/provider webhooks will not continue targeting deleted external references;
7. do not use production destructive tests against another tenant.

---

## 21. Deployment model

### Render + PostgreSQL/Supabase + R2

The primary cloud path uses:

- Render web service;
- Daphne/ASGI;
- PostgreSQL (commonly Supabase via `DATABASE_URL`);
- optional Cloudflare R2 for media;
- environment-driven Paystack/Monnify/Web Push/geocoder configuration;
- authenticated maintenance endpoints/scheduled jobs.

### PythonAnywhere

PythonAnywhere/SQLite remains a supported smaller deployment/failover style. Its normal HTTP setup does not provide the same WebSocket topology, so notification UI can fall back appropriately.

### Release sequence

`scripts/production.sh` centralizes:

- dependency/static/check build;
- migrations;
- scheduled jobs;
- server startup.

A production deployment should pass `manage.py check`, migrations and targeted test suites before traffic is considered healthy.

---

## 22. Why server-rendered Django instead of a separate SPA API for everything?

A common defense question is why the entire product is not React/Vue plus an API.

The answer is architectural fit, not ideology:

- the majority of INPROFIC pages are authenticated forms, tables, reports and transactional workflows;
- server rendering keeps authorization/data loading close to the business rules;
- Django forms and CSRF/session behavior reduce duplicated validation/security code;
- modest JavaScript adds interaction where it materially improves UX;
- the Headless Commerce API exists where a genuinely separate frontend is valuable;
- avoiding a full SPA reduces build/runtime complexity while the product is still evolving quickly.

A future dedicated mobile/SPA client can consume deliberately designed APIs without forcing every internal staff screen to become an API-first frontend prematurely.

---

## 23. Why not one giant “Order” status?

Another useful defense question is why checkout/payment/production/delivery each have separate statuses.

Because one order can simultaneously be:

```text
Payment: verified
Production: waiting for preparation
Delivery: assigned to rider
Customer tracking: in progress
Finance: cash receipt posted
```

Collapsing those into one value such as `processing` destroys information and creates ambiguous transitions. INPROFIC models the important lifecycles separately and links them.

---

## 24. Why preserve history instead of recalculating everything?

A business system must be able to answer “what did we believe/charge/pay/use at the time?”

If an old production batch automatically changed cost because today's flour price changed, or an old customer order changed because the customer's address was edited, reporting and audit would become unreliable.

Therefore INPROFIC often separates:

- current configuration/master data;
- historical transaction snapshots.

This is intentional denormalization for historical truth.

---

## 25. How to trace a bug methodically

A practical debugging route is:

1. identify the URL name and view;
2. identify `request.business` and effective permission path;
3. identify the primary model/queryset;
4. trace service-layer functions called by the view;
5. determine which transaction creates/updates the business truth;
6. inspect template/JavaScript only after server truth is understood;
7. verify tenant filters;
8. verify historical snapshot/idempotency expectations;
9. reproduce with Django tests;
10. inspect performance diagnostics for query/time regressions.

For checkout/payment bugs, additionally trace:

```text
quote → checkout → payment claim → verification → receipt/allocation → intake/materialization
```

For delivery bugs:

```text
origin/area geometry → location resolution → quote → checkout snapshot → assignment → event/status → tracking
```

For production bugs:

```text
recipe/input definition → planned quantity → approval/release snapshot → batch completion → cost snapshot → sale/stock destination
```

---

## 26. Migration discipline

Migrations are part of production history and should normally be append-only once deployed.

When changing a model:

- add a new migration rather than editing an already deployed migration;
- use data migrations when existing rows need a semantic transition;
- keep tenant ownership explicit;
- use `SeparateDatabaseAndState` only when database state and Django model state genuinely need different handling;
- test forward migration against realistic data;
- avoid irreversible destructive migrations unless there is a verified backup and migration plan.

The onboarding-tour migration is a useful example: it adds a version field with default `0` for future memberships, then marks memberships that already existed at rollout as current so the deployment does not interrupt all existing staff.

---

## 27. How to extend INPROFIC safely

Before implementing a feature, decide:

1. Which domain owns the new truth?
2. Is the record tenant-owned directly or through a parent?
3. Which module entitlement governs access?
4. Which role/user permission governs view/edit?
5. Does it require a new historical snapshot or can it refer to mutable master data?
6. Does it change money, stock, production or payment truth?
7. Does it need `transaction.atomic()`?
8. Is the operation idempotent if retried?
9. Does it need notification/audit evidence?
10. Will it add queries to every request, or can it be lazy/request-cached?
11. Does it need a migration?
12. What cross-tenant and regression tests prove the boundary?

This checklist is more valuable than copying a nearby view without understanding the domain invariant.

---

## 28. Useful defense questions and answers

### “How do you stop tenant A from viewing tenant B's record?”

Tenant context is resolved server-side, permissions are evaluated against `user + business`, and object querysets are filtered by that business/tenant relation. Sensitive tests verify cross-tenant IDs return denied/not-found rather than trusting the posted object id.

### “How do roles differ from subscriptions?”

The subscription controls the business's commercial ceiling. A role controls what that staff member may do inside the enabled ceiling. Both conditions must pass.

### “Why does checkout create records before the final Sale?”

Because the system must preserve what the customer accepted while waiting for authoritative payment. Only verified settlement materializes operational consequences, preventing abandoned/unpaid baskets from changing stock or finance.

### “How do you prevent duplicate orders from repeated webhooks?”

Payment/provider callbacks are treated as retryable. Receipt/payment/materialization services use unique references/status checks/transactions so the same verified event does not create operational truth twice.

### “How does Hybrid delivery work?”

Both in-house and the configured provider are real choices. INPROFIC calculates/retains quotes, applies the tenant routing policy, resolves each assignment to an actual method, snapshots the switching promise accepted by the customer, and tracks the execution through one delivery timeline.

### “Why is the delivery area a radius but pricing based on another distance?”

The area shape is a serviceability boundary. Price is based on actual dispatch-base-to-destination distance. Mixing those two concepts would make an irregular zone shape determine an artificial transport distance.

### “How can the Starter plan safely change from free to paid?”

Starter's commercial state is derived from its price. Founder Console performs the switch transactionally. Existing free subscribers receive the Founder-configured transition instead of immediate loss. Returning to free removes expiry requirements again.

### “What happens when the server is scaled?”

Database pooling and query optimization continue to help. For multiple ASGI processes/instances, Redis must become the Channels layer so WebSocket notifications cross worker boundaries. Database/service region locality still matters.

### “Can you promise no lag?”

No responsible system can promise zero latency. INPROFIC is instrumented to identify slow SQL versus non-SQL time and is structured to reduce query amplification. More compute improves resource headroom, but network/database/provider latency must also be controlled.

### “Why are some identifiers allowed to have gaps?”

Database primary keys are technical surrogate identities and can have gaps after rollbacks/deletes. Where business-readable sequence continuity matters, INPROFIC uses explicit per-business sequence models, such as production and Commerce order numbering. Gapless global primary keys are not a data-integrity requirement.

### “How is audit history protected?”

Historical snapshots are retained, sensitive corrections use explicit audit/measurement workflows, and external auditors review through a separate read-focused workspace. Auditor queries reference evidence rather than rewriting it.

---

## 29. What INPROFIC is not claiming yet

A technically credible defense includes boundaries.

INPROFIC has a strong connected operations foundation, but it should not be described as an exhaustive Tier-1 manufacturing ERP. Areas that can grow further include advanced MRP, finite-capacity production scheduling, detailed labour/overhead costing, deeper formal QC quarantine/release, advanced warehouse routing and broader courier/provider adapters.

Stating those boundaries makes the implemented architecture more credible, not less.

---

## 30. Final mental model

When studying INPROFIC, remember five layers:

### Layer A — Tenant and access

```text
Business → Subscription/module ceiling → UserBusiness membership → Role/user permission
```

### Layer B — Supply and stock

```text
Supplier/PO → Receive → Material/stock/cost evidence
```

### Layer C — Transformation and sale

```text
Recipe/input → Production order/run → Batch → Finished stock/Sale
```

### Layer D — Customer commerce and delivery

```text
Catalogue → Quote → Checkout → Verified payment → Order → Delivery assignment/tracking
```

### Layer E — Money, audit and reporting

```text
Operational event → Payment/ledger movement → Financial/report/audit evidence
```

The system is coherent when each new feature has a clear place in those layers and does not silently skip the boundaries between them.

That is the core architectural defense of INPROFIC.
